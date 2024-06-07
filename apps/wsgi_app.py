from __future__ import division, absolute_import, unicode_literals


# TODO: An API that accepts JSON encoded POST, PUT & PATCH requests should also
# require the Content-Type header be set to application/json or throw a 415
# Unsupported Media Type HTTP status code.

# -- Hacks --------------------------------------------------------------------

# Note: Here we remove bogus 'key' info from the environment because
# on EC2 we have blanks in there and it reads them and then barfs. (It
# will otherwise get the info from another config system, IF the env is
# empty of these.) HOWEVER when we run in local dev, we need these.
# For python 3 use: os.environ.has_key("HOME")
# Note: This is fixable on EB, but I don't yet know how to do it, I
# think it require use of the AWS API.
import os
if os.environ.get('IS_LOCAL_DEV') == '0':
    try:
        del os.environ['AWS_SECRET_KEY']
    except KeyError:
        pass
    try:
        del os.environ['AWS_ACCESS_KEY_ID']
    except KeyError:
        pass

# -- Imports ------------------------------------------------------------------

from flask import Flask

from apps.admin.admin import admin_blueprint
from apps.api.api import api_blueprint
from log import log
from model import create_model
from settings import settings
from apps.share.share import share_blueprint

# -- Logging ------------------------------------------------------------------

if settings.IS_WORKER:
    log.error("Attempting to run wsgi_app as worker, exiting")
    exit(1)

log.info("{} running in mode '{}'".format(settings.NAME, settings.MODE))

# -- Application Init ---------------------------------------------------------

# The application creates the model, adding any missing tables.
# Note: Long-term migrations should be controlled with their own system,
# though this is convenient for our current development stage.
create_model()

application = Flask(__name__)
application.debug = False
application.logger_name = 'wsgi_flask'
application.logger.info('{} Flask Logger engaged'.format(settings.NAME))
application.config.setdefault("HTTP_BASIC_AUTH_REALM", "localpictourney")
# TODO: Move the api_blueprint to a versioned folder.
application.register_blueprint(api_blueprint, url_prefix=settings.URL_PREFIX)
application.register_blueprint(share_blueprint, url_prefix='')
application.register_blueprint(admin_blueprint, url_prefix='/admin')
log.info(application.url_map)
log.info("blueprint registration complete")

if __name__ == '__main__':
    application.run(host='0.0.0.0', debug=True)
