#!/usr/bin/env python3

import os
import asyncio
from itertools import chain
from concurrent.futures import ThreadPoolExecutor

from taskpersistence import TaskPersistence
from copyrunner import RsyncRunner, NullRunner

def get_dirs(current_dir):
    subdirectories = [os.path.join(current_dir, name) for name in os.listdir(current_dir) if os.path.isdir(os.path.join(current_dir, name))]
    if len(subdirectories) == 0:
        return [os.path.dirname(current_dir)]

    dirs = [get_dirs(child_dir) for child_dir in subdirectories]
    return chain.from_iterable(dirs)

class CopyManager:
    def __init__(self, args):
        self.source = args['source']
        self.destination = args['destination']
        self.input_file = args['source_input']
        self.thread_count = args['thread_count']
        self.discover = True
        self.force = False
        self.__runner = RsyncRunner()

    def __enter__(self):
        self.__loop = asyncio.new_event_loop()
        return self
        
    def __exit__(self, exc_type, exc_value, traceback):
        self.__loop.close()

    # public
    def process_transfer(self):
        results = self.__loop.run_until_complete(self.__process_transfer())
        return results

    # private
    async def __process_transfer(self):
        dirs = set(get_dirs(self.source)) if self.discover else []
        with TaskPersistence(self.__loop) as store:
            store.add_tasks([(self.source, path, self.destination) for path in dirs])
            tasks = [asyncio.ensure_future(self.__worker('worker-{0}'.format(i), store), loop=self.__loop) for i in range(self.thread_count)]
            await asyncio.gather(*tasks, loop=self.__loop)
            
            store.print_stats()
            return store.get_remaining_tasks()

    async def __worker(self, name, store):
        worker_list = 10
        with ThreadPoolExecutor(worker_list) as executor:
            print('Started worker [{0}]'.format(name))
            task_list = await store.get_unclaimed_tasks(worker_list)
            while len(task_list) > 0:
                tasks = [asyncio.ensure_future(self.__loop.run_in_executor(executor, self.__construct_task_processor(store, task))) for task in task_list]
                await asyncio.gather(*tasks, loop=self.__loop)
                task_list = await store.get_unclaimed_tasks(worker_list)

    def __construct_task_processor(self, store, task):
        task_id, event_id, root_path, source_path, destination_path, _, _ = task
        def process_task():
            print('Processing [{0}] [{1}] {2}'.format(task_id, event_id, source_path))
            status, _ = self.__runner.process_dir(root_path, source_path, destination_path)
            store.update_task([(task_id, status)])
            print('Processed [{0}] [{1}] {2} with result {3}'.format(task_id, event_id, source_path, status))
        
        return process_task