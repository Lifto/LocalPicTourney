from __future__ import division, absolute_import, unicode_literals

import os

import logging
import logging.config
from settings import settings

logger_name = '{}-{}'.format(settings.NAME.lower(), settings.MODE.lower())

env_settings_name = os.getenv('OCEAN_SETTINGS', None)
env_settings_name = env_settings_name.replace('-', '_')

if env_settings_name.endswith('_worker'):
    env_settings_name = env_settings_name[:-len('_worker')]
elif env_settings_name.endswith('_server'):
    env_settings_name = env_settings_name[:-len('_server')]
conf_name = './logging_conf/{}_logging.conf'.format(env_settings_name)

logging.config.fileConfig(conf_name)
log = logging.getLogger(logger_name)
log.info("{} logging enabled".format(settings.NAME))
log.debug("{} DEBUG logging enabled".format(settings.NAME))
