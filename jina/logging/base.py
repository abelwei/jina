import json
import logging
import os
import re
import sys
from copy import copy
from logging import Formatter
from logging.handlers import QueueHandler
from typing import Union

from .profile import used_memory
from ..enums import LogVerbosity
from ..helper import colored


class ColorFormatter(Formatter):
    """Format the log into colored logs based on the log-level. """

    MAPPING = {
        'DEBUG': dict(color='white', on_color=None),  # white
        'INFO': dict(color='white', on_color=None),  # cyan
        'WARNING': dict(color='yellow', on_color='on_grey'),  # yellow
        'ERROR': dict(color='white', on_color='on_red'),  # 31 for red
        'CRITICAL': dict(color='white', on_color='on_green'),  # white on red bg
    }  #: log-level to color mapping

    def format(self, record):
        cr = copy(record)
        seq = self.MAPPING.get(cr.levelname, self.MAPPING['INFO'])  # default white
        cr.msg = colored(cr.msg, **seq)
        return super().format(cr)


class PlainFormatter(Formatter):
    """Remove all control chars from the log and format it as plain text """

    def format(self, record):
        cr = copy(record)
        if isinstance(cr.msg, str):
            cr.msg = re.sub(u'\u001b\[.*?[@-~]', '', str(cr.msg))
        return super().format(cr)


class JsonFormatter(Formatter):
    """Format the log message as a JSON object so that it can be later used/parsed in browser with javascript. """

    KEYS = {'created', 'filename', 'funcName', 'levelname', 'lineno', 'msg',
            'module', 'name', 'pathname', 'process', 'thread'}  #: keys to extract from the log

    def format(self, record):
        cr = copy(record)
        cr.msg = re.sub(u'\u001b\[.*?[@-~]', '', str(cr.msg))
        return json.dumps(
            {k: getattr(cr, k) for k in self.KEYS},
            sort_keys=True)


class ProfileFormatter(Formatter):
    """Format the log message as JSON object and add the current used memory into it"""

    def format(self, record):
        cr = copy(record)
        if isinstance(cr.msg, dict):
            cr.msg.update({k: getattr(cr, k) for k in ['created', 'module', 'name', 'pathname', 'process', 'thread']})
            cr.msg['memory'] = used_memory(unit=1)
        else:
            raise TypeError('profile logger only accepts dict')

        return json.dumps(cr.msg, sort_keys=True)


class EventHandler(logging.StreamHandler):
    """
    A cross-thread/process logger that allows fetching via iterator

    .. warning::

        Some logs may be missing, no clear reason why.
    """

    def __init__(self, event):
        super().__init__()
        self._event = event

    def emit(self, record):
        if record.levelno >= self.level:
            self._event.record = self.format(record)
            self._event.set()


class NTLogger:
    def __init__(self, context: str, log_level: 'LogVerbosity'):
        """A compatible logger for Windows system, colors are all removed to keep compat.

        :param context: the name prefix of each log
        :param verbose: show debug level info
        """
        self.context = self._planify(context)
        self.log_level = log_level

    @staticmethod
    def _planify(msg):
        return re.sub(u'\u001b\[.*?[@-~]', '', msg)

    def info(self, msg: str, **kwargs):
        """log info-level message"""
        if self.log_level <= LogVerbosity.INFO:
            sys.stdout.write('I:%s:%s' % (self.context, self._planify(msg)))

    def critical(self, msg: str, **kwargs):
        """log info-level message"""
        if self.log_level <= LogVerbosity.CRITICAL:
            sys.stdout.write('C:%s:%s' % (self.context, self._planify(msg)))

    def debug(self, msg: str, **kwargs):
        """log debug-level message"""
        if self.log_level <= LogVerbosity.DEBUG:
            sys.stdout.write('D:%s:%s' % (self.context, self._planify(msg)))

    def error(self, msg: str, **kwargs):
        """log error-level message"""
        if self.log_level <= LogVerbosity.ERROR:
            sys.stdout.write('E:%s:%s' % (self.context, self._planify(msg)))

    def warning(self, msg: str, **kwargs):
        """log warn-level message"""
        if self.log_level <= LogVerbosity.WARNING:
            sys.stdout.write('W:%s:%s' % (self.context, self._planify(msg)))


def get_logger(context: str, context_len: int = 10, profiling: bool = False, sse: bool = False, fmt_str: str = None,
               log_event=None,
               **kwargs) -> Union['logging.Logger', 'NTLogger']:
    """Get a logger with configurations

    :param context: the name prefix of the log
    :param context_len: length of the context, i.e. module, function, line number
    :param profiling: is this logger for profiling
    :param sse: is this logger used for server-side event
    :return: the configured logger

    .. note::
        One can change the verbosity of jina logger via the environment variable ``JINA_VERBOSITY``

    """
    from .. import __uptime__
    from .queue import __log_queue__, __profile_queue__
    if not fmt_str:
        fmt_str = f'{context[:context_len]:>{context_len}}@%(process)2d' \
                  f'[%(levelname).1s][%(filename).3s:%(funcName).3s:%(lineno)3d]:%(message)s'

    timed_fmt_str = f'%(asctime)s:' + fmt_str

    verbose_level = LogVerbosity.from_string(os.environ.get('JINA_VERBOSITY', 'INFO'))

    if os.name == 'nt':  # for Windows
        return NTLogger(context, verbose_level)

    # Remove all handlers associated with the root logger object.
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logger = logging.getLogger(context)
    logger.propagate = False

    if not logger.handlers:
        logger.setLevel(verbose_level.value)

        logger.handlers = []
        if log_event is not None:
            event_handler = EventHandler(log_event)
            event_handler.setLevel(verbose_level.value)
            event_handler.setFormatter(ColorFormatter(fmt_str))
            logger.addHandler(event_handler)

        if profiling:
            file_handler = logging.FileHandler('jina-profile-%s.json' % __uptime__, delay=True)
            file_handler.setFormatter(ProfileFormatter(timed_fmt_str))
            logger.addHandler(file_handler)

            if sse:
                queue_handler = QueueHandler(__profile_queue__)
                queue_handler.setLevel(verbose_level.value)
                queue_handler.setFormatter(JsonFormatter(timed_fmt_str))
                logger.addHandler(queue_handler)
        else:
            if sse:
                queue_handler = QueueHandler(__log_queue__)
                queue_handler.setLevel(verbose_level.value)
                queue_handler.setFormatter(JsonFormatter(timed_fmt_str))
                logger.addHandler(queue_handler)

            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(verbose_level.value)
            console_handler.setFormatter(ColorFormatter(fmt_str))
            logger.addHandler(console_handler)

            if os.environ.get('JINA_LOG_FORMAT') == 'TXT':
                file_handler = logging.FileHandler('jina-%s.log' % __uptime__, delay=True)
                file_handler.setFormatter(PlainFormatter(timed_fmt_str))
                logger.addHandler(file_handler)
            elif os.environ.get('JINA_LOG_FORMAT') == 'JSON':
                file_handler = logging.FileHandler('jina-%s.json' % __uptime__, delay=True)
                file_handler.setFormatter(JsonFormatter(timed_fmt_str))
                logger.addHandler(file_handler)

    return logger