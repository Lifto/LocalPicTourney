from __future__ import division, absolute_import, unicode_literals



import base64
import copy
from datetime import datetime, timedelta
from itertools import cycle
import json
import logging
import os
import random
import string
import unittest
from uuid import uuid1, UUID

from logic import facebook
from logic.kinesis import get_kinesis
from logic.location import Geo, Location, LOCATIONS
from logic.user import create_user, get_user, update_registration_status
from model import create_model, FacebookLog, HourLeaderboard, Match, \
    MonthLeaderboard, Photo, ProfileOnlyPhoto, TodayLeaderboard, User, \
    UserName, WeekLeaderboard, YearLeaderboard
from logic.photo import create_photo, crop, get_photo
from logic.sqs import get_queue
from settings import settings
from util import now, pluralize
from util import generate_random_string as rand_string
from apps import worker
from apps import wsgi_app

log = logging.getLogger("LocalPicTourney_Test")

# -- Helpers ------------------------------------------------------------------

la_s = "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"
la_geo = Geo.from_string(la_s)
la_location = Location.from_geo(la_geo)
la_geo_header = {'Geo-Position': la_s}
boston_s = "42.30001;-71.10001;0.0 hdn=-1.0 spd=0.0"
boston_geo = Geo.from_string(boston_s)
boston_location = Location.from_geo(boston_geo)
boston_geo_header = {'Geo-Position': boston_s}

def auth_header_value(user_uuid, token):
    # Username and password are combined into a string "username:password"
    val = u'%s:%s' % (user_uuid, token)
    val_encoded = base64.b64encode(val)
    header = u'Authorization: Basic %s' % val_encoded
    return header


def headers_with_auth(user_uuid, token):
    return {'Content-Type': 'application/json',
            'Authorization': auth_header_value(user_uuid.hex, token)}

def get_headers(user, geo=la_s):
    headers = headers_with_auth(user.uuid, user.token)
    headers['Geo-Position'] = geo
    return headers

def app_url(endpoint):
    return '{}{}'.format(settings.URL_PREFIX, endpoint)

def _error_kwargs(kwargs):
    try:
        error_message = kwargs['error_message']
    except KeyError:
        error_message = None
    else:
        del kwargs['error_message']
    return error_message

la_s = "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"
la_geo = Geo.from_string(la_s)
la_location = Location.from_geo(la_geo)
la_geo_header = {'Geo-Position': la_s}
boston_s = "42.30001;-71.10001;0.0 hdn=-1.0 spd=0.0"
boston_geo = Geo.from_string(boston_s)
boston_location = Location.from_geo(boston_geo)
boston_geo_header = {'Geo-Position': boston_s}


def create_user_with_photo(show_gender_male=False,
                           location=la_location,
                           random_score=False):
    user = create_user()
    user.show_gender_male = show_gender_male
    user.lat = float(location.lat)
    user.lon = float(location.lon)
    user.geodata = location._make_geo_meta()
    user.location = location.uuid
    user.save()
    photo_uuid = uuid1()
    if show_gender_male:
        gender_location = 'm%s' % location.uuid.hex
    else:
        gender_location = 'f%s' % location.uuid.hex
    photo = Photo(gender_location, photo_uuid)
    photo.is_gender_male = show_gender_male
    photo.lat = float(location.lat)
    photo.lon = float(location.lon)
    photo.geodata = location._make_geo_meta()
    photo.location = location.uuid
    photo.post_date = datetime.now()
    photo.user_uuid = user.uuid
    photo.copy_complete = True
    photo.file_name = "%s_%s" % (gender_location, photo_uuid.hex)
    photo.set_as_profile_photo = True
    if random_score:
        photo.score = random.randrange(1200, 1800)
    photo.save()
    user.photo = photo.uuid
    user.save()
    return user

# -- Test Base Class ----------------------------------------------------------

class LocalPicTourneyTestCase(unittest.TestCase):

    def setUp(self):
        # TODO: We should be able to start dynalite from here, and restart it
        # as needed.  I tried with proc but it wasn't connectable.
        if not settings.MODE == 'unit-test':
            log.error("Can not run tests with settings.NAME != 'unit_test_server', it could change the production database. was '{}'".format(settings.MODE))
            raise ValueError
        # Here we check if model configured to connect to real AWS resource
        # and not local.
        from model import OceanMeta
        if not OceanMeta.host:
            log.error('Can not run tests OceanMeta.host does not have a value, it implies changing the production database.')
            raise ValueError

        if hasattr(OceanMeta, 'region') and OceanMeta.region:
            log.error('Can not run tests OceanMeta.region has a value, it implies changing the production database.')
            raise ValueError

        create_model()
        wsgi_app.application.config['TESTING'] = True
        self.application = wsgi_app.application.test_client()
        worker.application.config['TESTING'] = True
        self.worker = worker.application.test_client()

    def tearDown(self):
        # We don't call delete table here because we don't want to accidentally
        # run delete_table on production.
        # Tests should use randomly generated input to prevent collisions.
        # Testing is expected to be run on a droppable data, like a dev's
        # dynalite instance.
        pass

    def reset_model(self):
        import model
        for m in model.get_models():
            m.delete_table()
        while any(m for m in model.get_models() if m.exists()):
            from time import sleep
            sleep(1)
        model.create_model()

    def create_user(self, gender='male', view_gender='female',
                    geodata=la_geo.meta, first_name=None, user_name=None,
                    last_name=None, random_user_name=True, facebook=True):
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'test user agent'
        }
        if geodata is not None:
            headers.update({'Geo-Position': geodata})
        result = self.post201('/users', headers=headers)
        self._assert_create_user(result)
        user_uuid = UUID(result['uuid'])
        user = User.get(user_uuid)
        headers = get_headers(user)
        self.assertEqual('test user agent', user.user_agent)

        data = {}
        if gender:
            data['gender'] = gender
        if view_gender:
            data['view_gender'] = view_gender
        if geodata:
            data['location'] = geodata
        if first_name is not None:
            data['first_name'] = first_name
        if user_name is not None:
            data['user_name'] = user_name
        if last_name is not None:
            data['last_name'] = last_name
        elif random_user_name:
            data['user_name'] = 'username%s' % rand_string(10)

        if geodata:
            self.patch200('/users/me',
                          data=json.dumps(data),
                          headers=get_headers(user))
        else:
            self.patch204('/users/me',
                          data=json.dumps(data),
                          headers=get_headers(user))

        if facebook:
            self.post_facebook(user)
            user.refresh()

        return user



    #     def create_user_with_photo(show_gender_male=False,
    #                        location=la_location,
    #                        random_score=False):
    # user = create_user()
    # user.show_gender_male = show_gender_male
    # user.lat = float(location.lat)
    # user.lon = float(location.lon)
    # user.geodata = location._make_geo_meta()
    # user.location = location.uuid
    # user.save()
    #
    #
    #
    #     result = json.loads(rv.data)
    #     user_uuid = UUID(result['uuid'])
    #     user = User.get(user_uuid)
    #     self.assertEqual('test user agent', user.user_agent)
    #     if categories:
    #         data = {'categories': {k: True for k in categories}}
    #         data = json.dumps(data)
    #         rv = self.application.patch('/users/me',
    #                                     data=data,
    #                                     headers=get_headers(user))
    #         self.assert204(rv)
    #     return user

    def _check(self, var, name, d):
        if var is not None:
            self.assertEqual(var, d[name])
        else:
            self.assertIn(name, d)

    def get(self, endpoint, **kwargs):
        return self.application.get(app_url(endpoint), **kwargs)

    def get200(self, endpoint, **kwargs):
        return self.assert200(self.get(endpoint, **kwargs))

    def get204(self, endpoint, **kwargs):
        return self.assert204(self.get(endpoint, **kwargs))

    def get400(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert400(self.get(endpoint, **kwargs),
                              error_message=error_message)

    def get401(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert401(self.get(endpoint, **kwargs),
                              error_message=error_message)

    def get404(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert404(self.get(endpoint, **kwargs),
                              error_message=error_message)

    def get409(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert409(self.get(endpoint, **kwargs),
                              error_message=error_message)

    def post(self, endpoint, **kwargs):
        return self.application.post(app_url(endpoint), **kwargs)

    def post200(self, endpoint, **kwargs):
        return self.assert200(self.post(endpoint, **kwargs))

    def post201(self, endpoint, **kwargs):
        return self.assert201(self.post(endpoint, **kwargs))

    def post202(self, endpoint, **kwargs):
        return self.assert202(self.post(endpoint, **kwargs))

    def post401(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert401(self.post(endpoint, **kwargs),
                              error_message=error_message)

    def post409(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert409(self.post(endpoint, **kwargs),
                              error_message=error_message)

    def put(self, endpoint, **kwargs):
        return self.application.put(app_url(endpoint), **kwargs)

    def put200(self, endpoint, **kwargs):
        return self.assert200(self.put(endpoint, **kwargs))

    def put204(self, endpoint, **kwargs):
        return self.assert204(self.put(endpoint, **kwargs))

    def put400(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert400(self.put(endpoint, **kwargs),
                              error_message=error_message)

    def put401(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert401(self.put(endpoint, **kwargs),
                              error_message=error_message)

    def put404(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert404(self.put(endpoint, **kwargs),
                              error_message=error_message)

    def put409(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert409(self.put(endpoint, **kwargs),
                              error_message=error_message)

    def patch(self, endpoint, **kwargs):
        return self.application.patch(app_url(endpoint), **kwargs)

    def patch200(self, endpoint, **kwargs):
        return self.assert200(self.patch(endpoint, **kwargs))

    def patch204(self, endpoint, **kwargs):
        return self.assert204(self.patch(endpoint, **kwargs))

    def patch400(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert400(self.patch(endpoint, **kwargs),
                              error_message=error_message)

    def patch401(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert401(self.patch(endpoint, **kwargs),
                              error_message=error_message)

    def patch409(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert409(self.patch(endpoint, **kwargs),
                              error_message=error_message)

    def patch415(self, endpoint, **kwargs):
        error_message = _error_kwargs(kwargs)
        return self.assert415(self.patch(endpoint, **kwargs),
                              error_message=error_message)

    def pop_sqs_to_worker(self):
        next = get_queue().messages.pop(0)
        data = base64.b64encode(next.get_body())
        self.worker.post('worker_callback', data=data)

    def worker_process_photo(self, key_value):
        # This is a message like the one s3 sends to our worker
        data = {
            'Records': [
                {'awsRegion': 'us-west-2',
                 'eventName': 'ObjectCreated:Post',
                 'eventSource': 'aws:s3',
                 'eventTime': '2015-02-09T19:27:54.912Z',
                 'eventVersion': '2.0',
                 'requestParameters': {
                     'sourceIPAddress': '71.254.65.74'},
                 'responseElements': {
                     'x-amz-id-2': 'K2vI/PfJWPnLnrxp2fYzVpk2ZlOmU5LBcg+2WbvXHKlnhKJZsiJxKRlz4jf85D3E',
                     'x-amz-request-id': 'F0C539AAE5B9A49D'},
                 's3': {
                     'bucket': {
                         'arn': 'arn:aws:s3:::localpictourney-inbox',
                         'name': 'localpictourney-inbox',
                         'ownerIdentity': {
                             'principalId': 'A3UCIRG5KSHMKI'}},
                     'configurationId': 'Upload',
                     'object': {'eTag': '1a7f86b3e0650c3c09d7990fefdda44f',
                                'key': key_value,
                                'size': 8634},
                     's3SchemaVersion': '1.0'},
                  'userIdentity': {
                      'principalId': 'AWS:AROAIDVTFQBCHQ5PUVFNQ:i-e9d3e4e3'}}]}
        self.worker.post('worker_callback', data=json.dumps(data))

    def photo_upload(self, headers={},
                     set_as_profile_photo=False, enter_in_tournament=True,
                     tags=[]):
        raw_data = {'set_as_profile_photo': set_as_profile_photo,
                    'enter_in_tournament': enter_in_tournament}
        if tags:
            raw_data['tags'] = tags
        elif random.random() > 0.5:
            # Half the time test if an empty arg works.
            raw_data['tags'] = tags
        data = json.dumps(raw_data)
        result = self.post202('/photos', data=data, headers=headers)
        photo_uuid_hex = result['id']
        share_url = result['share_url']
        post_form_args = result['post_form_args']

        self.assertIn('fields', post_form_args)
        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)

        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy", "value": "aoethuasoetuhasoteuhsaotehusaotehusatoehu123123123123="},
                {"name": "AWSAccessKeyId", "value": "12345"},
                {"name": "signature", "value": "aosetuhasteuhasotehuaaoeu="},
                {"name": "key", "value": key_value}]}
        self.maxDiff = None
        self.assertDictEqual(
            check_result,
            post_form_args)

        self.worker_process_photo(key_value)

        photo_uuid = UUID(key_value.split('_')[1])
        return photo_uuid

    def post_facebook(self, user, generate_credentials=True,
                      force_id=None, force_token=None):
        """Post the users facebook token to facebook (simulated).

        if generate_credentials is True then force_id and force_token
        are ignored. Matching id and token are generated and added to
        facebook.py's test data

        if generate_credentials is False then force_id and force_token
        are added to the test data if not None.

        """
        # Notes:
        # Our system requires that each user have a unique facebook_id,
        # and that when we query by token it returns the matching facebook_id.
        #
        # We want to be able to test these error states too, so we can't
        # always generate matching information.
        #
        # Our facebook module allows us to register ids and tokens.
        #
        # The default behavior is to create a new matching id and token.

        if generate_credentials:
            facebook_id = rand_string(20)
            facebook_token = rand_string(200)
        else:
            facebook_id = force_id
            facebook_token = force_token
        facebook.add_test_data(facebook_id, facebook_token)

        headers = get_headers(user)

        self.assertEqual('MockSQSQueue', type(get_queue()).__name__)
        self.assertEqual(0, len(get_queue().messages))
        data = json.dumps({'token': facebook_token})
        rv = self.put204('/users/me/facebook', data=data, headers=headers)

        user.refresh()
        self.assertEqual(user.facebook_api_token, facebook_token)

        # The PUT puts a SQS message on the worker to query facebook data.
        self.assertEqual(1, len(get_queue().messages))

        # This calls the worker
        self.pop_sqs_to_worker()
        self.assertEqual(0, len(get_queue().messages))

        user.refresh()
        self.assertEqual(user.facebook_id, facebook_id)
        self.assertEqual(user.facebook_api_token, facebook_token)
        self.assertNotEqual(user.registration, 'need facebook')

        # Check the log.
        logs = list(FacebookLog.query(user.uuid))
        self.assertEqual(1, len(logs))
        # TODO - better facebook log check
#        self.assertDictEqual(json.loads(facebook.TEST_DATA[facebook.FB_URL]),
#                             json.loads(logs[0].data))


    def assertStatus(self, status, response, error_message=None):
        try:
            result = json.loads(response.data)
        except:
            try:
                result = response.data
            except:
                result = ''
        if response.status != status:
            log.info('assertStatus FAIL')
            log.info(result)
        self.assertEqual(status, response.status)
        if error_message is not None:
            self.assertEqual(error_message,
                             result[u'message'])
        return result

    def assert200(self, response):
        return self.assertStatus(u'200 OK', response)

    def assert201(self, response):
        return self.assertStatus(u'201 CREATED', response)

    def assert202(self, response):
        return self.assertStatus(u'202 ACCEPTED', response)

    def assert204(self, response):
        result = self.assertStatus(u'204 NO CONTENT', response)
        self.assertEqual(result, '')
        return result

    def assert400(self, response, error_message=None):
        return self.assertStatus(u'400 BAD REQUEST',
                                 response,
                                 error_message=error_message)

    def assert401(self, response, error_message=None):
        return self.assertStatus(u'401 UNAUTHORIZED',
                                 response,
                                 error_message=error_message)

    def assert404(self, response, error_message=None):
        return self.assertStatus(u'404 NOT FOUND',
                                 response,
                                 error_message=error_message)

    def assert409(self, response, error_message=None):
        return self.assertStatus(u'409 CONFLICT',
                                 response,
                                 error_message=error_message)

    def assert415(self, response, error_message=None):
        return self.assertStatus(u'415 UNSUPPORTED MEDIA TYPE',
                                 response,
                                 error_message=error_message)

    def assert_feed_joined(self, feed_result, read=False, created_on=None):
        print feed_result
        self.assertEqual('Joined', feed_result[u'activity'])
        self.assertEqual(read, feed_result[u'read'])
        if created_on is not None:
            self.assertEqual(created_on, feed_result[u'created_on'])
        else:
            self.assertIn(u'created_on', feed_result)

    def _assert_render_photo(self, photo_data, photo=None, location=None,
                             location_name=None, score=None,
                             gender_is_male=None, user=None):
        self._check(photo, u'id', photo_data)
        self._check(location, u'location', photo_data)
        self._check(location_name, u'location_name', photo_data)
        self._check(score, u'score', photo_data)

        # We can't guess the gender location without the gender.
        if gender_is_male is not None and location is not None and photo is not None:
            gender = u'm' if gender_is_male else u'f'
            key = '%s%s_%s' % (gender, location, photo)
            base_url = '%s/%s' % (settings.SERVE_BUCKET_URL, key)
            url_small = '%s_240x240' % base_url
            url_medium = '%s_480x480' % base_url
            url_large = '%s_960x960' % base_url
            self.assertDictContainsSubset({u'url_small': url_small},
                                          photo_data)
            self.assertDictContainsSubset({u'url_medium': url_medium},
                                          photo_data)
            self.assertDictContainsSubset({u'url_large': url_large},
                                          photo_data)

        if user is not None:
            self.assertEqual(photo_data['user'][u'uuid'], user)

    def assert_feed_new_photo(self, feed_result, photo=None, created_on=None,
                              location=None, location_name=None, score=None,
                              gender_is_male=None, user=None, read=None):
# {u'activity': u'NewPhoto',
#  u'created_on': 14484687212435570,
#  u'photo': {u'id': u'1f7aea73939111e584e0c8e0eb16059b',
#             u'location': u'67f22847ecf311e4a264c8e0eb16059b',
#             u'location_name': u'Los Angeles',
#             u'score': 1500,
#             u'url_large': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/f67f22847ecf311e4a264c8e0eb16059b_1f7aea73939111e584e0c8e0eb16059b_960x960',
#             u'url_medium': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/f67f22847ecf311e4a264c8e0eb16059b_1f7aea73939111e584e0c8e0eb16059b_480x480',
#             u'url_small': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/f67f22847ecf311e4a264c8e0eb16059b_1f7aea73939111e584e0c8e0eb16059b_240x240',
#             u'user': {u'uuid': u'1d185ca6939111e5823fc8e0eb16059b'}},
#  u'read': False,
#  u'user': {u'uuid': u'1d185ca6939111e5823fc8e0eb16059b'}}
        self.assertEqual(u'NewPhoto', feed_result[u'activity'])
        self.assertIn('id', feed_result)
        self.assertIsNotNone(feed_result['id'])

        self._check(created_on, 'created_on', feed_result)
        self._check(read, 'read', feed_result)

        self.assertIn(u'user', feed_result)
        user_data = feed_result[u'user']
        self._check(user, u'uuid', user_data)

        self.assertIn(u'photo', feed_result)
        photo_data = feed_result[u'photo']
        self._assert_render_photo(photo_data, photo=photo, location=location,
                                  location_name=location_name, score=score,
                                  gender_is_male=gender_is_male, user=user)

    def assert_feed_new_comment(self, feed_result, created_on=None, read=None,
                                photo_user=None, photo=None,
                                photo_location=None,
                                photo_location_name=None, photo_score=None,
                                photo_gender_is_male=None,
                                commenter_gender_is_male=None,
                                commenter_first_name=None,
                                commenter_location=None,
                                commenter_location_name=None,
                                commenter_photo=None,
                                comment_posted_at=None,
                                commenter_score=None,
                                comment_text=None,
                                comment_uuid=None,
                                commenter_uuid=None):
# {u'activity': u'NewComment',
#  u'comment': {u'first_name': u'commenter 1',
#               u'gender': u'male',
#               u'location': u'67f22847ecf311e4a264c8e0eb16059b',
#               u'location_name': u'Los Angeles',
#               u'posted_at': 14484736479998310,
#               u'score': 1500,
#               u'text': u'test comment 4',
#               u'url_large': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/m67f22847ecf311e4a264c8e0eb16059b_97b03385939c11e5ab05c8e0eb16059b_960x960',
#               u'url_medium': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/m67f22847ecf311e4a264c8e0eb16059b_97b03385939c11e5ab05c8e0eb16059b_480x480',
#               u'url_small': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/m67f22847ecf311e4a264c8e0eb16059b_97b03385939c11e5ab05c8e0eb16059b_240x240',
#               u'user_uuid': u'979a2594939c11e5b6e6c8e0eb16059b',
#               u'uuid': u'980f75c5939c11e5802dc8e0eb16059b'},
#  u'created_on': 14484736480031310,
#  u'photo': {u'id': u'97a049e3939c11e58d2fc8e0eb16059b',
#             u'location': u'67f22847ecf311e4a264c8e0eb16059b',
#             u'location_name': u'Los Angeles',
#             u'score': 1500,
#             u'url_large': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/f67f22847ecf311e4a264c8e0eb16059b_97a049e3939c11e58d2fc8e0eb16059b_960x960',
#             u'url_medium': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/f67f22847ecf311e4a264c8e0eb16059b_97a049e3939c11e58d2fc8e0eb16059b_480x480',
#             u'url_small': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/f67f22847ecf311e4a264c8e0eb16059b_97a049e3939c11e58d2fc8e0eb16059b_240x240',
#             u'user': {u'uuid': u'97955e80939c11e595f4c8e0eb16059b'}},
#  u'read': False,
#  u'user': {u'uuid': u'979a2594939c11e5b6e6c8e0eb16059b'}}
        self.assertEqual(u'NewComment', feed_result[u'activity'])
        self.assertIn('id', feed_result)
        self.assertIsNotNone(feed_result['id'])

        self._check(created_on, 'created_on', feed_result)
        self._check(read, 'read', feed_result)

        self.assertIn(u'user', feed_result)
        return # TODO: better comment confirm.
        user_data = feed_result[u'user']
        if photo_user is not None:
            self._check(photo_user, u'uuid', user_data)

        # This is the photo commented on, and has data about the original
        # photo.
        self.assertIn(u'photo', feed_result)
        photo_data = feed_result[u'photo']
        self._assert_render_photo(photo_data, photo=photo,
                                  location=photo_location,
                                  location_name=photo_location_name,
                                  score=photo_score,
                                  gender_is_male=photo_gender_is_male,
                                  user=photo_user)

        self.assertIn(u'comment', feed_result)
        comment_data = feed_result[u'comment']
        if commenter_gender_is_male is None:
            commenter_gender = None
        else:
            if commenter_gender_is_male:
                commenter_gender = u'male'
            else:
                commenter_gender = u'female'
        # This is all information about the commenter, not the photo commented
        # on.
        if commenter_first_name is not None:
            self._check(commenter_first_name, u'first_name', comment_data)
        self._check(commenter_gender, u'gender', comment_data)  # male or female, not m/f
        self._check(commenter_location, u'location', comment_data)
        self._check(commenter_location_name, u'location_name', comment_data)
        self._check(comment_posted_at, u'posted_at', comment_data)
        self._check(comment_text, u'text', comment_data)
        self._check(comment_uuid, u'uuid', comment_data)
        self._check(commenter_uuid, u'user_uuid', comment_data)
        if commenter_photo is not None:
            self._check(commenter_score, u'score', comment_data)

        # We can't guess the gender location without the gender.
        if commenter_gender_is_male is not None and commenter_location is not None and commenter_photo is not None:
            commenter_gender = u'm' if commenter_gender_is_male else u'f'
            key = '%s%s_%s' % (commenter_gender, commenter_location, commenter_photo)
            base_url = '%s/%s' % (settings.SERVE_BUCKET_URL, key)
            url_small = '%s_240x240' % base_url
            url_medium = '%s_480x480' % base_url
            url_large = '%s_960x960' % base_url
            self.assertDictContainsSubset({u'url_small': url_small},
                                          comment_data)
            self.assertDictContainsSubset({u'url_medium': url_medium},
                                          comment_data)
            self.assertDictContainsSubset({u'url_large': url_large},
                                          comment_data)

    def assert_feed_won_tournament(self, feed_item, created_on=None,
                                   read=None, winner=None, photo=None,
                                   location=None, location_name=None,
                                   score=None, gender_is_male=None):
# {u'activity': u'WonTournament',
#  u'created_on': 14484797835692870,
#  u'photo': {u'id': u'e0713ea393aa11e586c7c8e0eb16059b',
#             u'location': u'e0270f8293aa11e59d9fc8e0eb16059b',
#             u'location_name': u'',
#             u'score': 1500,
#             u'url_large': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/fe0270f8293aa11e59d9fc8e0eb16059b_e0713ea393aa11e586c7c8e0eb16059b_960x960',
#             u'url_medium': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/fe0270f8293aa11e59d9fc8e0eb16059b_e0713ea393aa11e586c7c8e0eb16059b_480x480',
#             u'url_small': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/fe0270f8293aa11e59d9fc8e0eb16059b_e0713ea393aa11e586c7c8e0eb16059b_240x240',
#             u'user': {u'uuid': u'e06f6cee93aa11e5831ec8e0eb16059b'}},
#  u'read': False,
#  u'user': {u'uuid': u'e06f6cee93aa11e5831ec8e0eb16059b'}}
        self.assertEqual('WonTournament', feed_item['activity'])
        self.assertIn('created_on', feed_item)
        self.assertIn('id', feed_item)
        self.assertIsNotNone(feed_item['id'])
        #self._check(created_on, u'created_on', feed_item)
        self._check(read, u'read', feed_item)
        self.assertDictContainsSubset({'user': {u'uuid': winner}},
                                      feed_item)

        self.assertIn(u'photo', feed_item)
        photo_data = feed_item[u'photo']
        self._assert_render_photo(photo_data, photo=photo,
                                  location=location.hex,
                                  location_name=location_name, score=score,
                                  gender_is_male=gender_is_male, user=winner)

    def assert_feed_you_won_tournament(self, feed_item, feed_owner):
        raise NotImplementedError

    def assert_feed_new_follower(self, feed_item):
        raise NotImplementedError

    #--- Service --------------------------------------------------------------

    def test_test_get(self):
        data = self.get200('/test')
        self.assertEqual({'version': '0.1.1', 'dynamodb': 'OK'}, data)

    def test_config_get(self):
        data = self.get200('/config')
        self.assertEqual({'version': '0.1.1'}, data)

    def test_500(self):
        rv = self.get('/test_500')
        self.assertEqual('500 INTERNAL SERVER ERROR', rv.status)
        data = json.loads(rv.data)
        self.assertIn('message', data)
        self.assertEqual("KeyError: u'intentionally_missing_key'",
                         data['message'])
        self.assertIn('traceback', data)

    def assert_photo_render(self, photo, rendering):
        if photo.location:
            self.assertEqual(photo.location.hex, rendering['location'])
            if photo.location == la_location:
                self.assertEqual('Los Angeles', rendering['location_name'])
            else:
                self.assertIn('location_name', rendering)
        else:
            self.assertNotIn('location', rendering)
            self.assertNotIn('location_name', rendering)
        photo_url = '%s/%s_%%s' % (settings.SERVE_BUCKET_URL, photo.file_name)
        self.assertEqual(photo.uuid.hex, rendering['id'])
        self.assertEqual(photo_url % '240x240', rendering['url_small'])
        self.assertEqual(photo_url % '480x480', rendering['url_medium'])
        self.assertEqual(photo_url % '960x960', rendering['url_large'])
        self.assertEqual(photo.score, rendering['score'])
        self.assertIn('comment_count', rendering)
        self.assertEqual(photo.user_uuid.hex, rendering['user'])
        user = photo.get_user()
        if user.show_gender_male is not None:
            if user.show_gender_male:
                self.assertEqual('male', rendering['user']['gender'])
            else:
                self.assertEqual('female', rendering['user']['gender'])
        if user.first_name:
            self.assertEqual(rendering['user']['first_name'])

    # -- User -----------------------------------------------------------------

    def test_create_user(self):
        user = create_user()
        user_uuid = user.uuid
        user2 = User.get(user_uuid)
        self.assertIsNotNone(user2)
        self.assertIsNotNone(user2.token)
        self.assertIsNotNone(user2.joined_date)

    def _assert_create_user(self, result):
        self.assertIn('uuid', result)
        user_uuid = UUID(result['uuid'])
        self.assertIn('token', result)
        token = result['token']
        user = User.get(user_uuid)
        self.assertIsNotNone(user)
        self.assertEqual(token, user.token)
        return user


    def test_post_to_users_me(self):
        rv = self.post('/users/me',
                       headers={'Content-Type': 'application/json'})
        self.assertEqual('405 METHOD NOT ALLOWED', rv.status)


    def test_token_post_with_no_geodata(self):
        self.post201('/users', headers={'Content-Type': 'application/json'})


    def test_token_post_with_non_empty(self):
        # Even though this is ignored, it should not cause an error.
        name = 'm\xc3\xb8t'
        data = json.dumps({'user_name': name})
        headers = {'Content-Type': 'application/json'}
        headers.update(la_geo_header)
        result = self.post201('/users', data=data, headers=headers)
        self._assert_create_user(result)


    def test_token_post_with_empty_data(self):
        data = json.dumps({})
        headers = {'Content-Type': 'application/json'}
        headers.update(la_geo_header)
        result = self.post201('/users', data=data, headers=headers)
        self._assert_create_user(result)


    def test_token_post_for_test_user(self):
        data = json.dumps({})
        headers = {'Content-Type': 'application/json'}
        headers.update(la_geo_header)
        result = self.post201('/users?is_test=True', data=data, headers=headers)
        user = self._assert_create_user(result)
        self.assertTrue(user.is_test)


    def test_token_post_with_no_data(self):
        headers = {'Content-Type': 'application/json'}
        headers.update(la_geo_header)
        result = self.post201('/users', headers=headers)
        self._assert_create_user(result)


    def test_token_post_with_user_agent(self):
        headers = {'Content-Type': 'application/json',
                   'User-Agent': 'test user agent'}
        headers.update(la_geo_header)
        result = self.post201('/users', headers=headers)
        self._assert_create_user(result)
        user_uuid = UUID(result['uuid'])
        user = User.get(user_uuid)
        self.assertEqual('test user agent', user.user_agent)


    def test_get_user_by_user_name(self):

        user = self.create_user()

        self.assertIsNotNone(user.user_name)
        self.assertNotEqual('', user.user_name)

        user_name = user.user_name
        url = '/users_by_name/{}'.format(user_name)

        self.get401(url)

        headers = get_headers(user)

        result = self.get200(url, headers=headers)
        self.assertDictEqual({
            u'first_name': user.first_name,
            u'last_name': user.last_name,
            u'user_name': user.user_name,
            u'uuid': user.uuid.hex
        }, result)

        user2 = self.create_user()
        headers = get_headers(user2)
        result = self.get200(url, headers=headers)
        self.assertDictEqual({
            u'first_name': user.first_name,
            u'last_name': user.last_name,
            u'user_name': user.user_name,
            u'uuid': user.uuid.hex
        }, result)


    def test_user_facebook(self):
        self.assertFalse(settings.FACEBOOK_ENABLED)

        facebook_id = rand_string(20)
        facebook_api_token = rand_string(200)
        facebook.add_test_data(facebook_id, facebook_api_token)

        # Not logged in.
        test_user_name = rand_string(10)
        headers = {'Content-Type': 'application/json'}
        self.put401('/users/me/facebook',
                    data=facebook_api_token,
                    headers=headers)

        # Log in.
        user = self.create_user(random_user_name=False,
                                facebook=False,
                                gender=None,
                                geodata=None)
        self.assertEqual(user.registration, 'need name')
        headers = headers_with_auth(user.uuid, user.token)

        self.assertEqual('MockSQSQueue', type(get_queue()).__name__)
        self.assertEqual(0, len(get_queue().messages))
        data = json.dumps({'token': facebook_api_token})
        result = self.put204('/users/me/facebook',
                             data=data,
                             headers=headers)
        self.assertEqual('', result)

        user.refresh()
        self.assertEqual(user.facebook_api_token, facebook_api_token)
        self.assertEqual(user.facebook_id, facebook_id)

        # The PUT puts a SQS message on the worker to query facebook data.
        self.assertEqual(1, len(get_queue().messages))
        self.assertIsNone(user.photo)

        # This calls the worker
        self.assertIsNone(user.facebook_gender)
        self.assertIsNone(user.show_gender_male)
        self.assertIsNone(user.first_name)
        self.pop_sqs_to_worker()
        self.assertEqual(0, len(get_queue().messages))

        user.refresh()
        self.assertEqual(user.facebook_id, facebook_id)
        self.assertEqual(user.facebook_api_token, facebook_api_token)
        self.assertEqual(user.first_name, u'Will')
        self.assertIsNotNone(user.photo)
        photo = get_photo(user.photo)
        self.assertIsNotNone(photo)
        self.assertTrue(photo.copy_complete)
        self.assertEqual('female', user.facebook_gender)
        self.assertEqual(False, user.show_gender_male)
        self.assertEqual(user.registration, 'need name')

        # Check the log.
        logs = list(FacebookLog.query(user.uuid))
        self.assertEqual(1, len(logs))
        self.assertDictEqual(json.loads(facebook.TEST_DATA[facebook.FB_URL][facebook_api_token]),
                             json.loads(logs[0].data))

        data = json.dumps({'user_name': test_user_name})
        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(user.registration, 'need location')

        data = json.dumps({'location': la_s})
        self.patch200('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(user.registration, 'ok')

        # Let's change the user gender and first_name and see if it clobbers.
        user.show_gender_male = True
        new_first_name = u'testname123'
        user.first_name = new_first_name
        user.save()

        # Note this tests the ability for the same user to re-use a facebook id.
        data = json.dumps({'token': facebook_api_token})
        result = self.put204('/users/me/facebook',
                             data=data, headers=headers)
        self.assertEqual('', result)
        self.pop_sqs_to_worker()

        user.refresh()
        self.assertIsNotNone(user.photo)
        photo = get_photo(user.photo)
        self.assertIsNotNone(photo)
        self.assertTrue(photo.copy_complete)
        self.assertEqual('female', user.facebook_gender)
        self.assertEqual(True, user.show_gender_male)
        self.assertEqual(new_first_name, user.first_name)
        logs = list(FacebookLog.query(user.uuid))
        self.assertEqual(2, len(logs))
        self.assertDictEqual(json.loads(facebook.TEST_DATA[facebook.FB_URL][facebook_api_token]),
                             json.loads(logs[1].data))

        # Let's change the facebook gender and see if it clobbers.
        decoded_data = json.loads(facebook.TEST_DATA[facebook.FB_URL][facebook_api_token])
        decoded_data['gender'] = 'male'
        facebook.TEST_DATA[facebook.FB_URL][facebook_api_token] = json.dumps(decoded_data)

        result = self.put204('/users/me/facebook',
                             data=data, headers=headers)
        self.assertEqual('', result)
        self.pop_sqs_to_worker()

        user.refresh()
        self.assertEqual('male', user.facebook_gender)
        self.assertEqual(True, user.show_gender_male)
        logs = list(FacebookLog.query(user.uuid))
        self.assertEqual(3, len(logs))
        self.assertDictEqual(json.loads(facebook.TEST_DATA[facebook.FB_URL][facebook_api_token]),
                             json.loads(logs[2].data))

        # Let's start over with no gender in facebook data, which is what a
        # custom gender would show as.
        # Clear the facebook id so we avoid a 409.
        user.facebook_id = None
        user.save()

        x = json.loads(facebook.TEST_DATA[facebook.FB_URL][facebook_api_token])
        del x['gender']
        facebook.TEST_DATA[facebook.FB_URL][facebook_api_token] = json.dumps(x)

        user = self.create_user(random_user_name=False,
                                facebook=False,
                                gender=None)
        headers = get_headers(user)

        data = json.dumps({'token': facebook_api_token})
        result = self.put204('/users/me/facebook',
                             data=data, headers=headers)
        self.assertEqual('', result)

        user.refresh()
        self.assertEqual(user.facebook_api_token, facebook_api_token)

        # The PUT puts a SQS message on the worker to query facebook data.
        self.assertIsNone(user.photo)

        # This calls the worker
        self.assertIsNone(user.facebook_gender)
        self.assertIsNone(user.show_gender_male)
        self.pop_sqs_to_worker()

        user.refresh()
        self.assertIsNotNone(user.photo)
        photo = get_photo(user.photo)
        self.assertIsNotNone(photo)
        self.assertTrue(photo.copy_complete)
        self.assertIsNone(user.facebook_gender)
        self.assertIsNone(user.show_gender_male)
        logs = list(FacebookLog.query(user.uuid))
        self.assertEqual(1, len(logs))
        self.assertDictEqual(json.loads(facebook.TEST_DATA[facebook.FB_URL][facebook_api_token]),
                             json.loads(logs[0].data))

        # Try illegal data.
        self.put400('/users/me/facebook', data='', headers=headers)

        # # Wrong content header
        # headers['Content-Type'] = 'text/plain; charset=utf-8'
        # rv = self.application.put('/users/me/facebook',
        #                           data=FB_TEST_TOKEN,
        #                           headers=headers)
        # self.assertEqual('415 UNSUPPORTED MEDIA TYPE', rv.status)

        # Try a user with an existing profile.
        # Request it not be clobbered, which is the default.
        user.facebook_id = None
        user.save()
        user = self.create_user(random_user_name=False, facebook=False)

        self.assertIsNone(user.photo)
        headers = headers_with_auth(user.uuid, user.token)
        headers.update(la_geo_header)
        user_photo1_uuid = self.photo_upload(set_as_profile_photo=True,
                                             headers=headers)
        user.refresh()
        self.assertIsNotNone(user.photo)

        self.assertEqual(0, len(get_queue().messages))
        data = json.dumps({'token': facebook_api_token})
        result = self.put204('/users/me/facebook',
                             data=data, headers=headers)
        self.assertEqual('', result)

        user.refresh()
        self.assertEqual(user.facebook_api_token, facebook_api_token)

        # The PUT puts a SQS message on the worker to query facebook data.
        self.assertEqual(1, len(get_queue().messages))
        self.assertEqual(user_photo1_uuid, user.photo)

        self.pop_sqs_to_worker()

        user.refresh()
        self.assertEqual(user_photo1_uuid, user.photo)
        logs = list(FacebookLog.query(user.uuid))
        self.assertEqual(1, len(logs))
        self.assertDictEqual(json.loads(facebook.TEST_DATA[facebook.FB_URL][facebook_api_token]),
                             json.loads(logs[0].data))

        # Try a user with an existing profile.
        # Request it not be clobbered, explicitly.
        user.facebook_id = None
        user.save()
        user = self.create_user(random_user_name=False, facebook=False)
        self.assertIsNone(user.photo)
        headers = headers_with_auth(user.uuid, user.token)
        headers.update(la_geo_header)
        user_photo1_uuid = self.photo_upload(set_as_profile_photo=True,
                                             headers=headers)
        user.refresh()
        self.assertIsNotNone(user.photo)

        self.assertEqual(0, len(get_queue().messages))
        data = json.dumps({'token': facebook_api_token,
                           'force_set_as_profile_photo': False})
        result = self.put204('/users/me/facebook',
                             data=data, headers=headers)
        self.assertEqual('', result)

        user.refresh()
        self.assertEqual(user.facebook_api_token, facebook_api_token)

        # The PUT puts a SQS message on the worker to query facebook data.
        self.assertEqual(1, len(get_queue().messages))
        self.assertEqual(user_photo1_uuid, user.photo)

        self.pop_sqs_to_worker()

        user.refresh()
        self.assertEqual(user_photo1_uuid, user.photo)
        logs = list(FacebookLog.query(user.uuid))
        self.assertEqual(1, len(logs))
        self.assertDictEqual(json.loads(facebook.TEST_DATA[facebook.FB_URL][facebook_api_token]),
                             json.loads(logs[0].data))

        # Try a user with an existing profile, request it be clobbered.
        user.facebook_id = None
        user.save()
        user = self.create_user(random_user_name=False, facebook=False)
        user.show_gender_male = False
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = la_location.uuid
        user.save()
        headers = headers_with_auth(user.uuid, user.token)
        headers.update(la_geo_header)
        user_photo1_uuid = self.photo_upload(set_as_profile_photo=True,
                                             headers=headers)
        user.refresh()
        self.assertIsNotNone(user.photo)

        self.assertEqual(0, len(get_queue().messages))
        data = json.dumps({'token': facebook_api_token,
                           'force_set_as_profile_photo': True})
        result = self.put204('/users/me/facebook',
                             data=data, headers=headers)
        self.assertEqual('', result)

        user.refresh()
        self.assertEqual(user.facebook_api_token, facebook_api_token)

        # The PUT puts a SQS message on the worker to query facebook data.
        self.assertEqual(1, len(get_queue().messages))
        self.assertEqual(user_photo1_uuid, user.photo)

        self.pop_sqs_to_worker()

        user.refresh()
        self.assertNotEqual(user_photo1_uuid, user.photo)
        self.assertIsNotNone(user.photo)
        logs = list(FacebookLog.query(user.uuid))
        self.assertEqual(1, len(logs))
        self.assertDictEqual(json.loads(facebook.TEST_DATA[facebook.FB_URL][facebook_api_token]),
                             json.loads(logs[0].data))

        # Confirm different user same id raises an error
        user2 = self.create_user(random_user_name=False,
                                 facebook=False)
        headers = get_headers(user2)

        data = json.dumps({'token': facebook_api_token})
        self.put409('/users/me/facebook', data=data, headers=headers,
                    error_message="InvalidAPIUsage: Already have user with this facebook id")


    def test_name_input(self):
        name = ''
        self.get404('/user_names/%s' % name)


    def test_check_name_missing(self):
        name = rand_string(5) + ' ' + rand_string(7) + ' ' + rand_string(4)
        self.get404('/user_names/%s' % name)


    def test_check_name_present(self):
        name = rand_string(5) + ' ' + rand_string(7) + ' ' + rand_string(4)
        user_uuid = uuid1()
        user_name = UserName(name, user_uuid=user_uuid)
        user_name.user_uuid = user_uuid
        user_name.save()
        self.get204('/user_names/%s' % name)


    def test_set_name(self):
        name = rand_string(5) + ' ' + rand_string(7) + ' ' + rand_string(4)

        data = json.dumps({
            'user_name': name
        })

        # Not logged in.
        headers = {'Content-Type': 'application/json'}
        self.patch401('/users/me', data=data, headers=headers)

        # Log in.
        user = self.create_user()
        headers = headers_with_auth(user.uuid, user.token)

        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(user.user_name, name)

        self.get204('/user_names/%s' % name)

        # Now try set to an existing name.
        user2 = self.create_user()
        headers2 = headers_with_auth(user2.uuid, user2.token)

        self.patch409('/users/me', data=data, headers=headers2)

        # Try illegal data.
        data = json.dumps({
            'user_name': 22
        })

        self.patch400('/users/me', data=data, headers=headers)

        # Try emoji
        name3 = name = rand_string(5) + u'OK HAND SIGN \U0001F44C'
        data = json.dumps({
            'user_name': name3
        })

        self.patch204('/users/me', data=data, headers=headers)

        result = self.get200('/users/me', headers=headers)
        self.assertEquals(name3, result[u'user_name'])

        # Wrong content header
        headers['Content-Type'] = 'text/plain; charset=utf-8'
        self.patch415('/users/me', data=data, headers=headers)

    def test_location_db_test(self):
        self.get200('/test_location_db')

    def test_set_location(self):
        geodata = '34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0'
        data = json.dumps({
            'location': geodata
        })

        # Not logged in.
        headers = {'Content-Type': 'application/json'}
        self.patch401('/users/me', data=data, headers=headers)

        # Log in.
        user = self.create_user()
        headers = headers_with_auth(user.uuid, user.token)

        result = self.patch200('/users/me', data=data, headers=headers)
        self.assertDictEqual({
            'location': la_location.uuid.hex,
            'location_name': la_location.accent_city
        }, result)

        user.refresh()
        self.assertAlmostEqual(user.lat, 34.33233141, places=4)
        self.assertAlmostEqual(user.lon, -118.0312186, places=4)
        self.assertEqual(user.geodata, geodata)
        self.assertEqual(user.location, la_location.uuid)

        # Try illegal data.
        bad_data = json.dumps({
            'location': 'around the corner and just down the way'
        })

        self.patch400('/users/me', data=bad_data, headers=headers)

        # Wrong content header
        headers['Content-Type'] = 'text/plain; charset=utf-8'
        self.patch415('/users/me', data=data, headers=headers)


    def test_set_view_gender(self):
        data = json.dumps({
            'view_gender': 'male'
        })

        # Not logged in.
        headers = {'Content-Type': 'application/json'}
        self.patch401('/users/me', data=data, headers=headers)

        # Log in.
        user = self.create_user()
        headers = headers_with_auth(user.uuid, user.token)

        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertTrue(user.view_gender_male)

        # Now try set to gender female
        data = json.dumps({
            'view_gender': 'female'
        })

        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertFalse(user.view_gender_male)

        # Try illegal data.
        bad_data = json.dumps({
            'gender': 'bazqux'
        })

        self.patch400('/users/me', data=bad_data, headers=headers)

        # Wrong content header
        headers['Content-Type'] = 'text/plain; charset=utf-8'
        self.patch415('/users/me', data=data, headers=headers)


    def test_set_first_name(self):
        first_name_1 = u'OK HAND SIGN \U0001F44C'
        data = json.dumps({
            'first_name': first_name_1
        })

        # Not logged in.
        headers = {'Content-Type': 'application/json'}
        self.patch401('/users/me', data=data, headers=headers)

        # Log in.
        user = self.create_user(first_name=None, facebook=None)
        self.assertIsNone(user.first_name)
        headers = headers_with_auth(user.uuid, user.token)

        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(user.first_name, first_name_1)

        first_name_2 = u'SIGN \U0001F44C OK HAND'
        data = json.dumps({
            'first_name': first_name_2
        })

        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(user.first_name, first_name_2)

        # Try illegal data.
        bad_data = json.dumps({
            'first_name': 12345
        })

        self.patch400('/users/me', data=bad_data, headers=headers)

        # Wrong content header
        headers['Content-Type'] = 'text/plain; charset=utf-8'
        self.patch415('/users/me', data=data, headers=headers)


    def test_set_last_name(self):
        last_name_1 = u'OK HAND SIGN \U0001F44C'
        data = json.dumps({
            'last_name': last_name_1
        })

        # Not logged in.
        headers = {'Content-Type': 'application/json'}
        self.patch401('/users/me', data=data, headers=headers)

        # Log in.
        user = self.create_user()
        self.assertEqual('Romanescu', user.last_name)
        headers = get_headers(user)

        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(user.last_name, last_name_1)

        last_name_2 = u'SIGN \U0001F44C OK HAND'
        data = json.dumps({
            'last_name': last_name_2
        })

        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(user.last_name, last_name_2)

        # Try illegal data.
        bad_data = json.dumps({
            'last_name': 12345
        })

        self.patch400('/users/me', data=bad_data, headers=headers)

        # Wrong content header
        headers['Content-Type'] = 'text/plain; charset=utf-8'
        self.patch415('/users/me', data=data, headers=headers)


    def test_set_gender(self):
        data = json.dumps({
            'gender': 'male'
        })

        # Not logged in.
        headers = {'Content-Type': 'application/json'}
        self.patch401('/users/me', data=data, headers=headers)

        # Log in.
        user = self.create_user()
        headers = headers_with_auth(user.uuid, user.token)

        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertTrue(user.show_gender_male)

        # Now try set to gender female
        data = json.dumps({
            'gender': 'female'
        })
        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertFalse(user.show_gender_male)

        # Try illegal data.
        bad_data = json.dumps({
            'gender': 'bazqux'
        })

        self.patch400('/users/me', data=bad_data, headers=headers)

        # Wrong content header
        headers['Content-Type'] = 'text/plain; charset=utf-8'
        self.patch415('/users/me', data=data, headers=headers)


    def test_get_user_by_facebook(self):
        headers = {'Content-Type': 'application/json'}
        facebook_id_1 = rand_string(20)
        facebook_api_token_1 = rand_string(200)
        facebook.add_test_data(facebook_id_1, facebook_api_token_1)
        facebook_id_2 = rand_string(20)
        facebook_api_token_2 = rand_string(200)
        facebook.add_test_data(facebook_id_2, facebook_api_token_2)

        # Our fake test facebook does not have this token or id
        url = '/users_by_facebook/%s?token=%s'
        self.get404(url % (facebook_id_1, facebook_api_token_1),
                    headers=headers,
                    error_message='NotFound: Could not find user with that facebook id')

        # Our fake test facebook has this token, but its for a different id.
        self.get401(url % (facebook_id_1, facebook_api_token_2),
                    headers=headers,
                    error_message='InsufficientAuthorization: Facebook token did not match facebook id')

        # There is no user in our database with this facebook_id
        self.get404(url % (facebook_id_1, facebook_api_token_1),
                    headers=headers,
                    error_message='NotFound: Could not find user with that facebook id')

        user = self.create_user(facebook=False)
        headers = get_headers(user)
        data = json.dumps({'token': facebook_api_token_1})
        self.put204('/users/me/facebook', data=data, headers=headers)
        self.pop_sqs_to_worker()

        result = self.get200(url % (facebook_id_1, facebook_api_token_1),
                             headers=headers)
        self.assertDictEqual({'uuid': str(user.uuid),
                              'token': user.token}, result)

    def test_get_user_data(self):
        user = create_user(user_agent='testuser agent')
        user.save()

        headers = get_headers(user)
        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need name',
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50.0,
        }, result)

        user.show_gender_male = False
        user.save()

        headers = get_headers(user)
        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need name',
            u'gender': u'female',
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50.0,
        }, result)

        user.view_gender_male = False
        user.save()

        headers = get_headers(user)
        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need name',
            u'gender': u'female',
            u'view_gender': u'female',
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50.0,
        }, result)

        user.first_name = 'test first name 1'
        user.save()

        headers = get_headers(user)
        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need name',
            u'gender': u'female',
            u'view_gender': u'female',
            u'uuid': unicode(user.uuid.hex),
            u'first_name': 'test first name 1',
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50.0,
        }, result)

        # post name
        user_name = u'foo%20test%20user' + rand_string(5)
        data = json.dumps({'user_name': user_name})
        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(user_name, user.user_name)
        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need facebook',
            u'gender': u'female',
            u'view_gender': u'female',
            u'user_name': user_name,
            u'first_name': 'test first name 1',
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50.0,
        }, result)

        # post biography
        bio = u'a test biography for a test user'
        data = json.dumps({'biography': bio})
        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(bio, user.biography)
        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need facebook',
            u'gender': u'female',
            u'view_gender': u'female',
            u'user_name': user_name,
            u'first_name': 'test first name 1',
            u'biography': bio,
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50.0,
        }, result)

        # post instagram
        instagram = u'instagramtestcode'
        data = json.dumps({'instagram': instagram})
        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(instagram, user.instagram)
        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need facebook',
            u'gender': u'female',
            u'view_gender': u'female',
            u'user_name': user_name,
            u'first_name': 'test first name 1',
            u'biography': bio,
            u'instagram': instagram,
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50.0,
        }, result)

        # post snapchat
        snapchat = u'snapchattestcode'
        data = json.dumps({'snapchat': snapchat})
        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(snapchat, user.snapchat)
        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need facebook',
            u'gender': u'female',
            u'view_gender': u'female',
            u'user_name': user_name,
            u'first_name': 'test first name 1',
            u'biography': bio,
            u'instagram': instagram,
            u'snapchat': snapchat,
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50.0,
        }, result)

        # post website
        website = u'http://example.com/foobar'
        data = json.dumps({'website': website})
        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(website, user.website)
        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need facebook',
            u'gender': u'female',
            u'view_gender': u'female',
            u'user_name': user_name,
            u'first_name': 'test first name 1',
            u'biography': bio,
            u'instagram': instagram,
            u'snapchat': snapchat,
            u'website': website,
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50,
        }, result)

        # post location, then re-get to make sure location is right
        data = json.dumps({'location': la_s})

        self.patch200('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertIsNotNone(user.location)

        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need facebook',
            u'location': unicode(user.location.hex),
            u'location_name': u'Los Angeles',
            u'gender': u'male' if user.show_gender_male else u'female',
            u'view_gender': u'male' if user.view_gender_male else u'female',
            u'user_name': user.user_name,
            u'first_name': 'test first name 1',
            u'biography': bio,
            u'instagram': instagram,
            u'snapchat': snapchat,
            u'website': website,
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50,
        }, result)

        # set a photo and re-check
        headers['Geo-Position'] = la_s
        user_photo1_uuid = self.photo_upload(set_as_profile_photo=True,
                                             headers=headers,
                                             tags=['#testtag1' + rand_string(4), '#testtag2' + rand_string(4)])
        user_photo2_uuid = self.photo_upload(set_as_profile_photo=True,
                                             headers=headers)
        user_photo3_uuid = self.photo_upload(set_as_profile_photo=True,
                                             headers=headers)

        user.refresh()
        self.assertIsNotNone(user.location)

        result = self.get200('/users/me', headers=headers)
        photos = result[u'photos']
        self.assertEqual(3, len(photos))
        self.maxDiff = None
        for i, user_photo_uuid in enumerate([user_photo3_uuid, user_photo2_uuid, user_photo1_uuid, user_photo3_uuid]):
            self.assertDictContainsSubset({
                u'location_name': u'Los Angeles',
                u'url_medium': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s%s_%s_480x480' % ('m' if user.show_gender_male else 'f', la_location.uuid.hex, user_photo_uuid.hex),
                u'url_small': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s%s_%s_240x240' % ('m' if user.show_gender_male else 'f', la_location.uuid.hex, user_photo_uuid.hex),
                u'url_large': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s%s_%s_960x960' % ('m' if user.show_gender_male else 'f', la_location.uuid.hex, user_photo_uuid.hex),
                u'score': 1500,
                u'location': unicode(la_location.uuid.hex),
                u'id': unicode(user_photo_uuid.hex),
                u'user': {
                    u'uuid':unicode(user.uuid.hex),
                    u'user_name': user.user_name,
                    u'first_name': 'test first name 1',
                    u'biography': bio,
                    u'website': u'http://example.com/foobar'
                }
            },
                (photos + [result[u'photo']])[i])

        del result[u'photos']
        del result[u'photo']
        self.assertDictEqual({
            u'registered': u'need facebook',
            u'location': user.location.hex,
            u'location_name': u'Los Angeles',
            u'gender': u'male' if user.show_gender_male else u'female',
            u'view_gender': u'male' if user.view_gender_male else u'female',
            u'user_name': user.user_name,
            u'first_name': 'test first name 1',
            u'biography': bio,
            u'instagram': instagram,
            u'snapchat': snapchat,
            u'website': website,
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50,
        }, result)

        # Have another user get this profile.
        user2 = create_user(user_agent='testuser agent2')
        user2.user_name = u'foo%20test%20user2'
        user2.view_gender_male = False
        user2.save()
        headers = headers_with_auth(user2.uuid, user2.token)
        gender_location = user.get_show_gender_location()

        result = self.get200('/users/%s' % user.uuid.hex, headers=headers)
        photos = result[u'photos']
        self.assertEqual(3, len(photos))
        for i, user_photo_uuid in enumerate([user_photo3_uuid, user_photo2_uuid, user_photo1_uuid, user_photo3_uuid]):
            self.assertDictContainsSubset({
                u'location_name': u'Los Angeles',
                u'url_medium': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s_%s_480x480' % (gender_location, user_photo_uuid.hex),
                u'url_small': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s_%s_240x240' % (gender_location, user_photo_uuid.hex),
                u'url_large': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s_%s_960x960' % (gender_location, user_photo_uuid.hex),
                u'score': 1500,
                u'location': unicode(la_location.uuid.hex),
                u'id': unicode(user_photo_uuid.hex),
                u'user': {
                    u'uuid': unicode(user.uuid.hex),
                    u'user_name': user.user_name,
                    u'first_name': 'test first name 1',
                    u'biography': bio,
                    u'website': u'http://example.com/foobar'
                }},
                (photos + [result[u'photo']])[i])

        del result[u'photos']
        del result[u'photo']
        self.assertDictEqual({
            u'user_name': user.user_name,
            u'first_name': 'test first name 1',
            u'biography': bio,
#            u'instagram': instagram,   # These are missing because requesting
#            u'snapchat': snapchat,     # user is not registration=='ok'
            u'website': website,
            u'uuid': unicode(user.uuid.hex),
        }, result)

        # Can't update other user's stuff
        data = json.dumps({'first_name': 'fake_new_first_name'})
        self.patch409('/users/%s' % user.uuid.hex,
                      data=data, headers=headers)

        # Have another user get this profile.
        user2 = create_user(user_agent='testuser agent2')
        user2.user_name = u'foo%20test%20user2'
        user2.view_gender_male = False
        user2.save()
        headers = headers_with_auth(user2.uuid, user2.token)

    def test_user_photo_pagination(self):
        # Make a test user with 30 photos, make sure GET /users/me has only 20.
        user = self.create_user()
        headers = get_headers(user)
        photo_uuids = []
        for i in range(30):
            photo_uuid = self.photo_upload(headers)
            photo_uuids.append(photo_uuid)
        photo_uuids = list(reversed(photo_uuids))  # Most recent first.

        result = self.get200('/users/me', headers=headers)
        self.assertIn('photos', result)
        got_photos = result['photos']
        self.assertEqual(20, len(got_photos))
        for got, check_uuid in zip(got_photos, photo_uuids):
            self.assertEqual(got['id'], check_uuid.hex)

        result = self.get200('/users/me/photos', headers=headers)
        self.assertEqual(len(result), 25)
        for got, check_uuid in zip(result, photo_uuids):
            self.assertEqual(got['id'], check_uuid.hex)

        result = self.get200('/users/me/photos?count=2', headers=headers)
        self.assertEqual(len(result), 2)
        for got, check_uuid in zip(result, photo_uuids):
            self.assertEqual(got['id'], check_uuid.hex)

        result = self.get200('/users/me/photos?count=5', headers=headers)
        self.assertEqual(len(result), 5)
        for got, check_uuid in zip(result, photo_uuids):
            self.assertEqual(got['id'], check_uuid.hex)

        result = self.get200(
                '/users/me/photos?exclusive_start_key={}'.format(photo_uuids[3].hex),
                headers=headers)
        for got, check_uuid in zip(result, photo_uuids[4:]):
            self.assertEqual(got['id'], check_uuid.hex)

        result = self.get200(
                '/users/me/photos?exclusive_start_key={}&count=3'.format(photo_uuids[3].hex),
                headers=headers)
        self.assertEqual(len(result), 3)
        for got, check_uuid in zip(result, photo_uuids[4:]):
            self.assertEqual(got['id'], check_uuid.hex)

        result = self.get200('/users/%s' % user.uuid.hex, headers=headers)

        # Try with the uuid, from another user.
        user2 = self.create_user()
        headers = get_headers(user2)
        url = '/users/{}/photos'.format(user.uuid.hex)

        result = self.get200(url, headers=headers)
        self.assertEqual(len(result), 25)
        for got, check_uuid in zip(result, photo_uuids):
            self.assertEqual(got['id'], check_uuid.hex)

        result = self.get200(url + '?count=2', headers=headers)
        self.assertEqual(len(result), 2)
        for got, check_uuid in zip(result, photo_uuids):
            self.assertEqual(got['id'], check_uuid.hex)

        result = self.get200(url + '?count=5', headers=headers)
        self.assertEqual(len(result), 5)
        for got, check_uuid in zip(result, photo_uuids):
            self.assertEqual(got['id'], check_uuid.hex)

        result = self.get200(url + '?exclusive_start_key={}'.format(photo_uuids[3].hex), headers=headers)
        for got, check_uuid in zip(result, photo_uuids[4:]):
            self.assertEqual(got['id'], check_uuid.hex)

        result = self.get200(url + '?exclusive_start_key={}&count=3'.format(photo_uuids[3].hex), headers=headers)
        self.assertEqual(len(result), 3)
        for got, check_uuid in zip(result, photo_uuids[4:]):
            self.assertEqual(got['id'], check_uuid.hex)

    def test_delete_user(self):
        headers = {'Content-Type': 'application/json',
                   'User-Agent': 'test user agent'}
        headers.update(la_geo_header)
        result = self.post201('/users', headers=headers)
        self._assert_create_user(result)
        user_uuid = UUID(result['uuid'])
        user = User.get(user_uuid)

        headers = get_headers(user)
        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need name',
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'test user agent',
            u'win_loss_ratio': 50.0,
        }, result)

        rv = self.application.delete(app_url('/users/me'), headers=headers)
        self.assert204(rv)

        self.get401('/users/me', headers=headers)

        # Now test a fully registered user with a name and a facebook id.
        headers = {'Content-Type': 'application/json',
                   'User-Agent': 'test user agent'}
        headers.update(la_geo_header)
        result = self.post201('/users', headers=headers)
        self._assert_create_user(result)
        user_uuid = UUID(result['uuid'])
        user = User.get(user_uuid)
        self.assertEqual('test user agent', user.user_agent)

        headers = get_headers(user)
        facebook_id = rand_string(20)
        facebook_api_token = rand_string(200)
        facebook.add_test_data(facebook_id, facebook_api_token)
        data = json.dumps({'token': facebook_api_token})
        self.put204('/users/me/facebook', data=data, headers=headers)
        self.pop_sqs_to_worker()

        user.refresh()
        self.assertTrue(user.auth_enabled)
        self.assertEqual(user.facebook_id, facebook_id)

        user_name = u'foo test user' + rand_string(5)
        # TODO: We have an issue with escaped strings?
#        user_name = u'foo%20test%20user' + rand_string(5)
        rv = self.application.get('/user_names/%s' % user_name,
                                  headers=headers)
        self.assertEqual('404 NOT FOUND', rv.status)

        data = json.dumps({'user_name': user_name})
        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(user.user_name, user_name)
        self.get204('/user_names/%s' % user_name, headers=headers)

        # Try illegal input
        rv = self.application.delete(app_url('/users/%s' % user.uuid.hex),
                                     headers=headers)
        self.assertEqual("409 CONFLICT", rv.status)

        # Delete the user.
        rv = self.application.delete(app_url('/users/me'), headers=headers)
        self.assert204(rv)

        self.get401('/users/me', headers=headers)

        #user.refresh()  # Don't know why, but this doesn't work.
        user = User.get(user_uuid)

        self.assertFalse(user.auth_enabled)
        self.assertIsNone(user.user_name)
        self.assertIsNone(user.facebook_id)

        self.get404('/user_names/%s' % user_name, headers=headers)

    def test_photo_has_no_name_if_null(self):
        user = create_user(user_agent='testuser agent')
        user.show_gender_male = False
        user.view_gender_male = False
        update_registration_status(user)
        user.save()

        headers = get_headers(user)
        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need name',
            u'gender': u'male' if user.show_gender_male else u'female',
            u'view_gender': u'male' if user.view_gender_male else u'female',
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'win_loss_ratio': 50.0,
        }, result)

        # post location, then re-get to make sure location is right
        data = json.dumps({'location': la_s})
        self.patch200('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertIsNotNone(user.location)

        result = self.get200('/users/me', headers=headers)
        self.assertDictEqual({
            u'registered': u'need name',
            u'gender': u'male' if user.show_gender_male else u'female',
            u'view_gender': u'male' if user.view_gender_male else u'female',
            u'uuid': unicode(user.uuid.hex),
            u'user_agent': u'testuser agent',
            u'location': unicode(la_location.uuid.hex),
            u'location_name': unicode('Los Angeles'),
            u'win_loss_ratio': 50.0,
        }, result)

        # set a photo
        headers.update(la_geo_header)
        user_photo_uuid = self.photo_upload(set_as_profile_photo=True, headers=headers)

        result = self.get200('/users/me', headers=headers)
        photos = result[u'photos']
        self.assertEqual(1, len(photos))
        self.assertDictEqual(result['photo'], result['photos'][0])

        photo = result['photo']
        self.assertDictContainsSubset({
            u'location_name': u'Los Angeles',
            u'url_medium': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s%s_%s_480x480' % ('m' if user.show_gender_male else 'f', la_location.uuid.hex, user_photo_uuid.hex),
            u'url_small': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s%s_%s_240x240' % ('m' if user.show_gender_male else 'f', la_location.uuid.hex, user_photo_uuid.hex),
            u'url_large': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s%s_%s_960x960' % ('m' if user.show_gender_male else 'f', la_location.uuid.hex, user_photo_uuid.hex),
            u'score': 1500,
            u'location': unicode(la_location.uuid.hex),
            u'id': unicode(user_photo_uuid.hex),
            u'user': {
                u'uuid':unicode(user.uuid.hex)
            }
        },
        photo)

        # post name
        data = json.dumps({'user_name': u'foo%20test%20user' + rand_string(5)})
        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertIsNotNone(user.user_name)

        result = self.get200('/users/me', headers=headers)
        photos = result[u'photos']
        self.assertEqual(1, len(photos))
        self.assertDictEqual(result['photo'], result['photos'][0])

        photo = result['photo']
        self.assertDictContainsSubset({
            u'location_name': u'Los Angeles',
            u'url_medium': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s%s_%s_480x480' % ('m' if user.show_gender_male else 'f', la_location.uuid.hex, user_photo_uuid.hex),
            u'url_small': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s%s_%s_240x240' % ('m' if user.show_gender_male else 'f', la_location.uuid.hex, user_photo_uuid.hex),
            u'url_large': u'https://s3-us-west-2.amazonaws.com/localpictourney-serve/%s%s_%s_960x960' % ('m' if user.show_gender_male else 'f', la_location.uuid.hex, user_photo_uuid.hex),
            u'score': 1500,
            u'location': la_location.uuid.hex,
            u'id': user_photo_uuid.hex,
            u'user': {
                u'uuid':unicode(user.uuid.hex),
                u'user_name': user.user_name
            }
        },
        photo)

    def test_change_user_token(self):
        new_token = '12345'
        data = json.dumps({
            'token': new_token
        })

        # Not logged in.
        headers = {'Content-Type': 'application/json'}
        self.patch401('/users/me', data=data, headers=headers)

        # Log in.
        user = self.create_user()
        headers = headers_with_auth(user.uuid, user.token)

        self.patch204('/users/me', data=data, headers=headers)

        user.refresh()
        self.assertEqual(user.token, new_token)

        # Test if new token changes auth situation.
        self.get401('/test_auth', headers=headers)

        user.refresh()

        headers = get_headers(user)
        self.get200('/test_auth', headers=headers)

        # Try illegal data.
        bad_data = json.dumps({
            'token': 22
        })

        self.patch400('/users/me', data=bad_data, headers=headers)

        # Wrong content header
        headers['Content-Type'] = 'text/plain; charset=utf-8'
        self.patch415('/users/me', data=data, headers=headers)

    def test_activity_feed(self):
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'test user agent'
        }
        user = self.create_user()
        user_headers = get_headers(user)

        result = self.get200('/users/me/activity', headers=user_headers)
        self.assertEqual(1, len(result))
        self.assert_feed_joined(result[0])
        self.assertIsNotNone(result[0]['created_on'])
        result = self.get200('/users/me/activity', headers=user_headers)
        self.assertEqual(1, len(result))
        self.assert_feed_joined(result[0], read=True)
        self.assertIsNotNone(result[0]['created_on'])

        # Follow somebody and have them post an image. It should not appear
        # in your activity (but it should appear in your feed.)
        followed = self.create_user()
        # result = self.post201('/users', headers=headers)
        # self._assert_create_user(result)
        # followed_uuid = UUID(result['uuid'])
        # followed = User.get(followed_uuid)
        followed_headers = get_headers(followed)

        data = json.dumps({'followed': followed.uuid.hex})
        self.post200('/users/me/following', data=data, headers=user_headers)

        geodata = '34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0'
        data = json.dumps({
            'location': geodata,
            'gender': 'male'
        })
        self.patch200('/users/me', data=data, headers=followed_headers)

        followed_headers['Geo-Position'] = geodata
        self.photo_upload(headers=followed_headers,
                          set_as_profile_photo=True,
                          enter_in_tournament=True)

        result = self.get200('/users/me/activity', headers=user_headers)
        self.assertEqual(1, len(result))
        self.assert_feed_joined(result[0], read=True)
        self.assertIsNotNone(result[0]['created_on'])


    # def test_awards(self):
    #     user = self.create_user()
    #     headers = get_headers(user)
    #
    #     data = json.dumps({'categories': {default_category: True}})
    #     self.patch204('/users/me', data=data, headers=headers)
    #
    #     photo_uuid = self.photo_upload(headers, default_category)
    #
    #     url = '/photos/{photo_uuid}/awards'.format(photo_uuid=photo_uuid.hex)
    #     self.get401(url)
    #
    #     result = self.get200(url, headers=headers)
    #     self.assertEqual(0, len(result))
    #
    #     photo = get_photo(photo_uuid)
    #     self.assertIsNotNone(photo)
    #     from awards import create_award
    #     create_award(photo_uuid, 'TestAward')
    #
    #     result = self.get200(url, headers=headers)
    #     self.assertEqual(1, len(result))
    #     award = result[0]
    #
    #     self.assertIn('uuid', award)
    #     self.assertEqual(photo_uuid.hex, award['photo_uuid'])
    #     self.assertEqual('TestAward', award['kind'])
    #     self.assertIn('awarded_on', award)
    #
    #     self.assertEqual(1, len(photo.get_awards()))


    # -- Model ----------------------------------------------------------------

    def test_model_race(self):
        name = rand_string(5) + ' ' + rand_string(7) + ' ' + rand_string(4)
        user_uuid_1 = uuid1()
        user_uuid_2 = uuid1()

        user_name_1 = UserName(name, user_uuid=user_uuid_1)
        user_name_1.user_uuid = user_uuid_1

        user_name_2 = UserName(name, user_uuid=user_uuid_2)
        user_name_2.user_uuid = user_uuid_2

        user_name_1.save()
        user_name_2.save()

        user_name_1.refresh()
        user_name_2.refresh()

        self.assertEqual(name, user_name_1.name)
        self.assertEqual(name, user_name_2.name)
        # Note: Last one to save wins the race.
        self.assertEqual(user_uuid_2, user_name_1.user_uuid)
        self.assertEqual(user_uuid_2, user_name_2.user_uuid)

    # -- Photo ----------------------------------------------------------------

    def disabled_photo_crop(self):
        # This is disabled until we put in a mock for s3.
        original_path = './69635f54b15411e4a19fc8e0eb16059b'
        thumb_path = '%s_t' % original_path
        self.assertTrue(os.path.exists(original_path))
        crop(original_path)
        self.assertTrue(os.path.exists(thumb_path))
        try:
            os.remove(thumb_path)
        except IOError:
            pass

    def test_get_photo_url(self):
        # If not logged in, 401.
        self.post401('/photos',
                     headers={'Content-Type': 'application/json'})

        # Log in.
        user = self.create_user(random_user_name=False, facebook=False)
        user.show_gender_male = True
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = la_location.uuid
        user.save()
        headers = headers_with_auth(user.uuid, user.token)
        headers.update(la_geo_header)

        self.assertIsNone(user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertNotIn('photo', result)

        data = json.dumps({'set_as_profile_photo': True})
        result = self.post202('/photos', data=data, headers=headers)
        self.assertIn('id', result)
        self.assertIn('share_url', result)
        self.assertIn('post_form_args', result)

        post_form_args = result['post_form_args']

        self.assertIn('fields', post_form_args)
        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)

        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy", "value": "hnhdnhdnhdnhdnhdnhdndhtdutdu="},
                {"name": "AWSAccessKeyId", "value": "12345"},
                {"name": "signature", "value": "123456="},
                {"name": "key", "value": key_value}]}

        self.assertDictEqual(
            check_result,
            post_form_args)

        self.assertIsNone(user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertNotIn('photo', result)

        # Error messages
        no_gender = "InvalidAPIUsage: Cannot enter tournament with empty user gender"
        no_user_location = 'InvalidAPIUsage: Cannot post photo with empty user location'
        no_location_header = "InvalidAPIUsage: Geo-Position header not present"

        # This user has no gender.
        user = self.create_user(gender=None, geodata=None)
        user.save()
        headers = headers_with_auth(user.uuid, user.token)

        # This post has no location header.
        # With a user location, reg status is not 'ok' and the user is not
        # authorized to post.
        result = self.post401('/photos', headers=headers)
        # result = self.post409('/photos', headers=headers)
        # self.assertIn('message', result)
        # self.assertIn(result['message'],
        #               [no_gender, no_user_location, no_location_header])

        import copy
        loc_headers = copy.deepcopy(headers)
        loc_headers.update(la_geo_header)

        # Has location header, but user has no gender and no location.
        result = self.post401('/photos', headers=headers)
        # result = self.post409('/photos', headers=loc_headers)
        # self.assertIn('message', result)
        # self.assertIn(result['message'], [no_gender, no_user_location])

        user.show_gender_male = False
        user.save()

        # This post has no location header.
        result = self.post401('/photos', headers=headers)
        # result = self.post409('/photos', headers=headers)
        # self.assertIn('message', result)
        # self.assertIn(result['message'], [no_user_location, no_location_header])

        # Has location header, but user has no location.
        result = self.post401('/photos', headers=headers)
        # result = self.post409('/photos', headers=loc_headers)
        # self.assertIn('message', result)
        # self.assertIn(result['message'], [no_user_location])

        data = json.dumps({'gender': 'female', 'location': la_s})
        self.patch200('/users/me', data=data, headers=headers)

        # This post has no location header.
        result = self.post409('/photos', headers=headers)
        self.assertIn('message', result)
        self.assertIn(result['message'], [no_gender, no_location_header])

        # Has location header, but user has no gender.
        user.show_gender_male = None
        user.save()
        result = self.post401('/photos', headers=loc_headers)
        # result = self.post409('/photos', headers=loc_headers)
        # self.assertIn('message', result)
        # self.assertIn(result['message'], [no_gender])

        user.show_gender_male = False
        user.save()

        # This post has no location header.
        result = self.post401('/photos', headers=headers)
        # self.assertIn('message', result)
        # self.assertIn(result['message'], [no_location_header])

    def test_photo_upload_flags(self):
        user = self.create_user(gender='female', facebook=False)
        headers = get_headers(user)

        # This creates the photo object in DynamoDB, and returns a URL for
        # uploading the photo to s3.
        data = json.dumps({'enter_in_tournament': True,
                           'set_as_profile_photo': True})
        result = self.post202('/photos', data=data, headers=headers)
        self.assertIn('id', result)
        self.assertIn('share_url', result)
        self.assertIn('post_form_args', result)
        post_form_args = result['post_form_args']

        self.assertIn('fields', post_form_args)
        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)
        log.info("key value %s" % key_value)

        # This is a tournament photo, so the first character of the file name
        # is based on the user's gender.
        self.assertEqual('f', key_value[0])
        photo_uuid = UUID(key_value.split('_')[1])

        photo = get_photo(photo_uuid, check_copy_complete=False)
        self.assertIsNotNone(photo)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)
        self.assertEqual(photo.media_type, 'photo')
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        #self.assertEqual(None, user.photo)

        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy", "value": "aseotuhasoetuhasotehusathoeu="},
                {"name": "AWSAccessKeyId", "value": "12312312312"},
                {"name": "signature", "value": "123123123123123="},
                {"name": "key", "value": key_value}]}

        self.assertDictEqual(
            check_result,
            post_form_args)

        # set_as_profile_photo=True
        user = self.create_user(gender='female', facebook=False)
        headers = get_headers(user)

        self.assertIsNone(user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertNotIn('photo', result)

        # This creates the photo object in DynamoDB, and returns a URL for
        # uploading the photo to s3.
        data = json.dumps({'set_as_profile_photo': True})
        result = self.post202('/photos', data=data, headers=headers)
        self.assertIn('id', result)
        self.assertIn('share_url', result)
        self.assertIn('post_form_args', result)
        post_form_args = result['post_form_args']

        self.assertIn('fields', post_form_args)
        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)
        log.info("key value %s" % key_value)

        self.assertEqual('f', key_value[0])
        photo_uuid = UUID(key_value.split('_')[1])

        photo = get_photo(photo_uuid, check_copy_complete=False)
        self.assertIsNotNone(photo)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)
        self.assertEqual(photo.media_type, 'photo')

        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy", "value": "asoethusaoteuhsatoeuh="},
                {"name": "AWSAccessKeyId", "value": "aoesuthaoseuthasoteuh"},
                {"name": "signature", "value": "asoetuhasoteuhasoteuasotehu="},
                {"name": "key", "value": key_value}]}

        self.assertDictEqual(
            check_result,
            post_form_args)

        user.refresh()
        self.assertIsNone(user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertNotIn('photo', result)

        # Try with enter_in_tournament=False
        data = json.dumps({
            'set_as_profile_photo': True,
            'enter_in_tournament': False,
            'media_type': 'photo'})
        result = self.post202('/photos', data=data, headers=headers)
        self.assertIn('id', result)
        self.assertIn('share_url', result)
        self.assertIn('post_form_args', result)
        post_form_args = result['post_form_args']

        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)
        log.info("key value %s" % key_value)

        # This is a profile-only photo, so it starts with 'pop'
        self.assertEqual('pop', key_value[0:3])
        photo_uuid = UUID(key_value.split('_')[1])

        photo = get_photo(photo_uuid, check_copy_complete=False)
        self.assertIsNotNone(photo)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)
        self.assertEqual(photo.media_type, 'photo')
        user.refresh()
        self.assertIsNone(user.photo)

        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy", "value": "aoeutshaoesuthaosetuhasoetuhasoteuhsatoheu="},
                {"name": "AWSAccessKeyId", "value": "aoesuthasoeuthasoetuh"},
                {"name": "signature", "value": "aoesuthasoeuthasoetuh="},
                {"name": "key", "value": key_value}]}

        self.assertDictEqual(
            check_result,
            post_form_args)

        # Test the error if it's not profile and not tournament
        data = json.dumps({
            'set_as_profile_photo': False,
            'enter_in_tournament': False})
        self.post409('/photos', data=data, headers=headers,
                     error_message="InvalidAPIUsage: set_as_profile_photo and/or enter_in_tournament must be True")

        # Create a test photo. It should be marked as a test photo in the db.
        data = json.dumps({
            'set_as_profile_photo': True,
            'enter_in_tournament': True})
        result = self.post202('/photos?is_test=True',
                              data=data, headers=headers)
        post_form_args = result['post_form_args']
        self.assertIn('fields', post_form_args)
        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)

        # Let's inspect the photo in DynamoDB. It should exist, but have
        # flags showing it is not uploaded or processed. The user should still
        # show no photo.
        photo_uuid = UUID(key_value.split('_')[1])
        photo = get_photo(photo_uuid, check_copy_complete=False)
#        self.worker_process_photo(key_value)

 #       photo.refresh()
        self.assertTrue(photo.is_test)

        # Check media_type = 'movie'
        data = json.dumps({'set_as_profile_photo': True,
                           'media_type': 'movie'})
        result = self.post202('/photos', data=data, headers=headers)
        self.assertIn('id', result)
        self.assertIn('share_url', result)
        self.assertIn('post_form_args', result)
        post_form_args = result['post_form_args']

        self.assertIn('fields', post_form_args)
        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)
        log.info("key value %s" % key_value)

        self.assertEqual('f', key_value[0])
        photo_uuid = UUID(key_value.split('_')[1])

        photo = get_photo(photo_uuid, check_copy_complete=False)
        self.assertIsNotNone(photo)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)
        self.assertEqual(photo.media_type, 'movie')

        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy",
                 "value": "aoeutshaoesuthaosetuhasoetuhasoteuhsatoheu="},
                {"name": "AWSAccessKeyId", "value": "aoesuthasoeuthasoetuh"},
                {"name": "signature", "value": "aoesuthasoeuthasoetuh="},
                {"name": "key", "value": key_value}]}

        self.assertDictEqual(
            check_result,
            post_form_args)

    def test_model_photo_gender_tag(self):
        from model import PhotoGenderTag
        photo_uuid_1 = uuid1()
        photo_uuid_2 = uuid1()
        photo_uuid_3 = uuid1()
        score_1 = 1622.14
        score_2 = 1555.55
        score_3 = 923.83
        gender_tag_1 = u'f_#' + rand_string(8)
        gender_tag_2 = u'f_#123' + rand_string(6)
        gender_tag_3 = u'f_#\U0001F44C' + rand_string(6)

        # Photo 1 has tags 1, 2, photo 2 has tags 2, 3, photo 3 has tag 3
        photo_1_tags = [gender_tag_1, gender_tag_2]
        p_1_t_1 = PhotoGenderTag(gender_tag_1, uuid=photo_uuid_1,
                                 score=score_1,
                                 tag_with_case=gender_tag_1.lower())
        p_1_t_1.save()
        p_1_t_2 = PhotoGenderTag(gender_tag_2, uuid=photo_uuid_1,
                                 score=score_1,
                                 tag_with_case=gender_tag_2.lower())
        p_1_t_2.save()
        photo_2_tags = [gender_tag_2, gender_tag_3]
        p_2_t_2 = PhotoGenderTag(gender_tag_2, uuid=photo_uuid_2,
                                 score=score_2,
                                 tag_with_case=gender_tag_2.lower())
        p_2_t_2.save()
        p_2_t_3 = PhotoGenderTag(gender_tag_3, uuid=photo_uuid_2,
                                 score=score_2,
                                 tag_with_case=gender_tag_3.lower())
        p_2_t_3.save()
        photo_3_tags = [gender_tag_3]
        p_3_t_3 = PhotoGenderTag(gender_tag_3, uuid=photo_uuid_3,
                                 score=score_3,
                                 tag_with_case=gender_tag_3.lower())
        p_3_t_3.save()

        def to_tup(i):
            return (i.gender_tag, i.uuid, i.score)

        from model import PhotoGenderTagByScore
        t_1 = PhotoGenderTagByScore.query(gender_tag_1,
                                          scan_index_forward=False)

        t_1 = list(t_1)
        self.assertEqual([to_tup(p_1_t_1)], [to_tup(x) for x in t_1])

        t_2 = PhotoGenderTagByScore.query(gender_tag_2,
                                          scan_index_forward=False)

        t_2 = list(t_2)
        self.assertEqual([to_tup(p_1_t_2), to_tup(p_2_t_2)], [to_tup(x) for x in t_2])

        t_3 = PhotoGenderTagByScore.query(gender_tag_3,
                                          scan_index_forward=False)
        t_3 = list(t_3)
        self.assertEqual([to_tup(p_2_t_3), to_tup(p_3_t_3)], [to_tup(x) for x in t_3])

        # Change Photo 2's score,
        photo_2_score_new = 1800.07
        for tag in photo_2_tags:
            photo_gender_tag = PhotoGenderTag.get(tag, photo_uuid_2)
            photo_gender_tag.score = photo_2_score_new
            photo_gender_tag.save()

        p_1_t_1.refresh()
        p_1_t_2.refresh()
        p_2_t_2.refresh()
        p_2_t_3.refresh()
        p_3_t_3.refresh()
        t_2 = PhotoGenderTagByScore.query(gender_tag_2,
                                          scan_index_forward=False)
        t_2 = list(t_2)
        self.assertEqual([to_tup(p_2_t_2), to_tup(p_1_t_2)], [to_tup(x) for x in t_2])

        t_3 = PhotoGenderTagByScore.query(gender_tag_3,
                                          scan_index_forward=False)
        t_3 = list(t_3)
        self.assertEqual([to_tup(p_2_t_3), to_tup(p_3_t_3)], [to_tup(x) for x in t_3])


    def test_photo_tags(self):
        user = self.create_user(gender='female')
        headers = get_headers(user)

        tags = [u'##tag1', u'OK HAND SIGN \U0001F44Cxo', u'fooBAR']
        photo_uuid = self.photo_upload(headers=headers, tags=tags)

        photo = get_photo(photo_uuid)
        self.assertSetEqual(set(tags), photo.tags)

        photo_rendering = self.get200('/photos/{}'.format(photo_uuid.hex),
                                      headers=headers)
        self.assertEqual(set(tags),
                         set(photo_rendering['tags']))


    def test_tag_lists(self):
        user = self.create_user(gender='female')
        headers = get_headers(user)

        import tags
        for tag in tags.TOP_TAGS_MALE + tags.TOP_TAGS_FEMALE:
            for i in range(4):
                self.photo_upload(headers=headers, tags=[tag])

        self.get401('/tags/g')

        self.get404('/tags/g', headers=headers)

        self.get401('/tags/m')

        result = self.get200('/tags/m', headers=headers)
        found_tags = {x['tag'] for x in result}
        import tags
        self.assertSetEqual({x for x in tags.TOP_TAGS_MALE}, found_tags)

        self.get401('/tags/f')

        result = self.get200('/tags/f', headers=headers)
        found_tags = {x['tag'] for x in result}
        self.assertSetEqual({x for x in tags.TOP_TAGS_FEMALE}, found_tags)


    def test_tag_photos(self):
        tag = u'#OK HAND SIGN \U0001F44Cxo' + rand_string(5)

        self.get401('/tags/f/{}'.format(tag))

        user = self.create_user(gender='female')
        headers = get_headers(user)

        result = self.get200('/tags/f/{}'.format(tag), headers=headers)
        self.assertListEqual([], result)

        photo_uuids = {
            self.photo_upload(headers=headers, tags=[tag]) for x in range(5)
        }

        result = self.get200('/tags/f/{}'.format(tag), headers=headers)
        check_uuids = {x['id'] for x in result}
        self.assertSetEqual({p.hex for p in photo_uuids}, check_uuids)

        result = self.get200('/tags/f/{}?exclusive_start_key={}&count=2'.format(tag, result[0]['id']),
                             headers=headers)
        self.assertEqual(2, len(result))

        tag_2 = '#OTHERtesttag' + rand_string(5)
        for photo_uuid in photo_uuids:
            self.put204('/tags/{}/{}'.format(tag_2, photo_uuid),
                        headers=headers)

        result = self.get200('/tags/f/{}'.format(tag_2), headers=headers)
        check_uuids = {x['id'] for x in result}
        self.assertSetEqual({p.hex for p in photo_uuids}, check_uuids)

        user_2 = self.create_user(gender='female')
        headers_2 = get_headers(user_2)

        tag_3 = '#ThirdTESTtag' + rand_string(5)
        self.put401('/tags/{}/{}'.format(tag_3, list(photo_uuids)[0]),
                    headers=headers_2)


    def test_tag_trends(self):
        self.reset_model()
        get_kinesis().reset(settings.TAG_TREND_STREAM)
        from logic import tags
        tags.do_tag_trends()

        self.get401('/tags/trending/m')
        self.get401('/tags/trending/f')

        user = self.create_user(gender='female')
        headers = get_headers(user)

        result = self.get200('/tags/trending/m', headers=headers)
        self.assertEqual([], result)
        result = self.get200('/tags/trending/f', headers=headers)
        self.assertEqual([], result)

        tag_1 = u'test3' + rand_string(5)
        tag_2 = u'test2' + rand_string(5)
        tag_3 = u'OK HAND SIGN \U0001F44Cxo' + rand_string(5)

        tag_1_count = 3
        tag_2_count = 5
        tag_3_count = 8

        check_photo_uuid = None  # To test adding to a photo with existing tags.
        for i in range(tag_1_count):
            self.photo_upload(headers=headers, tags=[tag_1])
        for i in range(tag_2_count):
            check_photo_uuid = self.photo_upload(headers=headers, tags=[tag_2])
        for i in range(tag_3_count):
            self.photo_upload(headers=headers, tags=[tag_3])

        photo_uuid = self.photo_upload(headers=headers)
        self.put204('/tags/{}/{}'.format(tag_1, photo_uuid), headers=headers)
        self.put204('/tags/{}/{}'.format(tag_2, photo_uuid), headers=headers)
        self.put204('/tags/{}/{}'.format(tag_3, check_photo_uuid), headers=headers)

        tag_1_count += 1
        tag_2_count += 1
        tag_3_count += 1

        tags.do_tag_trends()

        result = self.get200('/tags/trending/m', headers=headers)
        self.assertEqual([], result)
        result = self.get200('/tags/trending/f', headers=headers)
        self.assertEqual([{'gender': 'f', 'tag': tag_3.lower(), 'count': unicode(tag_3_count)},
                          {'gender': 'f', 'tag': tag_2.lower(), 'count': unicode(tag_2_count)},
                          {'gender': 'f', 'tag': tag_1.lower(), 'count': unicode(tag_1_count)}
                          ], result)

        import urllib
        f = {'exclusive_start_key': tag_3.encode('utf-8'), 'count': "1"}
        query = urllib.urlencode(f)
        result = self.get200('/tags/trending/f?{}'.format(query),
                             headers=headers)
        self.assertEqual([{'gender': 'f', 'tag': tag_2.lower(), 'count': str(tag_2_count)}], result)

    def test_tag_search(self):
        self.reset_model()
        from logic import search
        search._drop_search_db()
        search._init_search_db()
        self.get401('/tags/search/f/foobar')

        user = self.create_user(gender='female')
        headers = get_headers(user)

        result = self.get404('/tags/search/x/foobar', headers=headers)

        result = self.get200('/tags/search/f/foobar', headers=headers)

        tag_1 = u'test3' + rand_string(5)
        tag_2 = u'test2' + rand_string(5)
        tag_3 = u'OK HAND SIGN \U0001F44Cxo' + rand_string(5)

        tag_1_count = 3
        tag_2_count = 5
        tag_3_count = 8

        check_photo_uuid = None  # To test adding to a photo with existing tags.
        for i in range(tag_1_count):
            self.photo_upload(headers=headers, tags=[tag_1])
        for i in range(tag_2_count):
            check_photo_uuid = self.photo_upload(headers=headers, tags=[tag_2])
        for i in range(tag_3_count):
            self.photo_upload(headers=headers, tags=[tag_3])

        photo_uuid = self.photo_upload(headers=headers)
        self.put204('/tags/{}/{}'.format(tag_1, photo_uuid), headers=headers)
        self.put204('/tags/{}/{}'.format(tag_2, photo_uuid), headers=headers)
        self.put204('/tags/{}/{}'.format(tag_3, check_photo_uuid), headers=headers)

        tag_1_count += 1
        tag_2_count += 1
        tag_3_count += 1

        # Same as tag 3
        result = self.get200(u'/tags/search/f/{}'.format(tag_3),
                             headers=headers)
        self.assertNotEqual(0, len(result))
        self.assertEqual(result[0]['tag'], tag_3)

        # Same as tag 3, lower
        result = self.get200(u'/tags/search/f/{}'.format(tag_3.lower()),
                             headers=headers)
        self.assertNotEqual(0, len(result))
        self.assertEqual(result[0]['tag'], tag_3)

        # # Same as tag 3, no hash
        result = self.get200(u'/tags/search/f/{}'.format(tag_3[1:]),
                             headers=headers)
        self.assertNotEqual(0, len(result))
        self.assertEqual(result[0]['tag'], tag_3)

        # Same as tag 2
        result = self.get200(u'/tags/search/f/{}'.format(tag_2), headers=headers)
        self.assertNotEqual(0, len(result))
        self.assertEqual(result[0]['tag'], tag_2)

        # Near tag 2 and 1
        url = u'/tags/search/f/{}'.format('test')
        result = self.get200(url, headers=headers)
        self.assertNotEqual(0, len(result))
        result = {x['tag'] for x in result}
        self.assertIn(tag_2, result)
        self.assertIn(tag_1, result)

        # Near tag 3
        url = u'/tags/search/f/{}'.format(u'HAND')
        result = self.get200(url, headers=headers)
        self.assertNotEqual(0, len(result))
        self.assertEqual(result[0]['tag'], tag_3)

        # Some other matches for that search
        self.photo_upload(headers=headers, tags=['HANDinHAND'])
        self.photo_upload(headers=headers, tags=['TheHandover', 'TheHandoff'])
        self.photo_upload(headers=headers, tags=['GottaHandItToYou'])

        url = u'/tags/search/f/{}'.format(u'HAND')
        result = self.get200(url, headers=headers)
        self.assertNotEqual(0, len(result))
        result = {x['tag'] for x in result}
        self.assertIn(tag_3, result)
        self.assertIn(u'HANDinHAND', result)
        self.assertIn(u'TheHandover', result)
        self.assertIn(u'TheHandoff', result)
        self.assertIn(u'GottaHandItToYou', result)

    def test_photo_upload_worker_profile_tournament(self):
        # Create a test user with no profile photo.
        user = create_user()
        user.show_gender_male = True
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = la_location.uuid
        user.save()
        headers = headers_with_auth(user.uuid, user.token)
        headers.update(la_geo_header)

        # Assert user shows no photo, internally or externally.
        user.refresh()
        self.assertIsNone(user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertNotIn('photo', result)

        # "Post" a photo, which initates the photo upload process.
        # It creates the photo object in DynamoDB, and returns a URL for
        # uploading the photo to s3.
        result = self.post202('/photos',
                              data=json.dumps({
                                  'set_as_profile_photo': True
                               }),
                               headers=headers)
        self.assertIn('id', result)
        self.assertIn('share_url', result)
        self.assertIn('post_form_args', result)
        post_form_args = result['post_form_args']

        # Let's be sure the URL we were sent looks like what amazon gives us.
        self.assertIn('fields', post_form_args)
        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)
        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy", "value": "aoeutshaoesuthaosetuhasoetuhasoteuhsatoheu="},
                {"name": "AWSAccessKeyId", "value": "aoesuthasoeuthasoetuh"},
                {"name": "signature", "value": "aoesuthasoeuthasoetuh="},
                {"name": "key", "value": key_value}]}

        self.assertDictEqual(
            check_result,
            post_form_args)

        # Let's inspect the photo in DynamoDB. It should exist, but have
        # flags showing it is not uploaded or processed. The user should still
        # show no photo.
        photo_uuid = UUID(key_value.split('_')[1])

        photo = get_photo(photo_uuid, check_copy_complete=False)
        self.assertIsNotNone(photo)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)
        self.assertIsInstance(photo, Photo)
        self.assertTrue(photo.set_as_profile_photo)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(None, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertNotIn('photo', result)

        # At this point we fake the use of the above URL to upload a pic to s3,
        # which upon completion queues an SQS message, which calls
        # the worker_callback endpoint on our worker. The worker gets the
        # message and processes the photo.

        self.worker_process_photo(key_value)
        # Note: the photo.crop is disabled for IS_LOCAL_DEV=True, but it would
        # be good to get some tests in there.

        # Test photo upload, copy_complete and that the photo was added to
        # the feed.
        photo.refresh()
        self.assertTrue(photo.uploaded)
        self.assertTrue(photo.copy_complete)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(photo.uuid, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertIn('photo', result)
        log.info(result['photo'])

        old_photo = photo

        # Test a new post

        # "Post" a photo, which initates the photo upload process.
        # It creates the photo object in DynamoDB, and returns a URL for
        # uploading the photo to s3.
        result = self.post202('/photos',
                              data=json.dumps({
                                  'set_as_profile_photo': True,
                                  'enter_in_tournament': False
                              }),
                              headers=headers)

        # Let's be sure the URL we were sent looks like what amazon gives us.
        self.assertIn('id', result)
        self.assertIn('share_url', result)
        self.assertIn('post_form_args', result)
        post_form_args = result['post_form_args']

        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)
        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy", "value": "aoeutshaoesuthaosetuhasoetuhasoteuhsatoheu="},
                {"name": "AWSAccessKeyId", "value": "aoesuthasoeuthasoetuh"},
                {"name": "signature", "value": "aoesuthasoeuthasoetuh="},
                {"name": "key", "value": key_value}]}

        self.assertDictEqual(
            check_result,
            post_form_args)

        # Let's inspect the photo in DynamoDB. It should exist, but have
        # flags showing it is not uploaded or processed. The user should still
        # show no photo.
        photo_uuid = UUID(key_value.split('_')[1])

        photo = get_photo(photo_uuid, check_copy_complete=False)
        self.assertIsNotNone(photo)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)
        self.assertIsInstance(photo, ProfileOnlyPhoto)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(old_photo.uuid, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertIn('photo', result)

        # At this point we fake the use of the above URL to upload a pic to s3,
        # which upon completion queues an SQS message, which calls
        # the worker_callback endpoint on our worker. The worker gets the
        # message and processes the photo.

        # This is a message like the one s3 sends to our worker
        self.worker_process_photo(key_value)
        # Note: the photo.crop is disabled for IS_LOCAL_DEV=True, but it would
        # be good to get some tests in there.

        # Test photo upload, copy_complete and that the photo was added to
        # the feed.
        photo.refresh()
        self.assertTrue(photo.uploaded)
        self.assertTrue(photo.copy_complete)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(photo.uuid, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertIn('photo', result)
        log.info(result['photo'])


    def test_photo_upload_worker_profile_only(self):
        # Create a test user with no profile photo.
        user = self.create_user(facebook=False)
        headers = get_headers(user)
        headers.update(la_geo_header)

        # Assert user shows no photo, internally or externally.
        user.refresh()
        self.assertIsNone(user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertNotIn('photo', result)

        # "Post" a photo, which initates the photo upload process.
        # It creates the photo object in DynamoDB, and returns a URL for
        # uploading the photo to s3.
        result = self.post202('/photos',
                              data=json.dumps({
                                  'set_as_profile_photo': True,
                                  'enter_in_tournament': False
                              }),
                              headers=headers)

        # Let's be sure the URL we were sent looks like what amazon gives us.
        self.assertIn('id', result)
        self.assertIn('share_url', result)
        self.assertIn('post_form_args', result)
        post_form_args = result['post_form_args']
        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)
        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy", "value": "aoeutshaoesuthaosetuhasoetuhasoteuhsatoheu="},
                {"name": "AWSAccessKeyId", "value": "aoesuthasoeuthasoetuh"},
                {"name": "signature", "value": "aoesuthasoeuthasoetuh="},
                {"name": "key", "value": key_value}]}

        self.assertDictEqual(
            check_result,
            post_form_args)

        # Let's inspect the photo in DynamoDB. It should exist, but have
        # flags showing it is not uploaded or processed. The user should still
        # show no photo.
        photo_uuid = UUID(key_value.split('_')[1])

        photo = get_photo(photo_uuid, check_copy_complete=False)
        self.assertIsNotNone(photo)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)
        self.assertIsInstance(photo, ProfileOnlyPhoto)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(None, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertNotIn('photo', result)

        # At this point we fake the use of the above URL to upload a pic to s3,
        # which upon completion queues an SQS message, which calls
        # the worker_callback endpoint on our worker. The worker gets the
        # message and processes the photo.

        # This is a message like the one s3 sends to our worker
        self.worker_process_photo(key_value)
        # Note: the photo.crop is disabled for IS_LOCAL_DEV=True, but it would
        # be good to get some tests in there.

        # Test photo upload, copy_complete and that the photo was added to
        # the feed.
        photo.refresh()
        self.assertTrue(photo.uploaded)
        self.assertTrue(photo.copy_complete)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(photo.uuid, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertIn('photo', result)
        log.info(result['photo'])

        old_photo = photo

        # Test a new post
        self.post_facebook(user)

        # "Post" a photo, which initates the photo upload process.
        # It creates the photo object in DynamoDB, and returns a URL for
        # uploading the photo to s3.
        result = self.post202('/photos',
                              data=json.dumps({
                                  'set_as_profile_photo': False,
                                  'enter_in_tournament': True
                              }),
                              headers=headers)

        # Let's be sure the URL we were sent looks like what amazon gives us.
        self.assertIn('id', result)
        self.assertIn('share_url', result)
        self.assertIn('post_form_args', result)
        post_form_args = result['post_form_args']
        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)
        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy", "value": "aoeutshaoesuthaosetuhasoetuhasoteuhsatoheu="},
                {"name": "AWSAccessKeyId", "value": "aoesuthasoeuthasoetuh"},
                {"name": "signature", "value": "aoesuthasoeuthasoetuh="},
                {"name": "key", "value": key_value}]}

        self.assertDictEqual(
            check_result,
            post_form_args)

        # Let's inspect the photo in DynamoDB. It should exist, but have
        # flags showing it is not uploaded or processed. The user should still
        # show no photo.
        photo_uuid = UUID(key_value.split('_')[1])

        photo = get_photo(photo_uuid, check_copy_complete=False)
        self.assertIsNotNone(photo)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)
        self.assertIsInstance(photo, Photo)
        self.assertFalse(photo.set_as_profile_photo)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(old_photo.uuid, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertIn('photo', result)

        # At this point we fake the use of the above URL to upload a pic to s3,
        # which upon completion queues an SQS message, which calls
        # the worker_callback endpoint on our worker. The worker gets the
        # message and processes the photo.

        # This is a message like the one s3 sends to our worker
        self.worker_process_photo(key_value)
        # Note: the photo.crop is disabled for IS_LOCAL_DEV=True, but it would
        # be good to get some tests in there.

        # Test photo upload, copy_complete and that the photo was added to
        # the feed.
        photo.refresh()
        self.assertTrue(photo.uploaded)
        self.assertTrue(photo.copy_complete)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(old_photo.uuid, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertIn('photo', result)
        log.info(result['photo'])


    def test_photo_upload_worker_tournament_only(self):
        # Create a test user with no profile photo.
        user = create_user()
        user.show_gender_male = True
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = la_location.uuid
        user.save()
        headers = headers_with_auth(user.uuid, user.token)
        headers.update(la_geo_header)

        # Assert user shows no photo, internally or externally.
        user.refresh()
        self.assertIsNone(user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertNotIn('photo', result)

        # "Post" a photo, which initates the photo upload process.
        # It creates the photo object in DynamoDB, and returns a URL for
        # uploading the photo to s3.
        result = self.post202('/photos',
                              data=json.dumps({
                                  'set_as_profile_photo': True
                              }),
                              headers=headers)

        # Let's be sure the URL we were sent looks like what amazon gives us.
        self.assertIn('id', result)
        self.assertIn('share_url', result)
        self.assertIn('post_form_args', result)
        post_form_args = result['post_form_args']
        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)
        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy", "value": "aoeutshaoesuthaosetuhasoetuhasoteuhsatoheu="},
                {"name": "AWSAccessKeyId", "value": "aoesuthasoeuthasoetuh"},
                {"name": "signature", "value": "aoesuthasoeuthasoetuh="},
                {"name": "key", "value": key_value}]}

        self.assertDictEqual(
            check_result,
            post_form_args)

        # Let's inspect the photo in DynamoDB. It should exist, but have
        # flags showing it is not uploaded or processed. The user should still
        # show no photo.
        photo_uuid = UUID(key_value.split('_')[1])

        photo = get_photo(photo_uuid, check_copy_complete=False)
        self.assertIsNotNone(photo)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)
        self.assertIsInstance(photo, Photo)
        self.assertTrue(photo.set_as_profile_photo)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(None, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertNotIn('photo', result)

        # At this point we fake the use of the above URL to upload a pic to s3,
        # which upon completion queues an SQS message, which calls
        # the worker_callback endpoint on our worker. The worker gets the
        # message and processes the photo.

        # This is a message like the one s3 sends to our worker
        self.worker_process_photo(key_value)

        # Note: the photo.crop is disabled for IS_LOCAL_DEV=True, but it would
        # be good to get some tests in there.

        # Test photo upload, copy_complete and that the photo was added to
        # the feed.
        photo.refresh()
        self.assertTrue(photo.uploaded)
        self.assertTrue(photo.copy_complete)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(photo.uuid, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertIn('photo', result)
        log.info(result['photo'])

        old_photo = photo

        # Test a new post

        # "Post" a photo, which initates the photo upload process.
        # It creates the photo object in DynamoDB, and returns a URL for
        # uploading the photo to s3.
        result = self.post202('/photos',
                              data=json.dumps({
                                  'set_as_profile_photo': True,
                                  'enter_in_tournament': False
                              }),
                              headers=headers)

        # Let's be sure the URL we were sent looks like what amazon gives us.
        self.assertIn('id', result)
        self.assertIn('share_url', result)
        self.assertIn('post_form_args', result)
        post_form_args = result['post_form_args']
        key_value = None
        for field in post_form_args['fields']:
            if field.get('name') == 'key':
                key_value = field.get('value')
                break
        self.assertIsNotNone(key_value)
        check_result = {
            "action": "http://localpictourney-inbox.s3.amazonaws.com/",
            "fields": [
                {"name": "x-amz-storage-class", "value": "STANDARD"},
                {"name": "policy", "value": "aoeutshaoesuthaosetuhasoetuhasoteuhsatoheu="},
                {"name": "AWSAccessKeyId", "value": "aoesuthasoeuthasoetuh"},
                {"name": "signature", "value": "aoesuthasoeuthasoetuh="},
                {"name": "key", "value": key_value}]}

        self.assertDictEqual(
            check_result,
            post_form_args)

        # Let's inspect the photo in DynamoDB. It should exist, but have
        # flags showing it is not uploaded or processed. The user should still
        # show no photo.
        photo_uuid = UUID(key_value.split('_')[1])

        photo = get_photo(photo_uuid, check_copy_complete=False)
        self.assertIsNotNone(photo)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)
        self.assertIsInstance(photo, ProfileOnlyPhoto)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(old_photo.uuid, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertIn('photo', result)

        # At this point we fake the use of the above URL to upload a pic to s3,
        # which upon completion queues an SQS message, which calls
        # the worker_callback endpoint on our worker. The worker gets the
        # message and processes the photo.

        # This is a message like the one s3 sends to our worker
        self.worker_process_photo(key_value)

        # Note: the photo.crop is disabled for IS_LOCAL_DEV=True, but it would
        # be good to get some tests in there.

        # Test photo upload, copy_complete and that the photo was added to
        # the feed.
        photo.refresh()
        self.assertTrue(photo.uploaded)
        self.assertTrue(photo.copy_complete)
        user.refresh()
        # parameter set_as_profile_photo=True for this to not be None.
        self.assertEqual(photo.uuid, user.photo)
        result = self.get200('/users/me', headers=headers)
        self.assertIn('photo', result)
        log.info(result['photo'])
        # TODO: Test photo was added to feed.

    def test_photo_url(self):
        # If not logged in, 401.
        self.get401('/users/me/photos/small')
        self.get401('/users/me/photos/medium')
        self.get401('/users/me/photos/game')

        # If photo does not exist, 404.
        # Log in.
        user = create_user()
        user.show_gender_male = True
        user.save()
        headers = headers_with_auth(user.uuid, user.token)

        self.get404('/users/me/photos/small', headers=headers)
        self.get404('/users/me/photos/medium', headers=headers)
        self.get404('/users/me/photos/game', headers=headers)

        # If photo exists but is not copied, 404.
        photo_uuid = uuid1()
        gender_location = 'm%s' % la_location.uuid.hex
        photo = Photo(gender_location, photo_uuid)
        photo.is_gender_male = True
        photo.post_date = datetime.now()
        photo.user_uuid = user.uuid
        photo.lat = la_geo.lat
        photo.lon = la_geo.lon
        photo.geodata = la_geo.meta
        photo.location = la_location.uuid
        photo.file_name = '%s_%s' % (gender_location, photo_uuid.hex)
        photo.set_as_profile_photo = True
        photo.save()
        user.photo = photo.uuid
        user.save()
        headers = headers_with_auth(user.uuid, user.token)

        self.get404('/users/me/photos/small', headers=headers)
        self.get404('/users/me/photos/medium', headers=headers)
        self.get404('/users/me/photos/game', headers=headers)

        # If photo exists and is copied, 200 with URL.
        photo.copy_complete = True
        photo.save()

        result = self.get200('/users/me/photos/small', headers=headers)
        self.assertDictContainsSubset({'url' : '%s/%s_240x240' % (
                                            settings.SERVE_BUCKET_URL,
                                            photo.file_name)}, result)
        result = self.get200('/users/me/photos/medium', headers=headers)
        self.assertDictContainsSubset({'url' : '%s/%s_480x480' % (
                                            settings.SERVE_BUCKET_URL,
                                            photo.file_name)}, result)
        result = self.get200('/users/me/photos/game', headers=headers)
        self.assertDictContainsSubset({'url' : '%s/%s_960x960' % (
                                            settings.SERVE_BUCKET_URL,
                                            photo.file_name)}, result)


    def test_create_photo(self):
        user = create_user()
        user.show_gender_male = False
        user.save()

        s = "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"
        geo = Geo.from_string(s)
        location = Location.from_geo(geo)

        photo = create_photo(user, location, geo, True, True)

        self.assertEqual(u'f', photo.gender_location[0])
        UUID(photo.gender_location[1:])
        self.assertIsNotNone(photo.uuid)
        self.assertFalse(photo.get_is_gender_male())
        UUID(photo.get_location())
        self.assertIsNotNone(photo.post_date)
        self.assertEqual(user.uuid, photo.user_uuid)
        self.assertIsNotNone(photo.phi)
        self.assertIsNotNone(photo.sigma)
        self.assertFalse(photo.live)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)
        self.assertTrue(photo.set_as_profile_photo)

        photo_gender_location = photo.gender_location
        photo_uuid = photo.uuid

        photo = Photo.get(photo_gender_location, photo_uuid)
        self.assertEqual(u'f', photo.gender_location[0])
        UUID(photo.gender_location[1:])
        self.assertIsNotNone(photo.uuid)
        self.assertFalse(photo.get_is_gender_male())
        UUID(photo.get_location())
        self.assertIsNotNone(photo.post_date)
        self.assertEqual(user.uuid, photo.user_uuid)
        self.assertIsNotNone(photo.phi)
        self.assertIsNotNone(photo.sigma)
        self.assertFalse(photo.live)
        self.assertFalse(photo.uploaded)
        self.assertFalse(photo.copy_complete)

        query_lookup = list(Photo.uuid_index.query(photo.uuid, limit=2))[0]

        # This doesn't get set until the worker completes the upload.
        self.assertIsNone(user.photo)


    def test_photo_comments(self):
        self.reset_model()
        user = self.create_user(gender='female')
        user.save()

        commenter1 = self.create_user(gender='male',
                                      first_name='commenter 1',
                                      facebook=False,
                                      random_user_name=False)
        commenter2 = self.create_user(gender='female',
                                      user_name='commenter2')
        commenter3 = self.create_user(gender='male',
                                      user_name='callme \U0001F44C')

        user_headers = headers_with_auth(user.uuid, user.token)
        user_headers.update(la_geo_header)
        photo_uuid = self.photo_upload(set_as_profile_photo=True, headers=user_headers)

        commenter1_headers = headers_with_auth(commenter1.uuid, commenter1.token)
        commenter1_headers.update(la_geo_header)
        commenter1_photo_uuid = self.photo_upload(set_as_profile_photo=True, headers=commenter1_headers)

        commenter3_headers = headers_with_auth(commenter3.uuid, commenter3.token)
        commenter3_headers.update(la_geo_header)
        commenter3_photo_uuid = self.photo_upload(set_as_profile_photo=True, headers=commenter3_headers)
        url = '/photos/%s/comments' % photo_uuid.hex
        comment_1 = u'test comment 1'
        comment_2 = u'test comment \U0001F44C 2'
        comment_3 = u'test comment 3'
        comment_4 = u'test comment 4'

        # Not logged in.
        self.post401(url, data=comment_1,
                     headers={'Content-Type': 'text/plain; charset=utf-8',
                              'Geo-Position': la_s})

        # -- Comment 1 --------------------------------------------------------
        # Log in.
        headers = headers_with_auth(commenter1.uuid, commenter1.token)
        headers.update(la_geo_header)

        commenter1.refresh()
        commenter1_photo = commenter1.get_photo()
        photo_url = '%s/%s_%%s' % (settings.SERVE_BUCKET_URL,
                                   commenter1_photo.file_name)
        comment1_expected = {
            'user_uuid': unicode(commenter1.uuid.hex),
            'first_name': 'commenter 1',
            'text': comment_1,
            'url_small': photo_url % '240x240',
            'url_medium': photo_url % '480x480',
            'url_large': photo_url % '960x960',
            'location': unicode(la_location.uuid.hex),
            'location_name': la_location.accent_city,
            'gender': 'male'
        }

        # This user has insufficient registration status to post a comment.
        self.post401(url, data=comment_1, headers=headers,
                     error_message=u"InsufficientAuthorization: User registration status was 'need name', needs to be 'ok'")

        # Set the user name.
        user_data = {
            'user_name': 'commenter 1 test'
        }
        self.patch204('/users/me', data=json.dumps(user_data), headers=headers)

        # Try again.
        self.post401(url, data=comment_1, headers=headers,
                     error_message=u"InsufficientAuthorization: User registration status was 'need facebook', needs to be 'ok'")
        commenter1.refresh()
        self.post_facebook(commenter1)

        # Try again, Success!
        result = self.post201(url, data=comment_1, headers=headers)
        self.assertDictContainsSubset(comment1_expected, result)
        self.assertIn('uuid', result)
        comment1_uuid_hex = result['uuid']

        # Read back all the comments
        result = self.get200(url, headers=headers)
        self.assertIn('comments', result)
        comments = result['comments']
        self.assertEqual(1, len(comments))
        self.assertDictContainsSubset(comment1_expected, comments[0])

        # -- Comment 2 --------------------------------------------------------
        headers = headers_with_auth(commenter2.uuid, commenter2.token)
        headers.update(la_geo_header)

        comment2_expected = {
            'user_uuid': unicode(commenter2.uuid.hex),
            'user_name': commenter2.user_name,
            'text': comment_2,
            'location': unicode(la_location.uuid.hex),
            'location_name': la_location.accent_city,
            'gender': 'female'
        }
        result = self.post201(url, data=comment_2, headers=headers)
        self.assertDictContainsSubset(comment2_expected, result)
        self.assertIn('uuid', result)
        comment2_uuid_hex = result['uuid']

        # Read back all the comments
        result = self.get200(url, headers=headers)
        self.assertIn('comments', result)
        comments = result['comments']
        self.assertEqual(2, len(comments))
        self.assertDictContainsSubset(comment1_expected, comments[0])
        self.assertDictContainsSubset(comment2_expected, comments[1])

        # -- Comment 3 --------------------------------------------------------
        headers = headers_with_auth(commenter3.uuid, commenter3.token)
        headers.update(la_geo_header)

        comment3_expected = {
            'user_uuid': unicode(commenter3.uuid.hex),
            'user_name': commenter3.user_name,
            'text': comment_3,
            'location': unicode(la_location.uuid.hex),
            'location_name': la_location.accent_city,
            'gender': 'male'
        }
        result = self.post201(url, data=comment_3, headers=headers)
        self.assertDictContainsSubset(comment3_expected, result)
        self.assertIn('uuid', result)
        comment3_uuid_hex = result['uuid']

        # Read back all the comments
        result = self.get200(url, headers=headers)
        self.assertIn('comments', result)
        comments = result['comments']
        self.assertEqual(3, len(comments))
        self.assertDictContainsSubset(comment1_expected, comments[0])
        self.assertDictContainsSubset(comment2_expected, comments[1])
        self.assertDictContainsSubset(comment3_expected, comments[2])

        # -- Comment 4 --------------------------------------------------------

        headers = headers_with_auth(commenter1.uuid, commenter1.token)
        headers.update(la_geo_header)

        photo_url = '%s/%s_%%s' % (settings.SERVE_BUCKET_URL,
                                   commenter1_photo.file_name)
        comment4_expected = {
            'user_uuid': unicode(commenter1.uuid.hex),
            'first_name': 'commenter 1',
            'text': comment_4,
            'url_small': photo_url % '240x240',
            'url_medium': photo_url % '480x480',
            'url_large': photo_url % '960x960',
            'location': unicode(la_location.uuid.hex),
            'location_name': la_location.accent_city,
            'gender': 'male'
        }
        result = self.post201(url, data=comment_4, headers=headers)
        self.assertDictContainsSubset(comment4_expected, result)
        self.assertIn('uuid', result)
        comment4_uuid_hex = result['uuid']

        # Read back all the comments
        result = self.get200(url, headers=headers)
        self.assertIn('comments', result)
        comments = result['comments']
        self.assertEqual(4, len(comments))
        self.assertDictContainsSubset(comment1_expected, comments[0])
        self.assertDictContainsSubset(comment2_expected, comments[1])
        self.assertDictContainsSubset(comment3_expected, comments[2])
        self.assertDictContainsSubset(comment4_expected, comments[3])

        # Check the feed.
        # User is the only one who should have NewComment items.
        commenters = [commenter1, commenter2, commenter3]
        for commenter in commenters:
            headers = headers_with_auth(commenter.uuid, commenter.token)
            headers.update(la_geo_header)
            feed = self.get200('/users/me/notification_history',
                               headers=headers)
            for item in feed:
                self.assertNotEqual(item[u'activity'], 'NewComment')

        # 'user' should have this recorded in their feed.
        headers = headers_with_auth(user.uuid, user.token)
        headers.update(la_geo_header)
        feed = self.get200('/users/me/notification_history', headers=headers)
        # Feed is newest to oldest.
        expected = [comment4_uuid_hex, comment3_uuid_hex, comment2_uuid_hex, comment1_uuid_hex]
        self.assert_feed_joined(feed[-1])
        self.assertEqual(len(expected), len(feed[:-1]))
        for comment_uuid_hex, item in zip(expected, feed[:-1]):
            self.assert_feed_new_comment(item)

    def test_photo_share_url(self):
        user = self.create_user()
        headers = get_headers(user)
        photo_uuid = self.photo_upload(headers=headers,
                                       set_as_profile_photo=False,
                                       enter_in_tournament=True)
        renderings = self.get200('/users/me/photos', headers=headers)
        found_rendering = [r for r in renderings if r['id'] == photo_uuid.hex][
            0]
        self.assertIn('share_url', found_rendering)
        self.assertEqual(
            'http://{}/{}'.format(
                settings.URL,
                photo_uuid.hex),
            found_rendering['share_url'])
        photo_uuid = self.photo_upload(headers=headers,
                                       set_as_profile_photo=True,
                                       enter_in_tournament=False)
        user_info = self.get200('/users/me', headers=headers)
        found_rendering = user_info['photo']
        self.assertEqual(photo_uuid.hex, found_rendering['id'])
        self.assertNotIn('share_url', found_rendering)

        photo_uuid = self.photo_upload(headers=headers,
                                       set_as_profile_photo=True,
                                       enter_in_tournament=True)
        user_info = self.get200('/users/me', headers=headers)
        found_rendering = user_info['photo']
        self.assertEqual(photo_uuid.hex, found_rendering['id'])
        self.assertEqual(
            'http://{}/{}'.format(
                settings.URL,
                photo_uuid.hex),
            found_rendering['share_url'])

        result = self.application.get(found_rendering['share_url'],
                                      headers=headers)
        self.assertEqual('200 OK', result.status)

        import uuid
        result = self.application.get(
            'http://localpictourney.elasticbeanstalk.com/12345',
            headers=headers)
        self.assertEqual('404 NOT FOUND', result.status)

        result = self.application.get(
            'http://localpictourney.elasticbeanstalk.com/{}'.format(
                uuid.uuid1().hex),
            headers=headers)
        self.assertEqual('404 NOT FOUND', result.status)

    def test_photo_individual(self):
        user = self.create_user()
        headers = get_headers(user)
        photo_uuid = self.photo_upload(headers=headers,
                                       set_as_profile_photo=False,
                                       enter_in_tournament=True)
        result = self.get200('/photos/{}'.format(photo_uuid.hex),
                             headers=headers)

    # -- Match ----------------------------------------------------------------

    def test_match_stream(self):
        self.reset_model()
        user_count = 40
        uncopy_count = 6

        match_ids = set()

        photos = []
        bad_photos = []
        from itertools import chain
        photo_count = chain([10], cycle([1,2,3]))  # One user has many photos
        for x in xrange(user_count):
            user = create_user()
            user.show_gender_male = False
            user.save()
            # Users have 1, 2 or 3 pix each.
            for x in range(photo_count.next()):
                photo_uuid = uuid1()
                gender_location = 'f%s' % la_location.uuid.hex
                photo = Photo(gender_location, photo_uuid)
                photo.is_gender_male = False
                photo.lat = la_geo.lat
                photo.lon = la_geo.lon
                photo.geodata = la_geo.meta
                photo.location = la_location.uuid
                photo.post_date = datetime.now()
                photo.user_uuid = user.uuid
                photo.copy_complete = True
                photo.file_name = "%s_%s" % (gender_location, photo_uuid.hex)
                photo.set_as_profile_photo = True
                photo.media_type = 'movie' if random.random() < 0.3 else 'photo'
                photo.save()
                user.photo = photo.uuid
                user.save()
                photos.append(photo)
            photo_uuid = uuid1()
            gender_location = 'f%s' % la_location.uuid.hex
            photo = Photo(gender_location, photo_uuid)
            photo.is_gender_male = False
            photo.lat = la_geo.lat
            photo.lon = la_geo.lon
            photo.geodata = la_geo.meta
            photo.location = la_location.uuid
            photo.post_date = datetime.now()
            photo.user_uuid = user.uuid
            photo.copy_complete = True
            photo.file_name = "%s_%s" % (gender_location, photo_uuid.hex)
            photo.set_as_profile_photo = True
            photo.media_type = 'movie' if random.random() < 0.3 else 'photo'
            photo.is_test = True
            photo.save()
        for photo in random.sample(photos, uncopy_count):
            photo.copy_complete = False
            photo.save()
            bad_photos.append(photo)
        photo_count = len(photos) - uncopy_count
        bad_photo_hex_uuids = set([p.uuid.hex for p in bad_photos])

        # If not logged in, 401.
        self.get401('/users/me/matches')

        # Create a test user with auth credentials.
        user = self.create_user()
        headers = get_headers(user)

        # TODO which of these to use?
        # Log in.
        user = create_user()
        user.show_gender_male = False  # User shows and views same gender.
        user.view_gender_male = False
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = la_location.uuid
        user.save()

        self.assertEqual(10, user.matches_until_next_tournament)
        self.assertEqual('local', user.next_tournament)

        headers = headers_with_auth(user.uuid, user.token)

        result = self.get200('/users/me/matches', headers=headers)

        # This should update tournament status
        user.refresh()
        self.assertEqual(0, user.matches_until_next_tournament)
        self.assertEqual('local', user.next_tournament)

        first = result
        matches = first[u'matches']
        def assert_match_unique(match):
            self.assertNotEqual(match[u'photo_a'][u'user'][u'uuid'],
                                match[u'photo_b'][u'user'][u'uuid'])
            match_id = match[u'match_id']
            self.assertNotIn(match_id[:32], bad_photo_hex_uuids)
            self.assertNotIn(match_id[32:], bad_photo_hex_uuids)
            self.assertNotIn(match_id, match_ids)
            match_ids.add(match_id)
            # Assert media types aren't mixed.
            self.assertEqual(match[u'photo_a'][u'media_type'],
                             match[u'photo_b'][u'media_type'])
        def assert_matches(matches):
            [assert_match_unique(m) for m in matches if m[u't'] == u'regular']
        assert_matches(matches)

        # this call should snarf a tournament
        result = self.get200('/users/me/matches', headers=headers)
        self.assertIn('matches', result)
        self.assertEqual(1, len(result[u'matches']))
        assert_matches(result[u'matches'])
        tournament = result[u'matches'][0]
        self.assertEqual(tournament[u't'], u'local')

        user.refresh()
        self.assertEqual(8, user.matches_until_next_tournament)
        self.assertEqual('regional', user.next_tournament)

        # We will interject here to post a photo, testing the feature that
        # if we post and view the same gender then our new photo will be
        # next in our next list of matches.
        headers.update(la_geo_header)
        user_photo1_uuid = self.photo_upload(set_as_profile_photo=True,
                                             enter_in_tournament=True,
                                             headers=headers)

        # more regular matches with a tournament on the end.
        second = self.get200('/users/me/matches', headers=headers)
        assert_matches(second[u'matches'])
        self.assertEqual(9, len(second[u'matches']))
        for m in second['matches'][0:-1]:
            self.assertEqual(u'regular', m[u't'])
        self.assertEqual(u'regional', second[u'matches'][-1][u't'])

        # Confirm the user's new photo is in the first match.
        self.assertIn(user_photo1_uuid.hex,
                        [second[u'matches'][0]['photo_a'][u'id'],
                         second[u'matches'][0]['photo_b'][u'id']])
        # for match in second[u'matches'][1:-1]:
        #     self.assertNotIn(user_photo1_uuid.hex,
        #                          [match['photo_a'][u'id'],
        #                           match['photo_b'][u'id']])

        user.refresh()
        self.assertEqual(8, user.matches_until_next_tournament)
        self.assertEqual('global', user.next_tournament)

        # this call should snarf more regular matches.
        # Let's test category limited.
        result = self.get200('/users/me/matches',
                            headers=headers)
        assert_matches(result[u'matches'])
        self.assertEqual(9, len(result[u'matches']))
        for m in result['matches'][0:-1]:
            self.assertEqual(u'regular', m[u't'])
        self.assertEqual(u'global', result[u'matches'][-1][u't'])

        for match in result[u'matches'][0:-1]:
            self.assertNotIn(user_photo1_uuid.hex,
                                 [match['photo_a'][u'id'],
                                  match['photo_b'][u'id']])

        user.refresh()
        self.assertEqual(10, user.matches_until_next_tournament)
        self.assertEqual('local', user.next_tournament)

        third = self.get200('/users/me/matches', headers=headers)
        assert_matches(third[u'matches'])
        user.refresh()
        self.assertEqual(0, user.matches_until_next_tournament)
        self.assertEqual('local', user.next_tournament)

        # Test the reset feature
        result = self.get200('/users/me/matches', headers=headers)
        assert_matches(result[u'matches'])
        user.refresh()
        self.assertEqual(8, user.matches_until_next_tournament)
        self.assertEqual('regional', user.next_tournament)

        result = self.get200('/users/me/matches?reset_tournament_status=True',
                             headers=headers)
        assert_matches(result[u'matches'])
        self.assertEqual(10, len(result[u'matches']))
        for m in result[u'matches']:
            self.assertEqual(u'regular', m[u't'])
        user.refresh()
        self.assertEqual(0, user.matches_until_next_tournament)
        self.assertEqual('local', user.next_tournament)

        first_matches = {m['match_id'] for m in first['matches'] if 'match_id' in m}
        # Let's test if score deltas seem OK.

        for match_id in first_matches:
            a_uuid = UUID(match_id[:32])
            b_uuid = UUID(match_id[32:])
            match = Match.get((a_uuid, b_uuid), user.uuid)
            self.assertGreater(match.a_win_delta, 0)
            self.assertLess(match.a_lose_delta, 0)
            self.assertGreater(match.b_win_delta, 0)
            self.assertLess(match.b_lose_delta, 0)
            photo_a = get_photo(a_uuid)
            self.assertTrue(photo_a.copy_complete)
            photo_b = get_photo(b_uuid)
            self.assertTrue(photo_b.copy_complete)

        second_matches = {m['match_id'] for m in second['matches'] if 'match_id' in m}
        third_matches = {m['match_id'] for m in third['matches'] if 'match_id' in m}
        self.assertSetEqual(set(), first_matches.intersection(second_matches))
        self.assertSetEqual(set(), first_matches.intersection(third_matches))
        self.assertSetEqual(set(), second_matches.intersection(third_matches))

        # Test tournament reset
        user.refresh()

        # Exhaust user 1, to confirm we cover all the possibilities (and
        # this is why we need to reset the DB or this could take an n^2 long
        # time!)
        # TODO: Reset database for this test. Or comment out this part.
        # (I commented it out. Too many records and this will never complete.
        # while True:
        #     rv = self.application.get('/users/me/matches', headers=headers)
        #     data = json.loads(rv.data)
        #     if u'message' in data and data[u'message'].startswith('not enough unique matches'):
        #         break
        #     assert_matches(json.loads(rv.data)[u'matches'])

        # Log in a second user
        user2 = create_user()
        user2.show_gender_male = True
        user2.view_gender_male = False
        user2.lat = la_geo.lat
        user2.lon = la_geo.lon
        user2.geodata = la_geo.meta
        user2.location = la_location.uuid
        user2.save()
        headers = headers_with_auth(user2.uuid, user2.token)

        first2 = self.get200('/users/me/matches', headers=headers)
        second2 = self.get200('/users/me/matches', headers=headers)
        third2 = self.get200('/users/me/matches', headers=headers)

        first2_matches = {m['match_id'] for m in first2['matches'] if 'match_id' in m}
        second2_matches = {m['match_id'] for m in second2['matches'] if 'match_id' in m}
        third2_matches = {m['match_id'] for m in third2['matches'] if 'match_id' in m}
        self.assertSetEqual(set(), first2_matches.intersection(second2_matches))
        self.assertSetEqual(set(), first2_matches.intersection(third2_matches))
        self.assertSetEqual(set(), second2_matches.intersection(third2_matches))

        self.assertNotEqual(first, first2)
        self.assertNotEqual(second, second2)
        self.assertNotEqual(third, third2)

        # All matches presented to User 1 for judging.
        all_match_1 = first_matches | second_matches | third_matches
        # All matches presented to User 2 for judging.
        all_match_2 = first2_matches | second2_matches | third2_matches

        # Odds are really low that these would be the same
        freshness = len(all_match_1) * .2
        self.assertLess(len(all_match_1.intersection(all_match_2)), freshness)


    def test_tag_match_stream(self):
        user_count = 40
        uncopy_count = 16
        test_tag = 'foo' + rand_string(6)

        match_ids = set()

        photo_uuids = []
        bad_photo_uuids = []
        from itertools import chain
        photo_count = chain([10], cycle([1,2,3]))  # One user has many photos
        for x in xrange(user_count):
            user = self.create_user(gender='female')
            headers = get_headers(user)
            # Users have 1, 2 or 3 pix each.
            for x in range(photo_count.next()):
                # TODO use create_photo, include the tag.
                photo_uuid = self.photo_upload(headers=headers,
                                               set_as_profile_photo=True,
                                               tags=[test_tag])
                photo_uuids.append(photo_uuid)
        for photo_uuid in random.sample(photo_uuids, uncopy_count):
            photo = get_photo(photo_uuid)
            photo.copy_complete = False
            photo.save()
            bad_photo_uuids.append(photo_uuid)
        photo_count = len(photo_uuids) - uncopy_count
        bad_photo_hex_uuids = {p.hex for p in bad_photo_uuids}

        # If not logged in, 401.
        url = '/users/me/tag_matches/{}'.format(test_tag)
        self.get401(url)

        # Create a test user with auth credentials.
        user = self.create_user()
        headers = get_headers(user)

        # Note: Tournaments disabled for tag matches.
        # Log in.
        user = self.create_user(gender='female', view_gender='female')
        headers = get_headers(user)

#        self.assertEqual(10, user.matches_until_next_tournament)
#        self.assertEqual('local', user.next_tournament)

        result = self.get200(url, headers=headers)

        # This should update tournament status
#        user.refresh()
#        self.assertEqual(0, user.matches_until_next_tournament)
#        self.assertEqual('local', user.next_tournament)

        first = result
        matches = first[u'matches']
        def assert_match_unique(match):
            self.assertNotEqual(match[u'photo_a'][u'user'][u'uuid'],
                                match[u'photo_b'][u'user'][u'uuid'])
            match_id = match[u'match_id']
            self.assertNotIn(match_id[:32], bad_photo_hex_uuids)
            self.assertNotIn(match_id[32:], bad_photo_hex_uuids)
            self.assertNotIn(match_id, match_ids)
            match_ids.add(match_id)
        def assert_matches(matches):
            [assert_match_unique(m) for m in matches if m[u't'] == u'regular']
        assert_matches(matches)

        # this call should snarf a tournament
        # result = self.get200(url, headers=headers)
        # self.assertIn('matches', result)
        # self.assertEqual(1, len(result[u'matches']))
        # assert_matches(result[u'matches'])
        # tournament = result[u'matches'][0]
        # self.assertEqual(tournament[u't'], u'local')
        #
        # user.refresh()
        # self.assertEqual(8, user.matches_until_next_tournament)
        # self.assertEqual('regional', user.next_tournament)

        # We will interject here to post a photo, testing the feature that
        # if we post and view the same gender then our new photo will be
        # next in our next list of matches.
        headers.update(la_geo_header)
        user_photo1_uuid = self.photo_upload(set_as_profile_photo=True,
                                             enter_in_tournament=True,
                                             headers=headers,
                                             tags=[test_tag])
        # more regular matches with a tournament on the end.
        second = self.get200(url, headers=headers)
        assert_matches(second[u'matches'])
        self.assertEqual(10, len(second[u'matches']))
        #self.assertEqual(9, len(second[u'matches']))
        # for m in second['matches'][0:-1]:
        #     self.assertEqual(u'regular', m[u't'])
        # self.assertEqual(u'regional', second[u'matches'][-1][u't'])

        # Confirm the user's new photo is in the first match.
        self.assertIn(user_photo1_uuid.hex,
                        [second[u'matches'][0]['photo_a'][u'id'],
                         second[u'matches'][0]['photo_b'][u'id']])
        # for match in second[u'matches'][1:-1]:
        #     self.assertNotIn(user_photo1_uuid.hex,
        #                          [match['photo_a'][u'id'],
        #                           match['photo_b'][u'id']])

        # user.refresh()
        # self.assertEqual(8, user.matches_until_next_tournament)
        # self.assertEqual('global', user.next_tournament)

        result = self.get200(url, headers=headers)
        assert_matches(result[u'matches'])
        self.assertEqual(10, len(result[u'matches']))
        # self.assertEqual(9, len(result[u'matches']))
        # for m in result['matches'][0:-1]:
        #     self.assertEqual(u'regular', m[u't'])
        # self.assertEqual(u'global', result[u'matches'][-1][u't'])

#        for match in result[u'matches'][0:-1]:
        for match in result[u'matches']:
                self.assertNotIn(user_photo1_uuid.hex,
                                 [match['photo_a'][u'id'],
                                  match['photo_b'][u'id']])

        # user.refresh()
        # self.assertEqual(10, user.matches_until_next_tournament)
        # self.assertEqual('local', user.next_tournament)

        third = self.get200(url, headers=headers)
        assert_matches(third[u'matches'])
        # user.refresh()
        # self.assertEqual(0, user.matches_until_next_tournament)
        # self.assertEqual('local', user.next_tournament)

        # Test the reset feature
        # result = self.get200(url, headers=headers)
        # assert_matches(result[u'matches'])
        # user.refresh()
        # self.assertEqual(8, user.matches_until_next_tournament)
        # self.assertEqual('regional', user.next_tournament)
        #
        # result = self.get200('{}?reset_tournament_status=True'.format(url),
        #                      headers=headers)
        # assert_matches(result[u'matches'])
        # self.assertEqual(10, len(result[u'matches']))
        # for m in result[u'matches']:
        #     self.assertEqual(u'regular', m[u't'])
        # user.refresh()
        # self.assertEqual(0, user.matches_until_next_tournament)
        # self.assertEqual('local', user.next_tournament)

        first_matches = {m['match_id'] for m in first['matches'] if 'match_id' in m}
        # Let's test if score deltas seem OK.

        for match_id in first_matches:
            a_uuid = UUID(match_id[:32])
            b_uuid = UUID(match_id[32:])
            match = Match.get((a_uuid, b_uuid), user.uuid)
            self.assertGreater(match.a_win_delta, 0)
            self.assertLess(match.a_lose_delta, 0)
            self.assertGreater(match.b_win_delta, 0)
            self.assertLess(match.b_lose_delta, 0)
            photo_a = get_photo(a_uuid)
            self.assertTrue(photo_a.copy_complete)
            photo_b = get_photo(b_uuid)
            self.assertTrue(photo_b.copy_complete)

        second_matches = {m['match_id'] for m in second['matches'] if 'match_id' in m}
        third_matches = {m['match_id'] for m in third['matches'] if 'match_id' in m}
        self.assertSetEqual(set(), first_matches.intersection(second_matches))
        self.assertSetEqual(set(), first_matches.intersection(third_matches))
        self.assertSetEqual(set(), second_matches.intersection(third_matches))

        # Test tournament reset
        user.refresh()

        # Exhaust user 1, to confirm we cover all the possibilities (and
        # this is why we need to reset the DB or this could take an n^2 long
        # time!)
        # TODO: Reset database for this test. Or comment out this part.
        # (I commented it out. Too many records and this will never complete.
        # while True:
        #     rv = self.application.get('/users/me/matches', headers=headers)
        #     data = json.loads(rv.data)
        #     if u'message' in data and data[u'message'].startswith('not enough unique matches'):
        #         break
        #     assert_matches(json.loads(rv.data)[u'matches'])

        # Log in a second user
        user2 = self.create_user(gender='female', view_gender='female')
        headers = get_headers(user2)

        first2 = self.get200(url, headers=headers)
        second2 = self.get200(url, headers=headers)
        third2 = self.get200(url, headers=headers)

        first2_matches = {m['match_id'] for m in first2['matches'] if 'match_id' in m}
        second2_matches = {m['match_id'] for m in second2['matches'] if 'match_id' in m}
        third2_matches = {m['match_id'] for m in third2['matches'] if 'match_id' in m}
        self.assertSetEqual(set(), first2_matches.intersection(second2_matches))
        self.assertSetEqual(set(), first2_matches.intersection(third2_matches))
        self.assertSetEqual(set(), second2_matches.intersection(third2_matches))

        self.assertNotEqual(first, first2)
        self.assertNotEqual(second, second2)
        self.assertNotEqual(third, third2)

        # All matches presented to User 1 for judging.
        all_match_1 = first_matches | second_matches | third_matches
        # All matches presented to User 2 for judging.
        all_match_2 = first2_matches | second2_matches | third2_matches

        # Odds are really low that these would be the same
        freshness = len(all_match_1) * .2
        self.assertLess(len(all_match_1.intersection(all_match_2)), freshness)


    def test_request_a_match(self):
        """You can request a match with a given photo."""
        self.reset_model()  # We reset the model to test match exhaustion.

        user = self.create_user()
        headers = get_headers(user)

        photo_1 = self.photo_upload(
                        headers, set_as_profile_photo=False,
                        enter_in_tournament=True)

        photo_2 = self.photo_upload(
                        headers, set_as_profile_photo=False,
                        enter_in_tournament=True)

        photo_3 = self.photo_upload(
                        headers, set_as_profile_photo=False,
                        enter_in_tournament=True)

        # Request a match with the first photo.
        result = self.get200('/users/me/matches/%s' % photo_1.hex,
                             headers=headers)
        a = result[u'photo_a'][u'id']
        b = result[u'photo_b'][u'id']
        self.assertIn(photo_1.hex, [a, b])
        if a == photo_1.hex:
            this_photo = a
            match_photo = b
        else:
            match_photo = a
            this_photo =b
        self.assertNotEqual(match_photo, this_photo)
        self.assertEqual(user.gender_location, get_photo(UUID(match_photo)).gender_location)
        first_match = match_photo

        # Request again.
        result = self.get200('/users/me/matches/%s' % photo_1.hex,
                             headers=headers)
        a = result[u'photo_a'][u'id']
        b = result[u'photo_b'][u'id']
        log.info(a)
        log.info(b)
        self.assertIn(photo_1.hex, [a, b])
        if a == photo_1.hex:
            this_photo = a
            match_photo = b
        else:
            match_photo = a
            this_photo = b
        self.assertNotEqual(match_photo, this_photo)
        self.assertNotEqual(match_photo, first_match)

        # Request again, get 404.
        self.get404('/users/me/matches/%s' % photo_1.hex, headers=headers,
                    error_message=u'Could not find photo to match with %s' % photo_1.hex)

        # Request with a bogus photo uuid.
        fake_photo_uuid = uuid1()
        self.get404('/users/me/matches/%s' % fake_photo_uuid.hex,
                    headers=headers,
                    error_message=u'Could not find photo with uuid %s' % fake_photo_uuid.hex)


    def test_match_model(self):
        photo_a_uuid = uuid1()
        photo_b_uuid = uuid1()
        user_uuid = uuid1()
        proposed_date = now()
        match = Match((photo_a_uuid, photo_b_uuid), user_uuid)
        match.proposed_date = proposed_date
        match.lat = la_geo.lat
        match.lon = la_geo.lon
        match.geodata = la_geo.meta
        match.location = la_location.uuid
        match.save()

        match2 = Match.get((photo_a_uuid, photo_b_uuid), user_uuid)
        self.assertIsNotNone(match2)
        self.assertEqual(proposed_date, match2.proposed_date)


    def test_judge_match(self):

        for x in xrange(5):
            user = self.create_user(gender='female')
            self.photo_upload(headers=get_headers(user),
                              set_as_profile_photo=True)

        # Log in.
        user = self.create_user()
        headers = get_headers(user)

        first = self.get200('/users/me/matches', headers=headers)

        # If you judge a Match that does not exist, you get an error.
        fake_match_id = uuid1().hex + uuid1().hex
        self.put404('/users/me/matches/%s' % fake_match_id,
                    data='a',
                    headers=headers)

        match_id = first['matches'][0]['match_id']

        self.put200('/users/me/matches/%s' % match_id,
                    data='a',
                    headers=headers)
        match = Match.get((UUID(match_id[:32]), UUID(match_id[32:])),
                          user.uuid)
        self.assertEqual(True, match.a_won)

        # Can't judge same match twice.
        self.put409('/users/me/matches/%s' % match_id,
                    data='b',
                    headers=headers)

        match_id = first['matches'][1]['match_id']
        self.put200('/users/me/matches/%s' % match_id,
                    data='b',
                    headers=headers)
        match = Match.get((UUID(match_id[:32]), UUID(match_id[32:])),
                          user.uuid)
        self.assertEqual(False, match.a_won)

        # Must provide legal input
        match_id = first['matches'][2]['match_id']
        self.put400('/users/me/matches/%s' % match_id,
                    data='c',
                    headers=headers)

        match_id = 'thisstringisnotverylong'
        self.put400('/users/me/matches/%s' % match_id,
                    data='c',
                    headers=headers)

        match_id = 'thisstringissixtyfourcharacterslongbutnotalegalidxxxxxxxxxxxxxxx'
        self.put409('/users/me/matches/%s' % match_id,
                    data='c',
                    headers=headers)

    def test_request_a_match(self):
        """You can request a match with a given photo."""
        self.reset_model()  # We reset the model to test match exhaustion.

        judge_user = self.create_user()
        post_user_1 = self.create_user(gender='female', view_gender='male')
        post_user_2 = self.create_user(gender='female', view_gender='male')
        post_user_3 = self.create_user(gender='female', view_gender='male')

        photo_1 = self.photo_upload(headers=get_headers(post_user_1))
        photo_2 = self.photo_upload(headers=get_headers(post_user_2))
        photo_3 = self.photo_upload(headers=get_headers(post_user_3))

        # Request a match with the first photo.
        headers = get_headers(judge_user)
        result = self.get200('/users/me/matches/%s' % photo_1.hex,
                             headers=headers)
        a = result[u'photo_a'][u'id']
        b = result[u'photo_b'][u'id']
        self.assertIn(photo_1.hex, [a, b])
        if a == photo_1.hex:
            this_photo = a
            match_photo = b
        else:
            match_photo = a
            this_photo = b
        self.assertNotEqual(match_photo, this_photo)
        self.assertIn(match_photo, [photo_2.hex, photo_3.hex])

        self.assertEqual(get_photo(photo_1).gender_location,
                         get_photo(UUID(match_photo)).gender_location)
        first_match = match_photo

        # Request again.
        result = self.get200('/users/me/matches/%s' % photo_1.hex,
                             headers=headers)
        a = result[u'photo_a'][u'id']
        b = result[u'photo_b'][u'id']
        self.assertIn(photo_1.hex, [a, b])
        if a == photo_1.hex:
            this_photo = a
            match_photo = b
        else:
            match_photo = a
            this_photo = b
        self.assertNotEqual(match_photo, this_photo)
        self.assertEqual(get_photo(photo_1).gender_location,
                         get_photo(UUID(match_photo)).gender_location)
        self.assertNotEqual(match_photo, first_match)

        # We now allow dupes (after trying no dupes)
        # # Request again, get 404.
        # result = self.get200('/users/me/matches/%s' % photo_1.hex,
        #                      headers=headers)
        # self.assertEqual(result[u'message'],
        #                  u'Could not find photo to match with %s' % photo_1.hex)

        # Request with a bogus photo uuid.
        fake_photo_uuid = uuid1()
        result = self.get404('/users/me/matches/%s' % fake_photo_uuid.hex,
                             headers=headers)
        self.assertEqual(result[u'message'],
                         u'Could not find photo with uuid %s' % fake_photo_uuid.hex)


    def test_win_feed(self):

        show_users = []
        for x in xrange(10):
            user = create_user()
            user.show_gender_male = False
            user.lat = la_geo.lat
            user.lon = la_geo.lon
            user.geodata = la_geo.meta
            user.location = la_location.uuid
            user.save()
            photo_uuid = uuid1()
            gender_location = 'f%s' % la_location.uuid.hex
            photo = Photo(gender_location, photo_uuid)
            photo.is_gender_male = False
            photo.lat = la_geo.lat
            photo.lon = la_geo.lon
            photo.geodata = la_geo.meta
            photo.location = la_location.uuid
            photo.post_date = datetime.now()
            photo.user_uuid = user.uuid
            photo.copy_complete = True
            photo.file_name = "%s_%s" % (gender_location, photo_uuid.hex)
            photo.set_as_profile_photo = True
            photo.save()
            user.photo = photo.uuid
            user.save()
            show_users.append(user)

        vote_users = []
        for x in xrange(5):
            user = self.create_user()
            vote_users.append(user)

        record = {}
        for user in vote_users:
            # Log in.
            headers = get_headers(user)
            first = self.get200('/users/me/matches', headers=headers)
            for match in first['matches']:
                if match[u't'] != u'regular':
                    continue
                match_id = match['match_id']
                a_id = match_id[:32]
                b_id = match_id[32:]

                if a_id in record:
                    vote = 'a'
                    win_id = a_id
                    lose_id = b_id
                else:
                    vote = 'b'
                    win_id = b_id
                    lose_id = a_id
                try:
                    r = record[win_id]
                except KeyError:
                    r = []
                    record[win_id] = r
                r.append(lose_id)
                self.put200('/users/me/matches/%s' % match_id,
                            data=vote,
                            headers=headers)

        # Check the win feed for each winner in the record.
        self.assertNotEqual(0, len(record))
        page_test_ran = False
        for win_photo_uuid, lose_photo_uuids in record.items():
            # Get the user for the win_photo.
            user = get_user(get_photo(UUID(win_photo_uuid)).user_uuid)
            headers = get_headers(user)

            wins = self.get200('/users/me/wins', headers=headers)

            for lose_photo_uuid in lose_photo_uuids:
                found = False
                for i, win in enumerate(wins):
                    if win['lose_photo']['id'] == lose_photo_uuid:
                        found = True
                        del wins[i]
                        break
                self.assertTrue(found)

            wins = self.get200('/users/me/wins', headers=headers)

            if len(wins) >= 5:
                page_wins = self.get200('/users/me/wins?exclusive_start_key={}&count=3'.format(wins[-5]['id']),
                                        headers=headers)
                self.assertLessEqual(len(page_wins), 3)
                self.assertEqual(page_wins[0]['id'], wins[-4]['id'])
                self.assertEqual(page_wins[1]['id'], wins[-3]['id'])
                self.assertEqual(page_wins[2]['id'], wins[-2]['id'])

                page_wins = self.get200('/users/me/wins?exclusive_start_key={}&count=2'.format(wins[-5]['id']),
                                        headers=headers)
                self.assertLessEqual(len(page_wins), 2)
                self.assertEqual(page_wins[0]['id'], wins[-4]['id'])
                self.assertEqual(page_wins[1]['id'], wins[-3]['id'])
                page_test_ran = True

        self.assertTrue(page_test_ran)  # If this is False we didn't get
        # a single winner with enough wins to run the paging test.


    # -- Tournaments ----------------------------------------------------------

    def test_create_tournament(self):

        s = "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"
        geo = Geo.from_string(s)
        location = Location.from_geo(geo)
        gender_location = Photo.make_gender_location(False, location.uuid.hex)

        for x in xrange(50):
            user = create_user()
            user.show_gender_male = False
            user.save()
            photo_uuid = uuid1()
            file_name = '%s_%s' % ('m%s' % location.uuid.hex, photo_uuid.hex)
            photo = Photo(gender_location, photo_uuid)
            photo.is_gender_male = False
            photo.location = location.uuid
            photo.post_date = datetime.now()
            photo.user_uuid = user.uuid
            photo.copy_complete = True
            photo.lat = geo.lat
            photo.lon = geo.lon
            photo.geodata = geo.meta
            photo.file_name = file_name
            photo.set_as_profile_photo = False
            photo.save()
            user.photo = photo.uuid
            user.save()

        # Log in.
        user = create_user()
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = la_location.uuid
        user.show_gender_male = True
        user.view_gender_male = False
        user.save()

        from logic import tournament

        local = tournament.create_local_tournament(user)
        self.assertIsNotNone(local)
        self.assertIsNotNone(local.one)
        self.assertIsNotNone(local.two)
        self.assertIsNotNone(local.three)
        self.assertIsNotNone(local.four)
        self.assertIsNotNone(local.five)
        self.assertIsNotNone(local.six)
        self.assertIsNotNone(local.seven)
        self.assertIsNotNone(local.eight)
        self.assertIsNotNone(local.nine)
        self.assertIsNotNone(local.ten)
        self.assertIsNotNone(local.eleven)
        self.assertIsNotNone(local.twelve)
        self.assertIsNotNone(local.thirteen)
        self.assertIsNotNone(local.fourteen)
        self.assertIsNotNone(local.fifteen)
        self.assertIsNotNone(local.sixteen)
        all = {
            local.one,
            local.two,
            local.three,
            local.four,
            local.five,
            local.six,
            local.seven,
            local.eight,
            local.nine,
            local.ten,
            local.eleven,
            local.twelve,
            local.thirteen,
            local.fourteen,
            local.fifteen,
            local.sixteen
        }
        self.assertEqual(16, len(all))

        regional = tournament.create_regional_tournament(user)
        self.assertIsNotNone(regional)
        self.assertIsNotNone(regional.one)
        self.assertIsNotNone(regional.two)
        self.assertIsNotNone(regional.three)
        self.assertIsNotNone(regional.four)
        self.assertIsNotNone(regional.five)
        self.assertIsNotNone(regional.six)
        self.assertIsNotNone(regional.seven)
        self.assertIsNotNone(regional.eight)
        self.assertIsNotNone(regional.nine)
        self.assertIsNotNone(regional.ten)
        self.assertIsNotNone(regional.eleven)
        self.assertIsNotNone(regional.twelve)
        self.assertIsNotNone(regional.thirteen)
        self.assertIsNotNone(regional.fourteen)
        self.assertIsNotNone(regional.fifteen)
        self.assertIsNotNone(regional.sixteen)
        all = {
            regional.one,
            regional.two,
            regional.three,
            regional.four,
            regional.five,
            regional.six,
            regional.seven,
            regional.eight,
            regional.nine,
            regional.ten,
            regional.eleven,
            regional.twelve,
            regional.thirteen,
            regional.fourteen,
            regional.fifteen,
            regional.sixteen
        }
        self.assertEqual(16, len(all))

        global_tournament = tournament.create_global_tournament(user)
        self.assertIsNotNone(global_tournament)
        self.assertIsNotNone(global_tournament.one)
        self.assertIsNotNone(global_tournament.two)
        self.assertIsNotNone(global_tournament.three)
        self.assertIsNotNone(global_tournament.four)
        self.assertIsNotNone(global_tournament.five)
        self.assertIsNotNone(global_tournament.six)
        self.assertIsNotNone(global_tournament.seven)
        self.assertIsNotNone(global_tournament.eight)
        self.assertIsNotNone(global_tournament.nine)
        self.assertIsNotNone(global_tournament.ten)
        self.assertIsNotNone(global_tournament.eleven)
        self.assertIsNotNone(global_tournament.twelve)
        self.assertIsNotNone(global_tournament.thirteen)
        self.assertIsNotNone(global_tournament.fourteen)
        self.assertIsNotNone(global_tournament.fifteen)
        self.assertIsNotNone(global_tournament.sixteen)
        all = {
            global_tournament.one,
            global_tournament.two,
            global_tournament.three,
            global_tournament.four,
            global_tournament.five,
            global_tournament.six,
            global_tournament.seven,
            global_tournament.eight,
            global_tournament.nine,
            global_tournament.ten,
            global_tournament.eleven,
            global_tournament.twelve,
            global_tournament.thirteen,
            global_tournament.fourteen,
            global_tournament.fifteen,
            global_tournament.sixteen
        }
        self.assertEqual(16, len(all))

    def disabled_matches_gets_tournaments(self):
        # Note: LocalPicTourney does not have tournaments.
        location = uuid1()

        for x in xrange(40):
            user = create_user()
            user.show_gender_male = False
            user.save()
            photo_uuid = uuid1()
            gender_location = 'f%s' % location.hex
            photo = Photo(gender_location, photo_uuid)
            photo.is_gender_male = False
            photo.lat = la_geo.lat
            photo.lon = la_geo.lon
            photo.geodata = la_geo.meta
            photo.location = location
            photo.post_date = datetime.now()
            photo.user_uuid = user.uuid
            photo.copy_complete = True
            photo.file_name = '%s_%s' % (gender_location, photo_uuid.hex)
            photo.set_as_profile_photo = True
            photo.save()
            user.photo = photo.uuid
            user.save()

        # Log in.
        user = create_user()
        user.show_gender_male = True
        user.view_gender_male = False
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = location
        user.save()
        headers = headers_with_auth(user.uuid, user.token)

        # Adjust the user's TournamentStatus so one is next.
        user.matches_until_next_tournament = 2
        user.save()

        rv = self.application.get('/users/me/matches', headers=headers)
        self.assertEqual('200 OK', rv.status)

        first = json.loads(rv.data)
        matches = first[u'matches']
#        self.assertEqual(3, len(matches))
#        self.assertEqual(u'regular', matches[0][u't'])
#        self.assertEqual(u'regular', matches[1][u't'])
#        self.assertEqual(u'global_by_category', matches[2][u't'])

        rv = self.application.get('/users/me/matches', headers=headers)
        self.assertEqual('200 OK', rv.status)

        second = json.loads(rv.data)
        matches = second[u'matches']
        self.assertEqual(9, len(matches))
        for match in matches[:-1]:
            self.assertEqual(u'regular', match[u't'])
        self.assertEqual(u'regional', matches[8][u't'])
        tournament = matches[8]
        # Tournament users who did not win.
        tournament_user_hexes = [
            tournament['two'][u'user'][u'uuid'],
            tournament['three'][u'user'][u'uuid'],
            tournament['four'][u'user'][u'uuid'],
            tournament['five'][u'user'][u'uuid'],
            tournament['six'][u'user'][u'uuid'],
            tournament['seven'][u'user'][u'uuid'],
            tournament['eight'][u'user'][u'uuid'],
            tournament['nine'][u'user'][u'uuid'],
            tournament['ten'][u'user'][u'uuid'],
            tournament['eleven'][u'user'][u'uuid'],
            tournament['twelve'][u'user'][u'uuid'],
            tournament['thirteen'][u'user'][u'uuid'],
            tournament['fourteen'][u'user'][u'uuid'],
            tournament['fifteen'][u'user'][u'uuid'],
            tournament['sixteen'][u'user'][u'uuid']
        ]
        tournament_winner_hex = tournament['one'][u'user'][u'uuid']
        User.get(UUID(tournament_winner_hex))

        # the winner needs some followers so we can test their feed.
        follower = create_user()
        follower.show_gender_male = False
        follower.save()
        photo_uuid = uuid1()
        file_name = '%s_%s' % ('m%s' % location.hex, photo_uuid.hex)
        photo = Photo(gender_location, photo_uuid)
        photo.is_gender_male = False
        photo.location = location
        photo.post_date = datetime.now()
        photo.user_uuid = follower.uuid
        photo.copy_complete = True
        photo.lat = la_geo.lat
        photo.lon = la_geo.lon
        photo.geodata = la_geo.meta
        photo.file_name = file_name
        photo.set_as_profile_photo = False
        photo.save()
        follower.photo = photo.uuid
        follower.save()

        follower_headers = headers_with_auth(follower.uuid, follower.token)
        data = json.dumps({'followed': tournament_winner_hex})
        rv = self.application.post('users/me/following',
                                   data=data,
                                   headers=follower_headers)
        self.assertEqual('200 OK', rv.status)

        # Vote on the tournament
        data = {
            'one_vs_two': tournament[u'one'][u'id'],
            'three_vs_four': tournament['four'][u'id'],
            'five_vs_six': tournament['five'][u'id'],
            'seven_vs_eight': tournament['eight'][u'id'],
            'nine_vs_ten': tournament['nine'][u'id'],
            'eleven_vs_twelve': tournament['twelve'][u'id'],
            'thirteen_vs_fourteen': tournament['thirteen'][u'id'],
            'fifteen_vs_sixteen': tournament['sixteen'][u'id'],
            'one_two_vs_three_four': tournament['one'][u'id'],
            'five_six_vs_seven_eight': tournament['eight'][u'id'],
            'nine_ten_vs_eleven_twelve': tournament['nine'][u'id'],
            'thirteen_fourteen_vs_fifteen_sixteen': tournament['sixteen'][u'id'],
            'one_four_vs_five_eight': tournament['one'][u'id'],
            'nine_twelve_vs_thirteen_sixteen': tournament['sixteen'][u'id'],
            'winner': tournament['one'][u'id']
        }

        url = '/users/me/tournaments/%s' % tournament[u'uuid']
        tournament_headers = headers_with_auth(user.uuid, user.token)
        tournament_headers['Content-Type'] = 'application/json'
        rv = self.application.put(url,
                                  data=json.dumps(data),
                                  headers=tournament_headers)
        self.assertEqual('200 OK', rv.status)

        # Check the feed.
        winner = User.get(UUID(tournament_winner_hex))
        winner_headers = headers_with_auth(winner.uuid, winner.token)
        rv = self.application.get('users/me/notification_history', headers=winner_headers)
        self.assertEqual('200 OK', rv.status)
        feed = json.loads(rv.data)
        self.assertEqual(3, len(feed))
        self.assertEqual('YouWonTournament', feed[0]['activity'])
        self.assertEqual('NewFollower', feed[1]['activity'])
        self.assert_feed_joined(feed[2])

        for user_hex in tournament_user_hexes:
            u = User.get(UUID(user_hex))
            u_headers = headers_with_auth(u.uuid, u.token)
            rv = self.application.get('users/me/notification_history', headers=u_headers)
            self.assertEqual('200 OK', rv.status)
            feed = json.loads(rv.data)
            import pprint
            pprint.pprint(feed)
            self.assertEqual(1, len(feed))
            self.assert_feed_joined(feed[0])

        rv = self.application.get('users/me/notification_history', headers=follower_headers)
        self.assertEqual('200 OK', rv.status)
        feed = json.loads(rv.data)
        self.assertEqual(2, len(feed))

        # feed_item, created_on=None,
        #                            read=None, winner=None, photo=None,
        #                            location=None, location_name=None,
        #                            score=None, gender_is_male=None
        winner_photo = winner.get_photo()
        self.assert_feed_won_tournament(feed[0], read=False,
                                        winner=tournament_winner_hex,
                                        photo=winner_photo.uuid.hex,
                                        location=winner_photo.location,
                                        location_name=None,
                                        score=winner_photo.score,
                                        gender_is_male=winner.show_gender_male)
        self.assert_feed_joined(feed[1])

        rv = self.application.get('users/me/notification_history',
                                  headers=follower_headers)
        self.assertStatus(u'200 OK', rv)
        feed = json.loads(rv.data)
        self.assertEqual(2, len(feed))

        # feed_item, created_on=None,
        #                            read=None, winner=None, photo=None,
        #                            location=None, location_name=None,
        #                            score=None, gender_is_male=None
        winner_photo = winner.get_photo()
        self.assert_feed_won_tournament(feed[0], read=True,
                                        winner=tournament_winner_hex,
                                        photo=winner_photo.uuid.hex,
                                        location=winner_photo.location,
                                        location_name=None,
                                        score=winner_photo.score,
                                        gender_is_male=winner.show_gender_male)
        self.assert_feed_joined(feed[1], read=True)

        # Can't vote twice.
        rv = self.application.put(url,
                                  data=json.dumps(data),
                                  headers=tournament_headers)
        self.assertEqual('409 CONFLICT', rv.status)

        rv = self.application.get('/users/me/matches', headers=headers)
        self.assertEqual('200 OK', rv.status)

        third = json.loads(rv.data)
        matches = third[u'matches']
        self.assertEqual(9, len(matches))
        for match in matches[:-1]:
            self.assertEqual(u'regular', match[u't'])
        self.assertEqual(u'global', matches[8][u't'])

        tournament = matches[8]

        # Vote on the tournament
        data = {
            'one_vs_two': tournament[u'one'][u'id'],
            'three_vs_four': tournament['four'][u'id'],
            'five_vs_six': tournament['five'][u'id'],
            'seven_vs_eight': tournament['eight'][u'id'],
            'nine_vs_ten': tournament['nine'][u'id'],
            'eleven_vs_twelve': tournament['twelve'][u'id'],
            'thirteen_vs_fourteen': tournament['thirteen'][u'id'],
            'fifteen_vs_sixteen': tournament['sixteen'][u'id'],
            'one_two_vs_three_four': tournament['one'][u'id'],
            'five_six_vs_seven_eight': tournament['eight'][u'id'],
            'nine_ten_vs_eleven_twelve': tournament['nine'][u'id'],
            'thirteen_fourteen_vs_fifteen_sixteen': tournament['sixteen'][u'id'],
            'one_four_vs_five_eight': tournament['one'][u'id'],
            'nine_twelve_vs_thirteen_sixteen': tournament['sixteen'][u'id'],
            'winner': tournament['one'][u'id']
        }

        url = '/users/me/tournaments/%s' % tournament[u'uuid']
        rv = self.application.put(url,
                                  data=json.dumps(data),
                                  headers=tournament_headers)
        self.assertEqual('200 OK', rv.status)

        # 70 matches, 10 at a time.
        # ... spec was changed to 10 instead of 70.
        for n in xrange(1):#7):
            rv = self.application.get('/users/me/matches', headers=headers)
            self.assertEqual('200 OK', rv.status)

            next = json.loads(rv.data)
            matches = next[u'matches']
            self.assertEqual(10, len(matches))
            for match in matches:
                self.assertEqual(u'regular', match[u't'])
        rv = self.application.get('/users/me/matches', headers=headers)
        self.assertEqual('200 OK', rv.status)

        repeat = json.loads(rv.data)
        matches = repeat[u'matches']
        self.assertEqual(1, len(matches))
        self.assertEqual(u'local', matches[0][u't'])
        tournament = matches[0]

        # Vote on the tournament
        data = {
            'one_vs_two': tournament[u'one'][u'id'],
            'three_vs_four': tournament['four'][u'id'],
            'five_vs_six': tournament['five'][u'id'],
            'seven_vs_eight': tournament['eight'][u'id'],
            'nine_vs_ten': tournament['nine'][u'id'],
            'eleven_vs_twelve': tournament['twelve'][u'id'],
            'thirteen_vs_fourteen': tournament['thirteen'][u'id'],
            'fifteen_vs_sixteen': tournament['sixteen'][u'id'],
            'one_two_vs_three_four': tournament['one'][u'id'],
            'five_six_vs_seven_eight': tournament['eight'][u'id'],
            'nine_ten_vs_eleven_twelve': tournament['nine'][u'id'],
            'thirteen_fourteen_vs_fifteen_sixteen': tournament['sixteen'][u'id'],
            'one_four_vs_five_eight': tournament['one'][u'id'],
            'nine_twelve_vs_thirteen_sixteen': tournament['sixteen'][u'id'],
            'winner': tournament['one'][u'id']
        }

        url = '/users/me/tournaments/%s' % tournament[u'uuid']
        rv = self.application.put(url,
                                  data=json.dumps(data),
                                  headers=tournament_headers)
        self.assertEqual('200 OK', rv.status)

        rv = self.application.get('/users/me/matches', headers=headers)
        self.assertEqual('200 OK', rv.status)

        repeat2 = json.loads(rv.data)
        matches = repeat2[u'matches']
        self.assertEqual(9, len(matches))
        for match in matches[:-1]:
            self.assertEqual(u'regular', match[u't'])
        self.assertEqual(u'regional', matches[8][u't'])

        tournament = matches[8]

        # Vote on the tournament
        data = {
            'one_vs_two': tournament[u'one'][u'id'],
            'three_vs_four': tournament['four'][u'id'],
            'five_vs_six': tournament['five'][u'id'],
            'seven_vs_eight': tournament['eight'][u'id'],
            'nine_vs_ten': tournament['nine'][u'id'],
            'eleven_vs_twelve': tournament['twelve'][u'id'],
            'thirteen_vs_fourteen': tournament['thirteen'][u'id'],
            'fifteen_vs_sixteen': tournament['sixteen'][u'id'],
            'one_two_vs_three_four': tournament['one'][u'id'],
            'five_six_vs_seven_eight': tournament['eight'][u'id'],
            'nine_ten_vs_eleven_twelve': tournament['nine'][u'id'],
            'thirteen_fourteen_vs_fifteen_sixteen': tournament['sixteen'][u'id'],
            'one_four_vs_five_eight': tournament['one'][u'id'],
            'nine_twelve_vs_thirteen_sixteen': tournament['sixteen'][u'id'],
            'winner': tournament['one'][u'id']
        }

        url = '/users/me/tournaments/%s' % tournament[u'uuid']
        rv = self.application.put(url,
                                  data=json.dumps(data),
                                  headers=tournament_headers)
        self.assertEqual('200 OK', rv.status)

    def test_leaderboards(self):
        # For 20 locations, create 20 male and 20 female users with photos.
        from apps.api.api import top_leaderboards_female, top_leaderboards_male
        import model
        locs = {}
        for name, uuid_hex in top_leaderboards_male:
            location_uuid = UUID(uuid_hex)
            location = LOCATIONS[location_uuid]
            locs[location_uuid] = {}
            users = []
            for i in range(15):
                users.append(create_user_with_photo(True, location, True))
            locs[location_uuid]['m'] = users
            gender_location = 'm%s' % uuid_hex
            log.info("testlooking up %s" % gender_location)
            top = list(model.Photo.score_index.query(gender_location,
                                                     limit=21))
            self.assertGreater(len(top), 14)

        for name, uuid_hex in top_leaderboards_female:
            location_uuid = UUID(uuid_hex)
            location = LOCATIONS[location_uuid]
            if location_uuid not in locs:
                locs[location_uuid] = {}
            users = []
            for i in range(15):
                users.append(create_user_with_photo(False, location, True))
            locs[location_uuid]['f'] = users

        # Log in as a male user from LA.
        user = create_user_with_photo(True)
        headers = headers_with_auth(user.uuid, user.token)

        result = self.get200('/leaderboards/m', headers=headers)
        self.assertIn('leaderboards', result)
        leaderboards = result['leaderboards']
        self.assertNotEqual(0, len(leaderboards))
        for leaderboard in leaderboards:
            self.assertIn('name', leaderboard)
            self.assertIn('gender_location', leaderboard)
            self.assertIn('photos', leaderboard)

        result = self.get200('/leaderboards/f', headers=headers)
        self.assertIn('leaderboards', result)
        leaderboards = result['leaderboards']
        self.assertNotEqual(0, len(leaderboards))
        for leaderboard in leaderboards:
            self.assertIn('name', leaderboard)
            self.assertIn('gender_location', leaderboard)
            self.assertIn('photos', leaderboard)

    def test_leaderboards_male_and_female(self):
        # Log in.
        user = create_user()
        user.show_gender_male = True
        user.view_gender_male = False
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = la_location.uuid
        user.save()
        headers = headers_with_auth(user.uuid, user.token)

        result = self.get200('/leaderboards/m', headers=headers)
        self.assertIn('leaderboards', result)
        leaderboards = result['leaderboards']
        self.assertNotEqual(0, len(leaderboards))
        for leaderboard in leaderboards:
            self.assertIn('name', leaderboard)
            self.assertIn('gender_location', leaderboard)
            self.assertIn('photos', leaderboard)

        result = self.get200('/leaderboards/f', headers=headers)
        self.assertIn('leaderboards', result)
        leaderboards = result['leaderboards']
        self.assertNotEqual(0, len(leaderboards))
        for leaderboard in leaderboards:
            self.assertIn('name', leaderboard)
            self.assertIn('gender_location', leaderboard)
            self.assertIn('photos', leaderboard)

    def test_leaderboard_filter(self):
        self.reset_model()
        url = '/leaderboards/f%s' % la_location.uuid.hex

        # Test Illegal Input
        user = self.create_user()
        headers = get_headers(user)
        this_url = '%s%s' % (url, '?when=fakeFilter')
        self.get409(this_url, headers=headers,
                    error_message=u"InvalidAPIUsage: unrecognized filter 'fakeFilter'")

        # To test leaderboard filters we need photos with scores and dates
        # The idea is that we will see different ordering when we use the
        # different leaderboard filters. day, week, month, alltime.
        # We will have multiple photos per user.
        users = []
        for i in range(20):
            user = self.create_user(gender='female')
            users.append(user)
        # By eliminating duplicate scores tests are easier to write.
        scores = range(1250, 1450 + 6 * 4 * len(users))
        random.shuffle(scores)
        scores = iter(scores)

        hour_photos = []
        day_photos = []
        week_photos = []
        month_photos = []
        year_photos = []
        all_photos = []
        count = 0
        # Give each user four entries in each of the time periods.
        gender_location = 'f%s' % la_location.uuid.hex
        for x in range(4):
            for user in users:
                hour_time = now() - timedelta(minutes=random.randrange(0, 55),
                                              seconds=random.randrange(0, 59))
                day_time = now() - timedelta(hours=random.randrange(2, 22),
                                             minutes=random.randrange(0, 59),
                                             seconds=random.randrange(0, 59))
                week_time = now() - timedelta(days=random.randrange(2, 6),
                                              minutes=random.randrange(0, 59),
                                              seconds=random.randrange(0, 59))
                month_time = now() - timedelta(days=random.randrange(8, 28),
                                               minutes=random.randrange(0, 59),
                                               seconds=random.randrange(0, 59))
                year_time = now() - timedelta(days=random.randrange(32, 363),
                                              minutes=random.randrange(0, 59),
                                              seconds=random.randrange(0, 59))
                all_time = now() - timedelta(days=random.randrange(367, 800),
                                             minutes=random.randrange(0, 59),
                                             seconds=random.randrange(0, 59))

                for post_date in [hour_time, day_time, week_time, month_time,
                                  year_time, all_time]:
                    photo_uuid = uuid1()
                    photo = Photo(gender_location, photo_uuid)
                    photo.is_gender_male = False
                    photo.lat = la_geo.lat
                    photo.lon = la_geo.lon
                    photo.geodata = la_geo.meta
                    photo.location = la_location.uuid
                    photo.post_date = post_date
                    photo.user_uuid = user.uuid
                    photo.copy_complete = True
                    photo.score = scores.next()
                    photo.file_name = "%s_%s" % (gender_location, photo_uuid.hex)
                    photo.set_as_profile_photo = True
                    photo.save()
                    count += 1
                    if post_date == hour_time:
                        hour_photos.append(photo)
                        photo_hour = HourLeaderboard(gender_location,
                                                     uuid=photo_uuid,
                                                     post_date=post_date,
                                                     score=photo.score)
                        photo_hour.save()
                        day_photos.append(photo)
                        photo_today = TodayLeaderboard(gender_location,
                                                       uuid=photo_uuid,
                                                       post_date=post_date,
                                                       score=photo.score)
                        photo_today.save()
                        week_photos.append(photo)
                        photo_week = WeekLeaderboard(gender_location,
                                                     uuid=photo_uuid,
                                                     post_date=post_date,
                                                     score=photo.score)
                        photo_week.save()
                        month_photos.append(photo)
                        photo_month = MonthLeaderboard(gender_location,
                                                       uuid=photo_uuid,
                                                       post_date=post_date,
                                                       score=photo.score)
                        photo_month.save()
                        year_photos.append(photo)
                        photo_year = YearLeaderboard(gender_location,
                                                     uuid=photo_uuid,
                                                     post_date=post_date,
                                                     score=photo.score)
                        photo_year.save()
                        all_photos.append(photo)
                    elif post_date == day_time:
                        day_photos.append(photo)
                        photo_today = TodayLeaderboard(gender_location,
                                                       uuid=photo_uuid,
                                                       post_date=post_date,
                                                       score=photo.score)
                        photo_today.save()
                        week_photos.append(photo)
                        photo_week = WeekLeaderboard(gender_location,
                                                     uuid=photo_uuid,
                                                     post_date=post_date,
                                                     score=photo.score)
                        photo_week.save()
                        month_photos.append(photo)
                        photo_month = MonthLeaderboard(gender_location,
                                                       uuid=photo_uuid,
                                                       post_date=post_date,
                                                       score=photo.score)
                        photo_month.save()
                        year_photos.append(photo)
                        photo_year = YearLeaderboard(gender_location,
                                                     uuid=photo_uuid,
                                                     post_date=post_date,
                                                     score=photo.score)
                        photo_year.save()
                        all_photos.append(photo)
                    elif post_date == week_time:
                        week_photos.append(photo)
                        photo_week = WeekLeaderboard(gender_location,
                                                     uuid=photo_uuid,
                                                     post_date=post_date,
                                                     score=photo.score)
                        photo_week.save()
                        month_photos.append(photo)
                        photo_month = MonthLeaderboard(gender_location,
                                                       uuid=photo_uuid,
                                                       post_date=post_date,
                                                       score=photo.score)
                        photo_month.save()
                        year_photos.append(photo)
                        photo_year = YearLeaderboard(gender_location,
                                                     uuid=photo_uuid,
                                                     post_date=post_date,
                                                     score=photo.score)
                        photo_year.save()
                        all_photos.append(photo)
                    elif post_date == month_time:
                        month_photos.append(photo)
                        photo_month = MonthLeaderboard(gender_location,
                                                       uuid=photo_uuid,
                                                       post_date=post_date,
                                                       score=photo.score)
                        photo_month.save()
                        year_photos.append(photo)
                        photo_year = YearLeaderboard(gender_location,
                                                     uuid=photo_uuid,
                                                     post_date=post_date,
                                                     score=photo.score)
                        photo_year.save()
                        all_photos.append(photo)
                    elif post_date == year_time:
                        year_photos.append(photo)
                        photo_year = YearLeaderboard(gender_location,
                                                     uuid=photo_uuid,
                                                     post_date=post_date,
                                                     score=photo.score)
                        photo_year.save()
                        all_photos.append(photo)
                    else:
                        all_photos.append(photo)

        s = lambda x: x.score
        hour_photos.sort(key=s, reverse=True)
        day_photos.sort(key=s, reverse=True)
        week_photos.sort(key=s, reverse=True)
        month_photos.sort(key=s, reverse=True)
        year_photos.sort(key=s, reverse=True)
        all_photos.sort(key=s, reverse=True)

        # Log in as a male user from the same area.
        user = create_user_with_photo(True)
        headers = headers_with_auth(user.uuid, user.token)
        # This trims the leaderboard.
        self.worker.post('/worker_filtered_leaderboard_callback')

        user = create_user()
        headers = get_headers(user)

        # # No filter is the all-time board.
        # rv = self.application.get('/leaderboards/f%s' % la_location.uuid.hex,
        #                           headers=headers)
        def assert_leaderboard(url, check_photos):
            result = self.get200(url, headers=headers)
            self.assertEqual(1, len(result))
            got_photos = result['photos']
            got_photo_ids = [r[u'id'] for r in result['photos']]
            check_photo_ids = [p.uuid.hex for p in check_photos]
            self.assertListEqual(check_photo_ids, got_photo_ids)
            # Note: We can't test the tail, there could be other same-
            # score records that were not returned, so it's
            # random whether it matches with our sorted list.
            for photo, render in zip(check_photos, got_photos):
                self.assertEqual(photo.uuid.hex, render[u'id'])

        url = '/leaderboards/f%s' % la_location.uuid.hex
        assert_leaderboard(url, week_photos[:50])
        assert_leaderboard(url + '?when=alltime', all_photos[:50])
        assert_leaderboard(url + '?when=alltime&exclusive_start_key={}'.format(all_photos[49].uuid.hex), all_photos[50:100])
        assert_leaderboard(url + '?when=alltime&count=10', all_photos[:10])
        assert_leaderboard(url + '?when=alltime&exclusive_start_key={}&count=15'.format(all_photos[29].uuid.hex), all_photos[30:45])
        assert_leaderboard(url + '?when=thishour', hour_photos[:50])
        assert_leaderboard(url + '?when=thishour&exclusive_start_key={}'.format(hour_photos[49].uuid.hex), hour_photos[50:100])
        assert_leaderboard(url + '?when=thishour&count=10', hour_photos[:10])
        assert_leaderboard(url + '?when=thishour&exclusive_start_key={}&count=15'.format(hour_photos[29].uuid.hex), hour_photos[30:45])
        assert_leaderboard(url + '?when=today', day_photos[:50])
        assert_leaderboard(url + '?when=today&exclusive_start_key={}'.format(day_photos[49].uuid.hex), day_photos[50:100])
        assert_leaderboard(url + '?when=today&count=10', day_photos[:10])
        assert_leaderboard(url + '?when=today&exclusive_start_key={}&count=15'.format(day_photos[29].uuid.hex), day_photos[30:45])
        assert_leaderboard(url + '?when=thisweek', week_photos[:50])
        assert_leaderboard(url + '?when=thisweek&exclusive_start_key={}'.format(week_photos[49].uuid.hex), week_photos[50:100])
        assert_leaderboard(url + '?when=thisweek&count=10', week_photos[:10])
        assert_leaderboard(url + '?when=thisweek&exclusive_start_key={}&count=15'.format(week_photos[29].uuid.hex), week_photos[30:45])
        assert_leaderboard(url + '?when=thismonth', month_photos[:50])
        assert_leaderboard(url + '?when=thismonth&exclusive_start_key={}'.format(month_photos[49].uuid.hex), month_photos[50:100])
        assert_leaderboard(url + '?when=thismonth&count=10', month_photos[:10])
        assert_leaderboard(url + '?when=thismonth&exclusive_start_key={}&count=15'.format(month_photos[29].uuid.hex), month_photos[30:45])
        assert_leaderboard(url + '?when=thisyear', year_photos[:50])
        assert_leaderboard(url + '?when=thisyear&exclusive_start_key={}'.format(year_photos[49].uuid.hex), year_photos[50:100])
        assert_leaderboard(url + '?when=thisyear&count=10', year_photos[:10])
        assert_leaderboard(url + '?when=thisyear&exclusive_start_key={}&count=15'.format(year_photos[29].uuid.hex), year_photos[30:45])

        url = '/leaderboards/all'
        assert_leaderboard(url, week_photos[:50])
        assert_leaderboard(url + '?when=alltime', all_photos[:50])
        assert_leaderboard(url + '?when=alltime&exclusive_start_key={}'.format(all_photos[49].uuid.hex), all_photos[50:100])
        assert_leaderboard(url + '?when=alltime&count=10', all_photos[:10])
        assert_leaderboard(url + '?when=alltime&exclusive_start_key={}&count=15'.format(all_photos[29].uuid.hex), all_photos[30:45])
        assert_leaderboard(url + '?when=thishour', hour_photos[:50])
        assert_leaderboard(url + '?when=thishour&exclusive_start_key={}'.format(hour_photos[49].uuid.hex), hour_photos[50:100])
        assert_leaderboard(url + '?when=thishour&count=10', hour_photos[:10])
        assert_leaderboard(url + '?when=thishour&exclusive_start_key={}&count=15'.format(hour_photos[29].uuid.hex), hour_photos[30:45])
        assert_leaderboard(url + '?when=today', day_photos[:50])
        assert_leaderboard(url + '?when=today&exclusive_start_key={}'.format(day_photos[49].uuid.hex), day_photos[50:100])
        assert_leaderboard(url + '?when=today&count=10', day_photos[:10])
        assert_leaderboard(url + '?when=today&exclusive_start_key={}&count=15'.format(day_photos[29].uuid.hex), day_photos[30:45])
        assert_leaderboard(url + '?when=thisweek', week_photos[:50])
        assert_leaderboard(url + '?when=thisweek&exclusive_start_key={}'.format(week_photos[49].uuid.hex), week_photos[50:100])
        assert_leaderboard(url + '?when=thisweek&count=10', week_photos[:10])
        assert_leaderboard(url + '?when=thisweek&exclusive_start_key={}&count=15'.format(week_photos[29].uuid.hex), week_photos[30:45])
        assert_leaderboard(url + '?when=thismonth', month_photos[:50])
        assert_leaderboard(url + '?when=thismonth&exclusive_start_key={}'.format(month_photos[49].uuid.hex), month_photos[50:100])
        assert_leaderboard(url + '?when=thismonth&count=10', month_photos[:10])
        assert_leaderboard(url + '?when=thismonth&exclusive_start_key={}&count=15'.format(month_photos[29].uuid.hex), month_photos[30:45])
        assert_leaderboard(url + '?when=thisyear', year_photos[:50])
        assert_leaderboard(url + '?when=thisyear&exclusive_start_key={}'.format(year_photos[49].uuid.hex), year_photos[50:100])
        assert_leaderboard(url + '?when=thisyear&count=10', year_photos[:10])
        assert_leaderboard(url + '?when=thisyear&exclusive_start_key={}&count=15'.format(year_photos[29].uuid.hex), year_photos[30:45])

        return

#     def disabled_leaderboard_filter_worker_task(self):
#         # TODO: Re-enable, disabled now that leaderboard objects are created
#         # when scores are processed.
#
#         # TODO: This test takes a very long time. can it be faster?
#         self.reset_model()
#
#         # duplicate scores make testing more difficult, so we use unique score.
#         scores = range(1250, 2050)
#         random.shuffle(scores)
#         scores = iter(scores)
#
#         # We'll make a bunch of photos with dates randomly in last 120 days,
#         # male and female, in all the listed locations, and add them to the
#         # day, week and month leaderboards, then trim them, and confirm they
#         # are trimmed.
#         n = now()
#         all_photos = []
#         all_week_photos = []
#         # day_photos_by_category = {}
#         # week_photos_by_category = {}
#         # month_photos_by_category = {}
#         # for category in CATEGORIES.keys():
#         #     day_photos = []
#         #     day_photos_by_category[category] = day_photos
#         #     week_photos = []
#         #     week_photos_by_category[category] = week_photos
#         #     month_photos = []
#         #     # TODO this got mucked in the merge, no categories in LocalPicTourney.
#         #     month_photos_by_location[location_uuid] = month_photos
#         #     for i in range(12):
#         #         log.info('making user')
#         #         gender = 'm' if i%2 else 'f'
#         #         gender_location = '%s%s' % (gender, location_uuid.hex)
#         #         user = create_user()
#         #         user.show_gender_male = gender == 'm'
#         #         user.lat = la_geo.lat  # Not a match for Location, but should
#         #         user.lon = la_geo.lon  # not matter.
#         #         user.geodata = la_geo.meta
#         #         user.location = location_uuid
#         #         user.save()
#         #         for i in range(10):
# # =======
# #             month_photos_by_category[category] = month_photos
# #             for i in range(7):
# #                 user = self.create_user()
# #                 headers = get_headers(user)
# #                 for i in range(7):
# #                     photo = self.photo_upload(headers, category,
# #                                               set_as_profile_photo=True,
# #                                               enter_in_tournament=True)
# #                     photo = get_photo(photo)
# # >>>>>>> 13b2b4e... fixing tests for new reg checking and category list:local_pic_tourney_tests.py
#                     # every third pic is today
#                     # if i % 3:
#                     #     post_date = n - timedelta(seconds=random.randrange(0, 40000))
#                     # else:
#                     #     post_date = n - timedelta(days=random.randrange(1, 35),
#                     #                               hours=random.randrange(0, 10),
#                     #                               minutes=random.randrange(0, 59),
#                     #                               seconds=random.randrange(0, 59))
#                     # photo_uuid = uuid1()
#                     # photo = Photo(gender_location, photo_uuid)
#                     # photo.is_gender_male = user.show_gender_male
#                     # photo.lat = la_geo.lat
#                     # photo.lon = la_geo.lon
#                     # photo.geodata = la_geo.meta
#                     # photo.location = location_uuid
#                     # photo.post_date = post_date
#                     # photo.score = random.randrange(1350, 1650)
#                     # photo.file_name = "%s_%s" % (gender_location,
#                     #                              photo_uuid.hex)
#                     # score = scores.next()
#                     # photo.post_date = post_date
#                     # photo.score = score
#                     # photo.save()
#                     # for c in [HourLeaderboard, TodayLeaderboard, WeekLeaderboard, MonthLeaderboard, YearLeaderboard]:
#                     #     item = c.get(photo.category, photo.uuid)
#                     #     item.post_date = post_date
#                     #     item.score = score
#                     #     item.save()
#                     # if post_date > n-timedelta(days=1):
#                     #     day_photos.append(photo)
#                     # photo_today = TodayLeaderboard(gender_location,
#                     #                                uuid=photo_uuid,
#                     #                                post_date=post_date,
#                     #                                score=photo.score)
#                     # photo_today.save()
#                     # if post_date > n-timedelta(days=7):
#                     #     week_photos.append(photo)
#                     # photo_week = WeekLeaderboard(gender_location,
#                     #                              uuid=photo_uuid,
#                     #                              post_date=post_date,
#                     #                              score=photo.score)
#                     # photo_week.save()
#                     # if post_date > n-timedelta(days=31):
#                     #     month_photos.append(photo)
#                     # photo_month = MonthLeaderboard(gender_location,
#                     #                                uuid=photo_uuid,
#                     #                                post_date=post_date,
#                     #                                score=photo.score)
#                     # photo_month.save()
#
#         import score
#         log.info("trimming today leaderboards")
#         score.trim_today_leaderboards()
#         log.info("trimming week leaderboards")
#         score.trim_week_leaderboards()
#         log.info("trimming month leaderboards")
#         score.trim_month_leaderboards()
#
# # TODO the merge may be wrong here.
# # =======
# #                     if post_date > n-timedelta(days=7):
# #                         week_photos.append(photo)
# #                     if post_date > n-timedelta(days=31):
# #                         month_photos.append(photo)
# # =======
# #                         all_week_photos.append(photo)
# #                     if post_date > n-timedelta(days=31):
# #                         month_photos.append(photo)
# #                     all_photos.append(photo)
#
#         # This trims the leaderboard.
#         self.worker.post('/worker_filtered_leaderboard_callback')
#
#         def assert_trim(got_photos, check_photos):
#             self.assertEqual(len(check_photos), len(got_photos))
#             # We can't be sure same-scored records will be in same order.
#             current_score = None
#             same_score_got_photos = []
#             same_score_check_photos = []
#             # Note: We can't test the tail, there could be other same-
#             # score records that were not returned, so it's
#             # random whether it matches with our sorted list.
#             # -- I think we can in this case as the query is exhaustive.
#             for check_photo, got_photo in zip(check_photos, got_photos):
#                 if current_score != got_photo.score:
#                     # Order the same-scored photos by uuid and then compare.
#                     same_score_got_photos.sort(key=lambda x: x.uuid.hex)
#                     same_score_check_photos.sort(key=lambda x: x.uuid.hex)
#                     # for p, r in zip(same_score_photos, same_score_renders):
#                     #     print '%s vs %s  --  %s %s' % (p.score, r['score'], p.uuid.hex, r['id'])
#                     for p, r in zip(same_score_check_photos,
#                                     same_score_got_photos):
#                         self.assert_photo_render(p, r)
#                     same_score_got_photos = []
#                     same_score_check_photos = []
#                 else:
#                     current_score = got_photo.score
#                     same_score_got_photos.append(check_photo)
#                     same_score_check_photos.append(got_photo)
#
#         def assert_dict(query, d):
#             for location_uuid, photos in d.items():
#                 for gender in ['m', 'f']:
#                     is_gender_male = gender == 'm'
#                     gender_location = '%s%s' % (gender, location_uuid.hex)
#                     logger.info('asserting %s' % gender_location)
#                     assert_trim(list(query(gender_location)),
#                                 [p for p in photos if p.is_gender_male == is_gender_male])
#
#         assert_dict(TodayLeaderboard.query, day_photos_by_location)
#         assert_dict(WeekLeaderboard.query, week_photos_by_location)
#         assert_dict(MonthLeaderboard.query, month_photos_by_location)
#
#         for photo in all_photos:
#             self.assertEqual(0, len(photo.get_awards()))
#
#         # This finds the current photo leaders and gives awards.
#         self.worker.post('/worker_awards_callback')
#
#         count = 0
#         for photo in all_photos:
#             count += len(photo.get_awards())
#
#         self.assertEqual(len(CATEGORIES) * 3 + 3, count)
#
#         for category, photos in week_photos_by_category.items():
#             photos.sort(key=lambda x: x.score, reverse=True)
#             for i, photo in enumerate(photos):
#                 if i < 3:
#                     awards = photo.get_awards()
#                     self.assertIn(len(awards), [1, 2])
#                     if len(awards) == 2:
#                         if awards[0].kind[-7:] == 'overall':
#                             overall_award = awards[0]
#                             award = awards[1]
#                         else:
#                             overall_award = awards[1]
#                             award = awards[0]
#                     else:
#                         award = awards[0]
#                     self.assertNotEqual(award.kind[-7:], 'overall')
#                     if i == 0:
#                         self.assertEqual(
#                                 'First Place in {}'.format(category),
#                                 award.kind)
#                     if i == 1:
#                         self.assertEqual(
#                                 'Second Place in {}'.format(category),
#                                 award.kind)
#                     if i == 2:
#                         self.assertEqual(
#                                 'Third Place in {}'.format(category),
#                                 award.kind)
#                 else:
#                     self.assertEqual(0, len(photo.get_awards()))
#
#         all_week_photos.sort(key=lambda x: x.score, reverse=True)
#         def get_overall_award(photo):
#             for a in photo.get_awards():
#                 if a.kind[-7:] == 'overall':
#                     return a
#
#         award = get_overall_award(all_week_photos[0])
#         self.assertEqual('First Place overall', award.kind)
#         award = get_overall_award(all_week_photos[1])
#         self.assertEqual('Second Place overall', award.kind)
#         award = get_overall_award(all_week_photos[2])
#         self.assertEqual('Third Place overall', award.kind)

    def test_score(self):
        location = uuid1()

        photos = []
        user_count = 3#40
        photos_per_user = 2
        photo_count = user_count * photos_per_user
        scores = [random.randrange(1450, 1550) for x in xrange(photo_count)]
        score_iter = iter(scores)
        gender_location = 'f%s' % location.hex
        users = []
        for x in xrange(user_count):
            user = create_user()
            user.show_gender_male = False
            user.save()
            users.append(user)
            for y in xrange(photos_per_user):
                photo_uuid = uuid1()
                photo = Photo(gender_location, photo_uuid)
                photo.is_gender_male = False
                photo.lat = la_geo.lat
                photo.lon = la_geo.lon
                photo.geodata = la_geo.meta
                photo.location = location
                photo.post_date = datetime.now()
                photo.user_uuid = user.uuid
                photo.copy_complete = True
                photo.file_name = '%s_%s' % (gender_location, photo_uuid.hex)
                photo.score = score_iter.next()
                photo.set_as_profile_photo = True
                photo.save()
                photo_today = TodayLeaderboard(gender_location,
                                               uuid=photo_uuid,
                                               post_date=photo.post_date,
                                               score=photo.score)
                photo_today.save()
                photo_week = WeekLeaderboard(gender_location,
                                             uuid=photo_uuid,
                                             post_date=photo.post_date,
                                             score=photo.score)
                photo_week.save()
                photo_month = MonthLeaderboard(gender_location,
                                               uuid=photo_uuid,
                                               post_date=photo.post_date,
                                               score=photo.score)
                photo_month.save()
                if y == 1:
                    user.photo = photo.uuid
                    user.save()
                photos.append(photo)

        index_photos = list(Photo.score_index.query(gender_location,
                                                    limit=51,
                                                    scan_index_forward=False))
        index_scores = [p.score for p in index_photos]
        expected_board_scores = sorted(scores, reverse=True)[:50]
        self.assertListEqual(index_scores, expected_board_scores)

        # Log in.
        user = create_user()
        user.show_gender_male = True
        user.view_gender_male = False
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = location
        user.save()
        headers = headers_with_auth(user.uuid, user.token)

        data = self.get200('/leaderboards/%s' % gender_location,
                              headers=headers)
        self.assertIn('photos', data)
        self.assertListEqual([p['score'] for p in data['photos']],
                             expected_board_scores)

        # -- Glicko 2 ---------------------------------------------------------

        from logic.score import decode_match, encode_match, log_match_for_scoring
        get_kinesis().reset(settings.SCORE_STREAM)

        matches = []
        for x in xrange(200):
            winner, loser = random.sample(photos, 2)
            user = random.choice(users)
            match = Match((winner.uuid, loser.uuid), user.uuid)
            match.proposed_date = now()
            match.lat = la_geo.lat
            match.lon = la_geo.lon
            match.geodata = la_geo.meta
            match.location = la_location.uuid
            match.save()
            matches.append(match)
            log_match_for_scoring(match)

        for match in matches:
            match_2 = decode_match(encode_match(match))
            self.assertEqual(match.photo_uuids, match_2.photo_uuids)
            self.assertEqual(match.user_uuid, match_2.user_uuid)

        from logic.score import do_scores
        do_scores()

    # -- Following ------------------------------------------------------------

    def test_following_and_new_photo_and_notification_history_and_feed(self):
        users = []
        for x in xrange(10):
            user = self.create_user(gender='female')
            users.append(user)
            if x % 2:
                self.photo_upload(headers=get_headers(user),
                                  set_as_profile_photo=True)

        # The feeds should be empty except for joining,
        # 'create_user' adds that.
        for user in users:
            headers = get_headers(user)
            feed = self.get200('/users/me/notification_history',
                               headers=headers)
            self.assertEqual(1, len(feed))
            self.assert_feed_joined(feed[0])

        # Have each user follow the remaining users in the user list.
        follows_by_user = {}
        for i, user in enumerate(users):
            headers = get_headers(user)
            follows = follows_by_user[user.uuid] = []
            for followed_user in users[i+1:]:
                follows.append(followed_user.uuid)
                data = json.dumps({'followed': followed_user.uuid.hex})
                rv = self.application.post(app_url('/users/me/following'),
                                           data=data,
                                           headers=headers)
                # Stats could be in 401 if user does not have a photo.
                self.assertIn(rv.status, [u'200 OK', u'401 UNAUTHORIZED'])

        # Confirm the list of followed and following users is correct.
        # (these lists are not in order.)
        for i, user in enumerate(users):
            headers = get_headers(user)
            following = self.get200('/users/me/following', headers=headers)
            self.assertEqual(len(users) - i - 1, len(following))
            following_hexes = [f['uuid'] for f in following]
            for followed_user in users[i+1:]:
                self.assertIn(followed_user.uuid.hex, following_hexes)
            followers = self.get200('/users/me/followers', headers=headers)
            self.assertEqual(i, len(followers))
            follower_hexes = [f['uuid'] for f in followers]
            for follower_user in users[:i]:
                self.assertIn(follower_user.uuid.hex, follower_hexes)
            for follower_user in users[i:]:
                self.assertNotIn(follower_user.uuid.hex, follower_hexes)
            # Here we confirm the individual checking feature of
            # /users/me/followers
            for follower_user in users[:i]:
                self.get204('/users/me/followers/%s' % follower_user.uuid.hex,
                            headers=headers)
            for follower_user in users[i:]:
                self.get404('/users/me/followers/%s' % follower_user.uuid.hex,
                            headers=headers)

        # Confirm the feed that shows following is correct and in order,
        # so the last user should be the first event in the feed.
        # Each user is followed by the previous users in the user list.
        for i, user in enumerate(users):
            # The oldest record is user 0, the newest record is user N, so
            # we need to reverse this to equal the order we expect in the feed.
            followed_uuid_hexes = [u.uuid.hex for u in reversed(users[:i])]
            headers = get_headers(user)
            feed = self.get200('/users/me/notification_history', headers=headers)
            # Joined, and new followers.
            self.assertEqual(len(feed), len(followed_uuid_hexes) + 1)
            self.assert_feed_joined(feed[-1], read=True)

            # Here I confirm the correct membership, next I confirm order.
            self.assertSetEqual(
                set([f['user']['uuid'] for f in feed[:-1]]),
                set(followed_uuid_hexes)
            )

            # The feed is newest to oldest,
            for uuid_hex, feed_item in zip(followed_uuid_hexes, feed[:-1]):
                self.assertEqual('NewFollower', feed_item['activity'])
                self.assertEqual(uuid_hex, feed_item['user']['uuid'])
                self.assertFalse(feed_item['read'])

            feed = self.get200('/users/me/notification_history',
                               headers=headers)
            for uuid_hex, feed_item in zip(followed_uuid_hexes, feed[:-1]):
                self.assertTrue(feed_item['read'])


        # Confirm feed is empty. (it's just photos, not notification history.)
        for user in users:
            headers = get_headers(user)
            feed = self.get200('/users/me/feed', headers=headers)
            self.assertEqual(len(feed), 0)

        # Have each user post a photo. Keep track of them so we can confirm
        # the feed is correct.
        latest_photo = {}  # user.uuid => photo.uuid
        for i, user in enumerate(users):
            # Post a new Photo to the feed.
            photo_uuid = self.photo_upload(headers=get_headers(user),
                                           set_as_profile_photo=True)
            latest_photo[user.uuid] = photo_uuid

        # Confirm feed has photos in order.
        for i, user in enumerate(users):
            headers = get_headers(user)
            feed = self.get200('/users/me/feed', headers=headers)
            photo_hexes = [latest_photo[u.uuid].hex for u in reversed(users[i+1:])]
            self.assertEqual(len(feed), len(photo_hexes))
            for feed_item, photo_hex in zip(feed, photo_hexes):
                self.assertEqual(feed_item['id'], photo_hex)

        # Get the notification history back and confirm presence and order of
        #  all photos.
        for i, user in enumerate(users):
            # Feed has photos posted by users followed, each user follows the
            # remaining users in the list. They'll appear in reverse order in
            # the feed.
            photo_hexes = [latest_photo[u.uuid].hex for u in reversed(users[i+1:])]
            follower_hexes = [u.uuid.hex for u in reversed(users[:i])]

            # Let's simulate a duplicate feed item to see if our dupe checker
            # gets it.
            if photo_hexes:
                photo = get_photo(UUID(photo_hexes[0]))
                import model
                import uuid
                model.FeedActivity(user.uuid,
                                   created_on=now(),
                                   activity='NewPhoto',
                                   read=False,
                                   user=photo.user_uuid,
                                   photo=photo.uuid,
                                   comment=None,
                                   uuid=uuid.uuid1()).save()

            headers = get_headers(user)
            feed = self.get200('/users/me/notification_history',
                               headers=headers)
            # feed is newphotos, new followers, Joined.
            self.assertEqual(len(photo_hexes) + len(follower_hexes) + 1,
                             len(feed))
            for photo_hex, feed_item in zip(photo_hexes, feed[:len(photo_hexes)]):
                photo = get_photo(UUID(photo_hex))
                self.assert_feed_new_photo(feed_item, photo=photo_hex,
                                           location=la_location.uuid.hex,
                                           location_name='Los Angeles',
                                           score=1500, gender_is_male=False,
                                           user=photo.user_uuid.hex,
                                           read=False)

            for follower_hex, feed_item in zip(follower_hexes, feed[len(photo_hexes):-1]):
                self.assertEqual('NewFollower', feed_item['activity'])
                self.assertEqual(True, feed_item['read'])
                self.assertEqual(follower_hex, feed_item['user']['uuid'])

            self.assert_feed_joined(feed[-1], read=True)

            # Second try, should be the same, but read.
            feed = self.get200('/users/me/notification_history',
                               headers=headers)
            # feed is newphotos, new followers, Joined.
            self.assertEqual(len(photo_hexes) + len(follower_hexes) + 1,
                             len(feed))
            for photo_hex, feed_item in zip(photo_hexes, feed[:len(photo_hexes)]):
                photo = get_photo(UUID(photo_hex))
                self.assert_feed_new_photo(feed_item, photo=photo_hex,
                                           score=1500, gender_is_male=False,
                                           user=photo.user_uuid.hex,
                                           read=True)

            for follower_hex, feed_item in zip(follower_hexes, feed[len(photo_hexes):-1]):
                self.assertEqual('NewFollower', feed_item['activity'])
                self.assertEqual(True, feed_item['read'])
                self.assertEqual(follower_hex, feed_item['user']['uuid'])

            self.assert_feed_joined(feed[-1], read=True)

        # rv = self.application.get('users/me/notification_history?page=2&per_page=3',
        #                           headers=headers)
        # result = self.assert200(rv)
        # self.assertEqual(3, len(result))

        # Test notification history
        user = users[0]
        headers = get_headers(user)

        feed = self.get200('/users/me/notification_history', headers=headers)

        self.assertNotEqual(0, len(feed))

        three_count_feed = self.get200('/users/me/notification_history?count=3',
                                       headers=headers)
        self.assertTrue(3, len(three_count_feed))
        for original_item, check_item in zip(feed[:3], three_count_feed):
            self.assertEqual(original_item['id'], check_item['id'])

        three_count_feed = self.get200('/users/me/notification_history?exclusive_start_key={}&count=3'.format(feed[3]['id']),
                                       headers=headers)

        for original_item, check_item in zip(feed[4:7], three_count_feed):
            self.assertEqual(original_item['id'], check_item['id'])

        # Test feed paging
        user = users[0]
        headers = get_headers(user)
        photo_hexes = [latest_photo[u.uuid].hex for u in reversed(users[1:])]

        feed = self.get200('/users/me/feed', headers=headers)

        self.assertEqual(len(feed), len(photo_hexes))
        for feed_item, photo_hex in zip(feed, photo_hexes):
            self.assertEqual(feed_item['id'], photo_hex)

        three_count_feed = self.get200('/users/me/feed?count=3',
                                       headers=headers)
        self.assertTrue(3, len(three_count_feed))
        for original_item, check_item in zip(feed[:3], three_count_feed):
            self.assertEqual(original_item['id'], check_item['id'])

        three_count_feed = self.get200(
                '/users/me/feed?exclusive_start_key={}&count=3'.format(feed[3]['id']),
                headers=headers)

        for original_item, check_item in zip(feed[4:7], three_count_feed):
            self.assertEqual(original_item['id'], check_item['id'])


    def test_following_and_unfollowing(self):
        users = []
        for x in xrange(10):
            user = self.create_user()
            if x % 2:
                headers = get_headers(user)
                self.photo_upload(headers=headers,
                                  set_as_profile_photo=True,
                                  enter_in_tournament=True)
            users.append(user)

        # Have each user follow the remaining users in the user list.
        follows_by_user = {}
        for i, user in enumerate(users):
            headers = get_headers(user)
            follows = follows_by_user[user.uuid] = []
            for followed_user in users[i+1:]:
                follows.append(followed_user.uuid)
                data = json.dumps({'followed': followed_user.uuid.hex})
                rv = self.application.post(app_url('/users/me/following'),
                                           data=data,
                                           headers=headers)
                # Stats could be in 401 if user does not have a photo.
                self.assertIn(rv.status, [u'200 OK', u'401 UNAUTHORIZED'])

        # Have each user unfollow the next user in the user list.
        for i, user in enumerate(users):
            if i == len(users) - 1:
                continue
            headers = get_headers(user)
            follows = follows_by_user[user.uuid]
            unfollow_user = users[i+1]
            follows.remove(unfollow_user.uuid)
            data = json.dumps({'followed': unfollow_user.uuid.hex})
            rv = self.application.delete(app_url('/users/me/following'),
                                         data=data,
                                         headers=headers)
            self.assert204(rv)
            if i % 2 != 0:
                # Is 404 if can't find.
                rv = self.application.delete(app_url('/users/me/following'),
                                             data=data,
                                             headers=headers)
                self.assert404(rv, 'Could not find Following object, Could not find Follower object')

        # Confirm the list of followed and following users is correct.
        # (these lists are not in order.)
        for i, user in enumerate(users):
            headers = get_headers(user)
            following = self.get200('/users/me/following', headers=headers)
            self.assertEqual(max(len(users) - i - 2, 0), len(following))
            following_hexes = [f['uuid'] for f in following]
            for followed_user in users[i+2:]:
                self.assertIn(followed_user.uuid.hex, following_hexes)
            for followed_user in users[:i]:
                self.assertNotIn(followed_user.uuid.hex, following_hexes)
            followers = self.get200('/users/me/followers', headers=headers)
            index = max(i-1, 0)
            self.assertEqual(index, len(followers))
            follower_hexes = [f['uuid'] for f in followers]
            for follower_user in users[:index]:
                self.assertIn(follower_user.uuid.hex, follower_hexes)
            for follower_user in users[index:]:
                self.assertNotIn(follower_user.uuid.hex, follower_hexes)
            # Here we confirm the individual checking feature of
            # /users/me/followers
            for follower_user in users[:index]:
                self.get204('/users/me/followers/%s' % follower_user.uuid.hex,
                            headers=headers)
            for follower_user in users[index:]:
                self.get404('/users/me/followers/%s' % follower_user.uuid.hex,
                            headers=headers)

    def test_notification_settings(self):
        user = create_user()
        user.show_gender_male = False
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = la_location.uuid
        user.save()

        self.assertTrue(user.notify_new_comment)
        self.assertTrue(user.notify_new_follower)
        self.assertTrue(user.notify_new_photo)
        self.assertTrue(user.notify_won_tournament)
        self.assertTrue(user.notify_you_won_tournament)

        # Change one.
        headers = get_headers(user)
        data = json.dumps({
            'new_comment': False
        })
        self.patch204('/users/me/notification_settings',
                      data=data, headers=headers)

        user.refresh()
        self.assertFalse(user.notify_new_comment)
        self.assertTrue(user.notify_new_follower)
        self.assertTrue(user.notify_new_photo)
        self.assertTrue(user.notify_won_tournament)
        self.assertTrue(user.notify_you_won_tournament)

        data = json.dumps({
            'new_comment': True
        })
        self.patch204('/users/me/notification_settings',
                      data=data, headers=headers)

        user.refresh()
        self.assertTrue(user.notify_new_comment)
        self.assertTrue(user.notify_new_follower)
        self.assertTrue(user.notify_new_photo)
        self.assertTrue(user.notify_won_tournament)
        self.assertTrue(user.notify_you_won_tournament)

        # Change a few at a time.
        headers = get_headers(user)
        data = json.dumps({
            'new_follower': False,
            'new_photo': False
        })
        self.patch204('/users/me/notification_settings',
                      data=data, headers=headers)

        user.refresh()
        self.assertTrue(user.notify_new_comment)
        self.assertFalse(user.notify_new_follower)
        self.assertFalse(user.notify_new_photo)
        self.assertTrue(user.notify_won_tournament)
        self.assertTrue(user.notify_you_won_tournament)

        data = json.dumps({
            'new_follower': True,
            'new_photo': True
        })
        self.patch204('/users/me/notification_settings',
                      data=data, headers=headers)

        user.refresh()
        self.assertTrue(user.notify_new_comment)
        self.assertTrue(user.notify_new_follower)
        self.assertTrue(user.notify_new_photo)
        self.assertTrue(user.notify_won_tournament)
        self.assertTrue(user.notify_you_won_tournament)

        # Change all at once.
        headers = get_headers(user)
        data = json.dumps({
            'new_comment': False,
            'new_follower': False,
            'new_photo': False,
            'won_tournament': False,
            'you_won_tournament': False
        })
        self.patch204('/users/me/notification_settings',
                      data=data, headers=headers)

        user.refresh()
        self.assertFalse(user.notify_new_comment)
        self.assertFalse(user.notify_new_follower)
        self.assertFalse(user.notify_new_photo)
        self.assertFalse(user.notify_won_tournament)
        self.assertFalse(user.notify_you_won_tournament)

        data = json.dumps({
            'new_comment': True,
            'new_follower': True,
            'new_photo': True,
            'won_tournament': True,
            'you_won_tournament': True
        })
        self.patch204('/users/me/notification_settings',
                      data=data, headers=headers)

        user.refresh()
        self.assertTrue(user.notify_new_comment)
        self.assertTrue(user.notify_new_follower)
        self.assertTrue(user.notify_new_photo)
        self.assertTrue(user.notify_won_tournament)
        self.assertTrue(user.notify_you_won_tournament)

        data = self.get200('/users/me/notification_settings',
                           headers=headers)

        self.assertDictEqual({
            u'new_comment': True,
            u'new_follower': True,
            u'new_photo': True,
            u'won_tournament': True,
            u'you_won_tournament': True
        }, data)

    # -- Flagging -------------------------------------------------------------

    def test_flag_user(self):
        location = uuid1()

        # Create test user.
        flaggee = create_user()
        url = '/users/%s/flags' % flaggee.uuid.hex

        # Log in.
        user = create_user()
        user.show_gender_male = True
        user.view_gender_male = False
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = location
        user.save()
        headers = headers_with_auth(user.uuid, user.token)

        data = "testreason"

        rv = self.application.post(app_url(url),
                                   data=data,
                                   headers=headers,
                                   environ_base={'REMOTE_ADDR': '127.0.0.1'})

        self.assertEqual('200 OK', rv.status)
        result = json.loads(rv.data)
        self.assertDictContainsSubset({
            'kind_id': 'User%s' % flaggee.uuid.hex,
            'user_id': user.uuid.hex,
            'reason': 'testreason'
        }, result)
        self.assertIn('created_on', result)
        self.assertIn('ip', result)

    def test_flag_photo(self):
        # Create test user.
        flaggee = create_user()
        flaggee.save()

        s = "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"
        geo = Geo.from_string(s)
        location = Location.from_geo(geo)

        photo = create_photo(flaggee, location, geo, True, True)
        photo.copy_complete = True
        photo.save()

        url = '/photos/%s/flags' % photo.uuid.hex

        # Log in.
        user = create_user()
        user.show_gender_male = True
        user.view_gender_male = False
        user.lat = la_geo.lat
        user.lon = la_geo.lon
        user.geodata = la_geo.meta
        user.location = location.uuid
        user.save()
        headers = headers_with_auth(user.uuid, user.token)

        data = "testreason"

        rv = self.application.post(app_url(url),
                                   data=data,
                                   headers=headers,
                                   environ_base={'REMOTE_ADDR': '127.0.0.1'})

        self.assertEqual('200 OK', rv.status)
        result = json.loads(rv.data)
        self.assertDictContainsSubset({
            'kind_id': 'Photo%s' % photo.uuid.hex,
            'user_id': user.uuid.hex,
            'reason': 'testreason'
        }, result)
        self.assertIn('created_on', result)
        self.assertIn('ip', result)

    def test_flag_comment(self):
        # Create test user.
        flaggee = create_user()
        flaggee.save()

        s = "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"
        geo = Geo.from_string(s)
        location = Location.from_geo(geo)

        photo = create_photo(flaggee, location, geo, True, True)
        photo.copy_complete = True
        photo.save()

        # Create some comments on photo.
        commenter1 = self.create_user(user_name='commenter1' + rand_string(5),
                                      gender='female')
        commenter2 = self.create_user(user_name='commenter2' + rand_string(5),
                                      gender='female')

        comment_url = '/photos/%s/comments' % photo.uuid.hex

        headers = headers_with_auth(commenter1.uuid, commenter1.token)
        headers.update(la_geo_header)

        result = self.post201(comment_url, data="comment 1", headers=headers)
        comment1_id = result['uuid']

        headers = headers_with_auth(commenter2.uuid, commenter2.token)
        headers.update(la_geo_header)

        self.post201(comment_url, data="comment 2", headers=headers)

        # Log in.
        user = self.create_user(gender='male',
                                user_name='test user ' + rand_string(5))
        headers = get_headers(user)

        data = "testreason"

        url = '/photo_comments/%s/flags' % comment1_id

        result = self.post200(url, data=data, headers=headers,
                              environ_base={'REMOTE_ADDR': '127.0.0.1'})
        self.assertDictContainsSubset({
            'kind_id': 'PhotoComment%s' % comment1_id,
            'user_id': user.uuid.hex,
            'reason': 'testreason'
        }, result)
        self.assertIn('created_on', result)
        self.assertIn('ip', result)


if __name__ == '__main__':
    unittest.main()
