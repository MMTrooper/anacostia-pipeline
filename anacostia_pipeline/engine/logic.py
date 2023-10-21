from typing import List
import time
from logging import Logger

if __name__ == "__main__":
    from base import BaseActionNode, BaseNode
    from constants import Result
else:
    from engine.base import BaseActionNode, BaseNode
    from engine.constants import Result



class AndAndNode(BaseNode):
    """
    AndAndNode does the following: 
        1. waits for all predecessors before signalling all successors.
        2. waits for all successors before signalling all predecessors.
    It is useful for synchronizing ActionNodes to wait for all predecessors to finish before executing.
    It is also useful for synchronizing ActionNodes with ResourceNodes 
    to ensure all ActionNodes finish executing before ResourceNodes update their respective state.
    """
    def __init__(self, name: str, predecessors: List[BaseNode], logger: Logger = None) -> None:
        super().__init__(name, predecessors, logger)

    def run(self):
        while True:
            while self.check_predecessors_signals() is False:
                time.sleep(0.2)

            self.log("all resource nodes have finished updating its state.")
            self.signal_successors(Result.SUCCESS)

            # checking for successors signals before signalling predecessors will 
            # ensure all action nodes have finished using the current state
            while self.check_successors_signals() is False:
                time.sleep(0.2)
            
            self.log("all action nodes have finished using the current state.")
            self.signal_predecessors(Result.SUCCESS)


class AndOrNode(BaseActionNode):
    pass


class OrAndNode(BaseActionNode):
    pass


class OrOrNode(BaseActionNode):
    pass