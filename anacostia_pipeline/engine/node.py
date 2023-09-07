from __future__ import annotations

from threading import Thread, Lock, Event, RLock, get_native_id
from queue import Queue
from typing import List, Dict, Optional, Set, Union
from functools import reduce, wraps
import time
from logging import Logger
from datetime import datetime

from pydantic import BaseModel

if __name__ == "__main__":
    from constants import Status, ASTOperation
else:
    from engine.constants import Status, ASTOperation


class SignalAST:
    '''
    This Class Represents the boolean expression of signals
    required by a node class to trigger
    '''
    def __init__(self, operation:ASTOperation, parameters:List[Union[BaseNode, SignalAST]]):
        self.operation = operation
        self.parameters = parameters
    
    def evaluate(self, node:BaseNode) -> bool:
        '''
        Evaluate the AST based on the existance of signal and success (if it does exist) for the given node's received signals
        '''
        evaluated_params = list()
        for param in self.parameters:
            if isinstance(param, SignalAST):
                evaluated_params.append(param.evaluate(node))
            else:
                value = (param.name in node.received_signals) and (node.received_signals[param.name].status == Status.SUCCESS)
                evaluated_params.append(value)

        if self.operation == ASTOperation.NOT:
            assert len(evaluated_params) == 1
            return not evaluated_params[0]
        elif self.operation == ASTOperation.AND:
            return all(evaluated_params)
        elif self.operation == ASTOperation.OR:
            return any(evaluated_params)
        elif self.operation == ASTOperation.XOR:
            return reduce(lambda x, y: x^x, evaluated_params)
        else:
            raise ValueError(f"Invalid Operation: {self.operation}")

    def nodes(self) -> Set[BaseNode]:
        '''
        Retset a set of nodes that are in this AST and subtrees
        '''
        return set(self._nodes())

    def _nodes(self):
        '''
        Recursive helper for `nodes()` to return all nodes included in the AST
        '''
        for param in self.parameters:
            if isinstance(param, SignalAST):
                for n in param._nodes():
                    yield n
            else:
                yield param

def Not(n: Union[SignalAST, BaseNode]):
    return SignalAST(
        operation = ASTOperation.NOT,
        parameters = [n]
    )

def And(*args: Union[SignalAST, BaseNode]):
    return SignalAST(
        operation = ASTOperation.AND,
        parameters = args
    )

def Or(*args: Union[SignalAST, BaseNode]):
    return SignalAST(
        operation = ASTOperation.OR,
        parameters = args
    )

def XOr(*args: Union[SignalAST, BaseNode]):
    return SignalAST(
        operation = ASTOperation.XOR,
        parameters = args
    )

class Message(BaseModel):
    # TODO allow dynamic key-values
    # https://docs.pydantic.dev/latest/usage/models/#dynamic-model-creation
    sender: str
    signal_type:str
    timestamp: datetime
    status: Optional[Status] = None

class BaseNode(Thread):
    def __init__(self, 
        name: str, 
        signal_type: str = "DEFAULT_SIGNAL",
        listen_to: Union[Union[BaseNode, SignalAST], List[Union[BaseNode, SignalAST]]] = list(),
        auto_trigger: bool = True,
    ) -> None:
        '''
        :param name: a name given to the node
        :param signal_type: ???
        :param listen_to: The list of nodes or boolean expression of nodes (SignalAST) that this node requires signals of to trigger. Items in the list will be boolean AND'd together
        :param auto_trigger: If False, then the node requires another object to trigger 
        '''

        super().__init__()
        self.name = name
        self.signal_type = signal_type # TODO what are the different signal types. Does a node need to track this?
        self.auto_trigger = auto_trigger
        if self.auto_trigger:
            self.triggered = True
        else:
            self.triggered = False
        
        # use self.status(). Property is Thread Safe 
        self._status_lock = Lock()
        self._status = Status.OFF

        self.dependent_nodes = set()
        
        if not isinstance(listen_to, list):
            listen_to = [listen_to]
        self.signal_ast = And(*listen_to)
        for item in listen_to:
            if isinstance(item, SignalAST):
                self.dependent_nodes |= item.nodes()
            else:
                self.dependent_nodes |= {item}

        # Nodes to signal
        self.successors = list()

        # set successors for each dependent node with self
        for node in self.dependent_nodes:
            node.successors.append(self)
            node.num_successors += 1

        # Queue of incoming signals from the dependent_nodes
        self.incoming_signals = Queue()

        # Store for signals after processing them (and in the future after acknowledging them too maybe?)
        # Only keeps the most recent signal received
        self.received_signals: Dict[str, Message] = dict()
        
        self.logger = None

        self.num_successors = 0
        self.num_predecessors = 0

        # we don't need to use a lock to write to self.waiting because we are only writing to it in the thread that runs this node
        self.waiting = False
    
    @staticmethod
    def pausable(func):
        '''
        A Decorator for allowing execution in the Status.RUNNING state to be paused mid execution
        '''
        def wrapper(self, *args, **kwargs):
            while self.status == Status.PAUSED:
                time.sleep(0.1)

            ret = func(self, *args, **kwargs)
            return ret
        return wrapper

    def __hash__(self) -> int:
        return hash(self.name)

    def __repr__(self) -> str:
        return f"'Node(name: {self.name}, status: {str(self.status)})'"
    
    def __and__(self, other) -> SignalAST:
        '''Overwrites the & bitwise operator'''
        return And(self, other)

    def __or__(self, other) -> SignalAST:
        '''Overwrites the | bitwise operator'''
        return Or(self, other)

    def __xor__(self, other) -> SignalAST:
        '''Overwrites the ^ bitwise operator'''
        return XOr(self, other)

    def __invert__(self) -> SignalAST:
        '''Overwrites the ~ bitwise operator'''
        return Not(self)

    def set_logger(self, logger: Logger) -> None:
        self.logger = logger

    def log(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)
        else:
            print(message)

    def setup(self) -> None:
        # override to specify actions needed to create node.
        # such actions can include pulling and setting up docker containers, 
        # creating python virtual environments, creating database connections, etc.
        # note that the setup() method will be ran in a separate thread; 
        # this is the main difference between setting up the node using setup() and __init__()
        # therefore, it is best to put set up logic here that is not dependent on other nodes.
        pass

    @pausable
    def pre_trigger(self) -> bool:
        # should be used for continuously checking if the node is ready to start
        # i.e., checking if database connections, API connections, etc. are ready 
        return True

    @pausable
    def check_signals(self) -> bool:
        '''
        Verify all received signal statuses match the condition for this node to execute
        '''

        if len(self.dependent_nodes) > 0:
            # if there are dependent nodes, then we need to check if we have received signals from them
            # if we have not received signals from them, then we need to wait and try again
            if self.incoming_signals.empty():
                #print(f"{self.name} incoming signals queue is empty")
                return False

            # Pull out the queued up incoming signals and register them
            while not self.incoming_signals.empty():
                sig = self.incoming_signals.get()
                self.received_signals[sig.sender] = sig
                # TODO For signaling over the network, this is where we'd send back an ACK

            # Check if the signals match the execute condition
            signals_eval = self.signal_ast.evaluate(self)
            return signals_eval
        
        # If there are no dependent nodes, then we can just return True
        return True

    @pausable
    def send_signals(self, status:Status):
        msg = Message(
            sender = self.name,
            signal_type = self.signal_type,
            timestamp = datetime.now(),
            status = status
        )

        for n in self.successors:
            n.incoming_signals.put(msg)

    @pausable
    def pre_execution(self) -> None:
        # override to enable node to do something before execution; 
        # e.g., send an email to the data science team to let everyone know the pipeline is about to train a new model
        pass

    @pausable
    def execute(self, *args, **kwargs) -> bool:
        # the logic for a particular stage in the MLOps pipeline
        return True

    @pausable
    def post_execution(self) -> None:
        pass
    
    @pausable
    def on_success(self) -> None:
        # override to enable node to do something after execution in event of success of action_function; 
        # e.g., send an email to the data science team to let everyone know the pipeline has finished training a new model
        pass

    @pausable
    def on_failure(self, e: Exception = None) -> None:
        # override to enable node to do something after execution in event of failure of action_function; 
        # e.g., send an email to the data science team to let everyone know the pipeline has failed to train a new model
        pass
    
    def teardown(self) -> None:
        # override to specify actions to be executed upon removal of node from dag or on pipeline shutdown
        pass

    def trigger(self) -> None:
        self.triggered = True

    def reset_trigger(self):
        # TODO reset trigger dependent on the state of the system i.e. data store, feature store, model store
        if self.auto_trigger == False:
            self.triggered = False

    @property
    def status(self):
        while True:
            with self._status_lock:
                return self._status

    @status.setter
    def status(self, value: Status):
        while True:
            with self._status_lock:
                self._status = value
                break

    def pause(self):
        self.status = Status.PAUSED

    def resume(self):
        self.status = Status.RUNNING

    def stop(self):
        self.status = Status.STOPPING

    def force_stop(self):
        # TODO
        pass

    def on_exit(self):
        """
        on_exit is called when the node is being stopped.
        implement this method to do things like release locks, 
        release resources, anouncing to other nodes that this node has stopped, etc.
        """
        pass

    def run(self) -> None:
        self.status = Status.INIT
        try:
            self.setup()
        except Exception as e:
            print(f"{str(self)} setup failed: {e}")
            self.status = Status.ERROR
            return

        self.status = Status.RUNNING

        while True:
            if self.status == Status.RUNNING:               

                # If pre-check fails, then just wait and try again
                # consider checking status before executing each function called in this loop
                # consider making pre_trigger() a pauseable function
                if self.pre_trigger() is False:
                    self.waiting = True
                    continue
                self.waiting = False

                if self.triggered:
                    # If not all signals received / boolean statement of signals is false, wait and try again
                    # consider making check_signals() a pauseable function
                    if self.check_signals() is False:
                        self.waiting = True
                        continue
                    self.waiting = False

                    # Precheck is good and the signals we want are good
                    self.pre_execution()
                    
                    # Run the action function
                    try:
                        ret = self.execute()
                        if ret:
                            self.on_success()
                            self.post_execution()
                            self.send_signals(Status.SUCCESS)
                        else:
                            self.on_failure()
                            self.post_execution()
                            self.send_signals(Status.FAILURE)
                    except Exception as e:
                        self.on_failure(e)
                        self.post_execution()
                        self.send_signals(Status.FAILURE)

                    self.reset_trigger()    
            
            elif self.status == Status.PAUSED:
                # Stay Indefinitely Paused until external action
                time.sleep(0.1)

            elif self.status == Status.STOPPING:
                self.on_exit()
                self.status = Status.EXITED

            if self.status == Status.EXITED:
                break

            time.sleep(0.1)

class TrueNode(BaseNode):
    '''A Node that does nothing and always returns a success'''
    def __init__(
        self, 
        name: str, 
        listen_to: BaseNode | SignalAST | List[BaseNode | SignalAST] = list(), 
    ) -> None:
        super().__init__(
            name=name, 
            signal_type="DEFAULT_SIGNAL", 
            listen_to=listen_to, 
            auto_trigger=True
        )
    
    def execute(self):
        return True

    def setup(self):
        time.sleep(2)

class FalseNode(BaseNode):
    '''A Node that does nothing and always returns a failure'''
    def __init__(
        self, 
        name: str, 
        listen_to: BaseNode | SignalAST | List[BaseNode | SignalAST] = list(), 
    ) -> None:
        super().__init__(
            name=name, 
            signal_type="DEFAULT_SIGNAL", 
            listen_to=listen_to, 
            auto_trigger=False
        )
    
    def execute(self):
        return False

    def setup(self):
        time.sleep(1)


class ActionNode(BaseNode):
    def __init__(self, name: str, signal_type: str, listen_to: List[BaseNode] = []) -> None:
        super().__init__(name, signal_type, listen_to, auto_trigger=True)


class ResourceNode(BaseNode):
    def __init__(self, name: str, signal_type: str) -> None:
        super().__init__(name, signal_type, auto_trigger=False)
        self.resource_lock = RLock()
        self.event = Event()
        self.reference_lock = RLock()
        self.reference_count = 0

    def await_references(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            self.event.wait()
            
            result = func(self, *args, **kwargs)
            
            if self.event.is_set():
                self.event.clear()
            
            return result
        return wrapper
    
    def exeternally_accessible(func):
        # best practice: use the ref_count_decorator on all functions that are accessible from outside the class
        # note: there could be a situation where one function acquires the reference lock and another function acquires the resource lock
        # but both functions need to acquire both the reference lock and the resource lock;
        # in this case, both are waiting for the other lock to be released, resulting in a deadlock
        # however, this situation is unlikely to occur in practice (if at all) because thus far, 
        # all functions that need to acquire both locks are decorated with the ref_count_decorator and THEN the lock_decorator.
        # thus, all functions that need to acquire both locks must acquire the reference lock first, preventing a deadlock.
        # i am not sure if there will ever be a situation where a function needs to acquire the resource lock first and then the reference lock;
        # but if there is such a situation, then the user will have to adjust their code to allow for the reference lock to be acquired first.

        # possible race condition: if the same thread acquires the reference lock, increments the reference count, 
        # and then releases the reference lock but reacquires the reference lock before other threads 
        # (that also call externally accessible methods) have the chance to acquire the lock.
        # in this case, the event will be set prematurely and the other threads will not be able to access the same state as the thread 
        # that originally acquired the reference lock and set the event.
        # possible fix for this race condition: implement a check to see if all successor nodes have acquired the reference lock.
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            while True:
                with self.reference_lock:
                    self.reference_count += 1
                    break

            result = func(self, *args, **kwargs)

            while True:
                with self.reference_lock:
                    self.reference_count -= 1
                    if self.reference_count == 0:
                        self.event.set()
                    break

            return result
        return wrapper
    
    def resource_accessor(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            # make sure setup is finished before allowing other nodes to access the resource
            if func.__name__ == "setup":
                # keep trying to acquire lock until function is finished
                # generally, it is best practice to use lock inside of a while loop to avoid race conditions (recall GMU CS 571)
                while True:
                    with self.resource_lock:
                        return func(self, *args, **kwargs)
            else:
                while self.status == Status.INIT:
                    time.sleep(0.1)

                while True:
                    with self.resource_lock:
                        return func(self, *args, **kwargs)
        return wrapper

    def log(self, message: str) -> None:
        # adding a delay to make sure the node has time to access the logger
        time.sleep(0.1)
        return super().log(message)
