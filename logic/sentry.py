from __future__ import division, absolute_import, unicode_literals

from functools import wraps

from raven.base import DummyClient
from raven.contrib.flask import Sentry

from settings import settings
from log import log

CLIENT = None

def get_client():
    global CLIENT
    if CLIENT is None:
        if settings.SENTRY_ENABLED:
            CLIENT = Sentry(dsn=settings.SENTRY_DSN)
            if settings.IS_WORKER:
                from apps.worker import application
                CLIENT.init_app(application)
            else:
                from apps.wsgi_app import application
                CLIENT.init_app(application)
        else:
            log.info("Raven about to complain because we are using the DummyClient")
            CLIENT = DummyClient()
    return CLIENT

class sentryDecorator(object):
    def __call__(self, f):
        @wraps(f)
        def wrapped_f(*args, **kwargs):
            try:
                retval = f(*args, **kwargs)
            except Exception as e:
                get_client().captureException()
                raise
            return retval
        return wrapped_f


# Note: can read configuration from the ``SENTRY_DSN`` environment variable
# client = Client()

# Private DSN for the lifto@mac.com localpictourney-dev account
# https://somecode:somecode@app.getsentry.com/59918
# Public DSN (for use in javascript, etc)
# https://somecode:somecode@app.getsentry.com/59918

# Example
# try:
#     1 / 0
# except ZeroDivisionError:
#     client.captureException()

# or

# client.captureMessage('Something went fundamentally wrong')
