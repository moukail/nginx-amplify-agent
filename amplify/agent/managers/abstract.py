# -*- coding: utf-8 -*-
import abc
import time

from threading import current_thread

from amplify.agent import Singleton
from amplify.agent.common.context import context


__author__ = "Grant Hulegaard"
__copyright__ = "Copyright (C) Nginx, Inc. All rights reserved."
__credits__ = ["Mike Belov", "Andrei Belov", "Ivan Poluyanov", "Oleg Mamontov", "Andrew Alexeev", "Grant Hulegaard"]
__license__ = ""
__maintainer__ = "Grant Hulegaard"
__email__ = "grant.hulegaard@nginx.com"


class AbstractManager(Singleton):
    """
    A manager is an encapsulated body that is spawned by supervisor.  Every manager, regardless of encapsulated purpose
    should have a run action that will be run in a while loop in .start().
    """
    name = 'abstract_manager'

    def __init__(self, **kwargs):
        self.running = False
        self.interval = kwargs.get('interval') or 5.0  # Run interval for manager

    @property
    def status(self):
        return 'running' if self.running else 'stopped'

    @abc.abstractmethod
    def _run(self):
        # Example since this is an abstract method.
        try:
            pass  # Do something here...
        except:
            context.default_log.error('failed', exc_info=True)
            raise

    @staticmethod
    def _wait(seconds):
        time.sleep(seconds)  # Releases the GIL.
        # TODO: Investigate more efficient methods for releasing the GIL.  Probably use more functionality from gevent.

    def start(self):
        """
        Primary execution loop.  Follows the pattern: wait, increment action id, call manager run method.
        """
        # TODO: Standardize this with collectors.
        current_thread().name = self.name
        context.setup_thread_id()

        self.running = True

        while self.running:
            self._wait(self.interval)
            context.inc_action_id()
            self._run()

    def stop(self):
        # TODO: Think about whether or not this is necessary.  Managers should probably be receiving thread.kill().
        self.running = False


class ObjectManager(AbstractManager):
    """
    Common Object manager.  Object managers manage objects of a specific type.  There should a be a different object
    manager for each type ('system' and 'nginx' for now).

    Object managers should have a run action that follows the following run pattern: discover, start objects, schedule
    cloud commands.
    """
    name = 'object_manager'
    type = 'common'
    types = ('common',)

    def __init__(self, object_configs=None, **kwargs):
        super(ObjectManager, self).__init__(**kwargs)
        self.config = context.app_config['containers'].get(self.type) or {}
        self.config_intervals = self.config.get('poll_intervals') or {}
        self.object_configs = object_configs if object_configs else {}
        self.objects = context.objects  # Object tank
        self.last_discover = 0

    @abc.abstractmethod
    def _discover_objects(self):
        """
        Abstract discovering method.  Should be overridden.
        """
        pass

    # Step 1: Discover
    def _discover(self):
        """
        Wrapper for _discover_objects - runs discovering with period
        """
        if time.time() > self.last_discover + (self.config_intervals.get('discover') or self.interval):
            self._discover_objects()
        context.log.debug('%s objects: %s' % (
            self.type,
            [obj.definition_hash for obj in self.objects.find_all(types=self.types)]
        ))

    # Step 2: Start objects
    def _start_objects(self):
        """
        Starts all objects.
        """
        for managed_obj in self.objects.find_all(types=self.types):
            managed_obj.start()
            for child_obj in self.objects.find_all(obj_id=managed_obj.id, children=True, include_self=False):
                child_obj.start()

    # Step 3: Schedule cloud commands
    def _schedule_cloud_commands(self):
        """
        Reads global cloud command queue and applies commands to specific objects.  Optionally overridden.
        """
        pass

    def _run(self):
        try:
            self._discover()
            self._start_objects()
            self._schedule_cloud_commands()
        except:
            context.default_log.error('run failed', exc_info=True)

    def run(self):
        """
        Unprotected wrapper for _run.  Ideally, ObjectManagers would be run as coroutines with gevent, but given some
        problems this is a work around where run method is called explicitly in an main loop from supervisor.
        """
        self._run()

    def stop(self):
        super(ObjectManager, self).stop()
        self._stop_objects()

    def _stop_objects(self):
        for managed_obj in self.objects.find_all(types=self.types):
            for child_obj in self.objects.find_all(obj_id=managed_obj.id, children=True, include_self=False):
                child_obj.stop()
                self.objects.unregister(obj=child_obj)
            managed_obj.stop()
            self.objects.unregister(obj=managed_obj)
