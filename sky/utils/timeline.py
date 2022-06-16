"""This module helps generating timelines of an application.

The timeline follows the trace event format defined here:
https://docs.google.com/document/d/1CvAClvFfyA5R-PhYUmn5OOQtYMH4h6I0nSsKchNAySU/preview
"""  # pylint: disable=line-too-long
import functools
from typing import Optional, Union, Callable

import atexit
import json
import os
import threading
import time
import inspect

import filelock

_events = []


class Event:
    """Record an event.

    Args:
        name: The name of the event.
        message: The message attached to the event.
    """

    def __init__(self, name: str, message: str = None):
        self._name = name
        self._message = message
        # See the module doc for the event format.
        self._event = {
            'name': self._name,
            'cat': 'event',
            'pid': str(os.getpid()),
            'tid': str(threading.current_thread().ident),
            'args': {
                'message': self._message
            }
        }
        if self._message is not None:
            self._event['args'] = {'message': self._message}

    def begin(self):
        event_begin = self._event.copy()
        event_begin.update({
            'ph': 'B',
            'ts': f'{time.time() * 10 ** 6: .3f}',
        })
        if self._message is not None:
            event_begin['args'] = {'message': self._message}
        global _events
        _events.append(event_begin)

    def end(self):
        event_end = self._event.copy()
        event_end.update({
            'ph': 'E',
            'ts': f'{time.time() * 10 ** 6: .3f}',
        })
        if self._message is not None:
            event_end['args'] = {'message': self._message}
        global _events
        _events.append(event_end)

    def __enter__(self):
        self.begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end()


class FileLockEvent:
    """Serve both as a file lock and event for the lock."""

    def __init__(self, lockfile: Union[str, os.PathLike]):
        self._lockfile = lockfile
        # TODO(mraheja): remove pylint disabling when filelock version updated
        # pylint: disable=abstract-class-instantiated
        self._lock = filelock.FileLock(self._lockfile)
        self._hold_lock_event = Event(f'[FileLock.hold]:{self._lockfile}')

    def acquire(self):
        was_locked = self._lock.is_locked
        with Event(f'[FileLock.acquire]:{self._lockfile}'):
            self._lock.acquire()
        if not was_locked and self._lock.is_locked:
            # start holding the lock after initial acquiring
            self._hold_lock_event.begin()

    def release(self):
        was_locked = self._lock.is_locked
        self._lock.release()
        if was_locked and not self._lock.is_locked:
            # stop holding the lock after initial releasing
            self._hold_lock_event.end()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def __call__(self, f):
        # Make this class callable as a decorator.
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            with self:
                return f(*args, **kwargs)

        return wrapper


def event(name_or_fn: Union[str, Callable], message: Optional[str] = None):
    """A decorator for logging events when applied to functions.

    Args:
        name_or_fn: The name of the event or the function to be wrapped.
        message: The message attached to the event.
    """
    if isinstance(name_or_fn, str):

        def _wrapper(f):

            def _record(*args, **kwargs):
                nonlocal name_or_fn
                with Event(name=name_or_fn, message=message):
                    return f(*args, **kwargs)

            return _record

        return _wrapper
    else:
        if not inspect.isfunction(name_or_fn):
            raise ValueError(
                'Should directly apply the decorator to a function.')

        def _record(*args, **kwargs):
            nonlocal name_or_fn
            f = name_or_fn
            func_name = getattr(f, '__qualname__', f.__name__)
            module_name = getattr(f, '__module__', '')
            if module_name:
                full_name = f'{module_name}.{func_name}'
            else:
                full_name = func_name
            with Event(name=full_name, message=message):
                return f(*args, **kwargs)

        return _record


def _save_timeline(file_path: str):
    json_output = {
        'traceEvents': _events,
        'displayTimeUnit': 'ms',
        'otherData': {
            'log_dir': os.path.dirname(file_path),
        }
    }
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w') as f:
        json.dump(json_output, f)


if os.environ.get('SKY_TIMELINE_FILE_PATH'):
    atexit.register(_save_timeline, os.environ['SKY_TIMELINE_FILE_PATH'])