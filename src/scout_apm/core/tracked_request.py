from __future__ import absolute_import

import logging
from datetime import datetime
from uuid import uuid4

from scout_apm.core.samplers import Samplers
from scout_apm.core.request_manager import RequestManager
from scout_apm.core.thread_local import ThreadLocalSingleton

# Logging
logger = logging.getLogger(__name__)


class SpanManager():
    """
    Internal class, get an instance of by:

    with TrackedRequest.instance().span(operation="SQL/Query") as span:
    """
    def __init__(self, tracked_request, operation, on_error=None):
        self.tracked_request = tracked_request
        self.on_error = on_error
        self.operation = operation

    def __enter__(self):
        span = self.tracked_request.start_span(self.operation)
        self.span = span
        return span

    def __exit__(self, exception_type, exception_value, traceback):
        if self.on_error is not None and exception_type is not None:
            self.on_error(exception_type, exception_value, traceback)
        self.tracked_request.stop_span()

class TrackedRequest(ThreadLocalSingleton):
    """
    This is a container which keeps track of all module instances for a single
    request. For convenience they are made available as attributes based on
    their keyname
    """
    def __init__(self, *args, **kwargs):
        self.req_id = 'req-' + str(uuid4())
        self.start_time = kwargs.get('start_time', datetime.utcnow())
        self.end_time = kwargs.get('end_time', None)
        self.active_spans = kwargs.get('active_spans', [])
        self.complete_spans = kwargs.get('complete_spans', [])
        self.tags = kwargs.get('tags', {})
        self.real_request = kwargs.get('real_request', False)
        logger.debug('Starting request: %s', self.req_id)

    def mark_real_request(self):
        self.real_request = True

    def is_real_request(self):
        return self.real_request

    def tag(self, key, value):
        if hasattr(self.tags, key):
            logger.debug('Overwriting previously set tag for request %s: %s' % self.req_id, key)
        self.tags[key] = value

    def span(self, operation=None, on_error=None):
        """
        Returns a Span ContextManager, wrapping a section of code
        """
        return SpanManager(self, operation=operation, on_error=on_error)

    def start_span(self, operation=None):
        maybe_parent = self.current_span()

        if maybe_parent is not None:
            parent_id = maybe_parent.span_id
        else:
            parent_id = None

        new_span = Span(
            request_id=self.req_id,
            operation=operation,
            parent=parent_id)
        self.active_spans.append(new_span)
        return new_span

    def stop_span(self):
        if len(self.active_spans) == 0:
            logger.debug('Skipping attempt to pop span off empty list')
            return

        stopping_span = self.active_spans.pop()
        stopping_span.stop()
        self.complete_spans.append(stopping_span)
        if len(self.active_spans) == 0:
            self.finish()

    def current_span(self):
        if len(self.active_spans) > 0:
            return self.active_spans[-1]
        else:
            return None

    # Request is done, release any info we have about it.
    def finish(self):
        logger.debug('Stopping request: %s', self.req_id)
        if self.end_time is None:
            self.end_time = datetime.utcnow()
        RequestManager.instance().add_request(self)
        if self.is_real_request():
            Samplers.ensure_running()
        self.release()


class Span:
    def __init__(self, *args, **kwargs):
        self.span_id = kwargs.get('span_id', 'span-' + str(uuid4()))
        self.start_time = kwargs.get('start_time', datetime.utcnow())
        self.end_time = kwargs.get('end_time', None)
        self.request_id = kwargs.get('request_id', None)
        self.operation = kwargs.get('operation', None)
        self.parent = kwargs.get('parent', None)
        self.tags = kwargs.get('tags', {})

    def dump(self):
        if self.end_time is None:
            logger.debug(self.operation)
        return 'request=%s operation=%s id=%s parent=%s start_time=%s end_time=%s' % (
                self.request_id,
                self.operation,
                self.span_id,
                self.parent,
                self.start_time.isoformat(),
                self.end_time.isoformat()
            )

    def stop(self):
        self.end_time = datetime.utcnow()

    def tag(self, key, value):
        if hasattr(self.tags, key):
            logger.debug('Overwriting previously set tag for span %s: %s' % self.span_id, key)
        self.tags[key] = value

    # In seconds
    def duration(self):
        if self.end_time is not None:
            (self.end_time - self.start_time).total_seconds()
        else:
            # Current, running duration
            (datetime.utcnow() - self.start_time).total_seconds()
