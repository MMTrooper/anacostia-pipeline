from typing import List, Iterable
import time
import sys
import os
from logging import Logger
import networkx as nx

sys.path.append(os.path.abspath('..'))
sys.path.append(os.path.abspath('../anacostia_pipeline'))
if __name__ == "__main__":
    from base import BaseActionNode, BaseResourceNode, BaseNode
    from constants import Status
else:
    from engine.base import BaseActionNode, BaseResourceNode, BaseNode
    from engine.constants import Status



class InvalidNodeDependencyError(Exception):
    pass


class Pipeline:
    """
    Pipeline is a class that is in charge of graph management and execution; this includes:
    1. Providing an API to interact with the nodes.
        - Pipeline class will be used to create a CLI and a browser GUI.
        - CLI commands include help, version, start, shutdown, pause, resume, and check_status. More commands will be added later.
        - Browser GUI will be added later.
    2. Saving graph as graph.json file and loading a graph.json file back into the pipeline to recreate the DAG. 
    2. Ensuring the user built the graph correctly (i.e., ensuring the graph is a DAG)
    """

    def __init__(self, nodes: Iterable[BaseNode], logger: Logger = None) -> None:
        self.graph = nx.DiGraph()

        # Add nodes into graph
        for node in nodes:
            self.graph.add_node(node)

        # Add edges into graph
        for node in nodes:
            for predecessor in node.predecessors:
                self.graph.add_edge(predecessor, node)
        
        # set successors for all nodes
        for node in nodes:
            node.successors = list(self.graph.successors(node))
        
        # Set logger for all nodes
        if logger is not None:
            for node in nodes:
                node.set_logger(logger)

        # check if graph is acyclic (i.e., check if graph is a DAG)
        if not nx.is_directed_acyclic_graph(self.graph):
            raise InvalidNodeDependencyError("Node Dependencies do not form a Directed Acyclic Graph")

        self.nodes: List[BaseNode] = list(nx.topological_sort(self.graph))
        
    def launch_nodes(self):
        """
        Lanches all the registered nodes in topological order.
        """
        for node in self.nodes:
            # Note: since node is a subclass of Thread, calling start() will run the run() method
            node.start()

    def terminate_nodes(self) -> None:
        # terminating nodes need to be done in reverse order so that the successor nodes are terminated before the predecessor nodes
        # this is because the successor nodes will continue to listen for signals from the predecessor nodes,
        # and if the predecessor nodes are terminated first, then the sucessor nodes will never receive the signals,
        # thus, the successor nodes will never be terminated.
        # predecessor nodes need to wait for the successor nodes to terminate before they can terminate. 

        print("Terminating nodes")
        for node in reversed(self.nodes):
            node.exit()
            node.join()
        print("All nodes terminated")