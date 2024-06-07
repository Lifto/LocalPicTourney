from __future__ import division, absolute_import, unicode_literals

import base64
from datetime import datetime
import json
import logging
import os
import random
import string
import unittest
from uuid import uuid1, UUID

from apps import wsgi_app
from logic.location import Geo, Location
from logic.user import create_user
from model import create_model, Match, Photo, User, UserName
import settings
from util import now
from util import generate_random_string as rand_string

logger = logging.getLogger("LocalPicTourney_Test")

# -- Helpers ------------------------------------------------------------------

def auth_header_value(user_uuid, token):
    # Username and password are combined into a string "username:password"
    val = u'%s:%s' % (user_uuid, token)
    val_encoded = base64.b64encode(val)
    header = u'Authorization: Basic %s' % val_encoded
    return header


def headers_with_auth(user_uuid, token):
    return {'Content-Type': 'text/plain; charset=utf-8',
            'Authorization': auth_header_value(user_uuid.hex, token)}

la_s = "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"
la_geo = Geo.from_string(la_s)
la_location = Location.from_geo(la_geo)
la_geo_header = {'Geo-Position': la_s}
boston_s = "42.30001;-71.10001;0.0 hdn=-1.0 spd=0.0"
boston_geo = Geo.from_string(boston_s)
boston_location = Location.from_geo(boston_geo)
boston_geo_header = {'Geo-Position': boston_s}

# -- Test Base Class ----------------------------------------------------------

class LocalPicTourneyAdminTestCase(unittest.TestCase):

    def setUp(self):
        # TODO: We should be able to start dynalite from here, and restart it
        # as needed.  I tried with proc but it wasn't connectable.
        create_model()
        wsgi_app.application.config['TESTING'] = True
        self.application = wsgi_app.application.test_client()


    def tearDown(self):
        # We don't call delete table here because we don't want to accidentally
        # run delete_table on production.
        # Tests should use randomly generated input to prevent collisions.
        # Testing is expected to be run on a droppable data, like a dev's
        # dynalite instance.
        pass

    #--- Service --------------------------------------------------------------

    def test_test_get(self):
        rv = self.application.get('/admin')
        self.assertEqual('200 OK', rv.status)
        #data = json.loads(rv.data)
        #self.assertEqual({'version': '0.1.1'}, data)


if __name__ == '__main__':
    unittest.main()