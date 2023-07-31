import time
import sys
import os
from logging import Logger
sys.path.append("../../anacostia_pipeline")

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from engine.node import ResourceNode


def get_file_states(directory: str):
    file_states = {}
    for root, _, files in os.walk(directory):
        for filename in files:
            filepath = os.path.join(root, filename)
            file_states[filepath] = os.path.getmtime(filepath)
    return file_states


def get_new_files(directory: str, prev_states: dict):
    current_states = get_file_states(directory)
    added_files = [filepath for filepath in current_states if filepath not in prev_states]
    return added_files


def get_modified_files(directory: str, prev_states: dict):
    current_states = get_file_states(directory)
    modified_files = [filepath for filepath in current_states if prev_states.get(filepath) != current_states.get(filepath)]
    return modified_files


def get_removed_files(directory: str, prev_states: dict):
    current_states = get_file_states(directory)
    removed_files = [filepath for filepath in prev_states if filepath not in current_states]
    return removed_files


class DirWatchNode(ResourceNode, FileSystemEventHandler):
    def __init__(self, name: str, path: str, logger: Logger=None):
        self.path = path
        super().__init__(name, logger)
        self.observer = Observer()

        self.directory_state = get_file_states(self.path)
    
    def on_modified(self, event):
        if event.is_directory:
            if self.logger is not None:
                self.logger.info(f"Detected change: {event.event_type} {event.src_path}")
            else:
                print(f"Detected change: {event.event_type} {event.src_path}")
            self.trigger()
    
    def setup(self) -> None:
        if self.logger is not None:
            self.logger.info(f"Setting up node '{self.name}'")
        else:
            print(f"Setting up node '{self.name}'")
        
        self.observer.schedule(event_handler=self, path=self.path, recursive=True)
        self.observer.start()

        if self.logger is not None:
            self.logger.info(f"Node '{self.name}' setup complete. Observer started, waiting for file change...")
        else:
            print(f"Node '{self.name}' setup complete. Observer started, waiting for file change...")
    
    def teardown(self) -> None:
        self.observer.stop()
        self.observer.join()
        if self.logger is not None:
            self.logger.info(f"Node '{self.name}' teardown complete.")
        else:
            print(f"Node '{self.name}' teardown complete.")


if __name__ == "__main__":
    folder1_node = DirWatchNode("folder1", "/Users/minhquando/Desktop/anacostia/anacostia_pipeline/resource/folder1")
    folder1_node.start()

    time.sleep(20)