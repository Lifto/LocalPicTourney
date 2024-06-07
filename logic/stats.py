from __future__ import division, absolute_import, unicode_literals



from functools import wraps

from datadog import initialize, statsd
from flask.ext.restful.utils import unpack
#import statsd

from log import log
from settings import settings
from util import now

#CLIENT = statsd.StatsClient(settings.STATSD_HOST, settings.STATSD_PORT)
initialize(settings.DATADOG_API_KEY, settings.DATADOG_APP_KEY)

def incr(name):
    if settings.STATSD_ENABLED:
        #CLIENT.incr(name)
        statsd.increment(name)
        if settings.STATS_LOG:
            log.info('stats incr: %s', name)

def timing(name, value):
    if settings.STATSD_ENABLED:
        statsd.timing(name, value)
        if settings.STATS_LOG:
            log.info('stats timing: %s %s', name, value)

class timingDecorator(object):
    def __init__(self, name):
        self.name = name

    def __call__(self, f):
        @wraps(f)
        def wrapped_f(*args, **kwargs):
            timer = now()
            retval = f(*args, **kwargs)
            timing(self.name, (now()-timer).total_seconds())
            return retval
        return wrapped_f

class timingIncrDecorator(object):
    def __init__(self, name, track_status=True):
        """Wrap a function that sends timing and an increment to statsd.

        If track_status is True, generate additional statsd data for the
        returned HTTP status (2xx, 4xx, 5xx) if return is an HTTPResponse.

        """
        self.name = name
        self.track_status = track_status

    def __call__(self, f):
        @wraps(f)
        def wrapped_f(*args, **kwargs):
            timer = now()
            retval = f(*args, **kwargs)
            duration = (now()-timer).total_seconds()
            timing('%s-timing' % self.name, duration)
            incr('%s-incr' % self.name)
            if self.track_status:
                # This seems to be my option for introspecting returns for
                # HTTP Responses.
                if isinstance(retval, tuple):
                    data, code, headers = unpack(retval)
                else:
                    code = 200

                if code >= 200 and code < 300:
                    timing('2xx-timing', duration)
                    incr('2xx-incr')
                elif code >= 400 and code < 500:
                    timing('4xx-timing', duration)
                    incr('4xx-incr')
                elif code >= 500 and code < 600:
                    timing('5xx-timing', duration)
                    incr('5xx-incr')
                elif code >= 300 and code < 400:
                    timing('3xx-timing', duration)
                    incr('3xx-incr')
                elif code >= 100 and code < 200:
                    timing('1xx-timing', duration)
                    incr('1xx-incr')
                else:
                    log.warn('timingIncrDecorator unknown status "%s"' % code)
            return retval
        return wrapped_f


class classAwareDecorator(object):
    def __init__(self, name):
        """Wrap a function that sends timing and an increment to statsd.

        Stat name is prefixed with class name. Used in Model to get named
        stat tracking automatically.

        """
        self.name = name

    def __call__(self, f):
        @wraps(f)
        def wrapped_f(*args, **kwargs):
            timer = now()
            retval = f(*args, **kwargs)
            duration = (now()-timer).total_seconds()
            timing('%s.%s-timing' % (args[0].__name__, self.name), duration)
            incr('%s.%s-incr' % (args[0].__name__, self.name))
            return retval
        return wrapped_f


class instanceAwareDecorator(object):
    def __init__(self, name):
        """Wrap a function that sends timing and an increment to statsd.

        Stat name is prefixed with instance's class name. Used in Model to get
        named stat tracking automatically.

        """
        self.name = name

    def __call__(self, f):
        @wraps(f)
        def wrapped_f(*args, **kwargs):
            timer = now()
            retval = f(*args, **kwargs)
            duration = (now()-timer).total_seconds()
            timing('%s.%s-timing' % (type(args[0]).__name__, self.name), duration)
            incr('%s.%s-incr' % (type(args[0]).__name__, self.name))
            return retval
        return wrapped_f

