from __future__ import division, absolute_import, unicode_literals

import importlib
import os

# The Settings object first checks the environment for OCEAN__<name>, then
# checks the settings module for <name>. Results are cached, so once a value
# has been accessed it never changes. (Say, by way of changing the env var.)

# I wonder if this could be hooked in to Zookeeper and offer a simple hook
# interface for settings that change.

class Settings(object):

    def __init__(self, settings_name):
        self.cache = {}
        self.settings_name = settings_name
        self.lib = importlib.import_module("settings.{}".format(settings_name))

    def __getattr__(self, name):
        try:
            return self.cache[name]
        except KeyError:
            got = os.getenv('OCEAN__{}'.format(name))
            if got is not None:
                # TODO: This prevents certain strings like True and False
                if got == 'False':
                    got = False
                elif got == 'True':
                    got = True
                self.cache[name] = got
                return got
            got = getattr(self.lib, name)
            self.cache[name] = got
            return got

def _get_settings(settings_name=None):
    """Return the settings module for the given settings name."""
    if settings_name is None:
        env_settings_name = os.getenv('OCEAN_SETTINGS', None)
        if env_settings_name is None:
            raise ValueError('No settings name found in env var OCEAN_SETTINGS')
        env_settings_name = env_settings_name.replace('-', '_')
        settings_name = env_settings_name
    return Settings(settings_name)

settings = _get_settings()