from __future__ import division, absolute_import, unicode_literals


# -- Imports ------------------------------------------------------------------

from collections import OrderedDict
from functools import wraps
from itertools import chain, islice
from random import choice, randrange, shuffle
import traceback
import uuid

from flask import abort, Blueprint, jsonify, request
from flask.ext.restful.utils import unpack
from flask.ext.restplus import Api, fields, Resource
from flask.ext.restplus.utils import merge
from flask_restplus import reqparse
from pynamodb.models import DoesNotExist
from voluptuous import All, Any, Coerce, Length, Required, Schema

from apps.admin.admin import flag_comment, flag_photo, flag_user
from ocean_exceptions import InsufficientAuthorization, InvalidAPIUsage, \
    NotFound
from log import log
from logic import search
from logic import sentry
from logic import sns
from logic import tags
from logic.auth_token import current_api_user
from logic.facebook import get_facebook_id
from logic.feed import feed_activity, feed_new_comment, feed_tournament_win
from logic.location import Geo, get_location_name, Location
import model
from logic.photo import create_photo, get_photo
from logic.s3 import get_s3_connection
from logic.score import log_match_for_scoring
from logic.sqs import get_facebook_photo
from logic.stats import incr, timingIncrDecorator
from logic.tournament import create_match, get_next_tournament, \
    init_tournament_status, tournament_is_next, tournament_status_log_match
from logic.user import change_user_name, create_user, delete_user, get_user, \
    update_registration_status
from settings import settings
from util import count_iter, now, random_by_twos, unix_time

# -- Error Handlers -----------------------------------------------------------

# Note we raise Exceptions that parse into errors, but a more flask-centric
# way would be to use 'abort', but I'm not saying we should do that.
# http://stackoverflow.com/questions/12285903/flask-how-to-create-custom-abort-code

codes = {
    'FacebookError': 400,
    'InsufficientAuthorization': 401,
    'InvalidAPIUsage': 409,
    'MultipleInvalid': 400,
    'NotFound': 404,
}

class OceanApi(Api):
    """A class to allow us to have a custom 500 handler."""

    def handle_error(self, e):
        name = type(e).__name__
        if name in codes:
            code = codes[name]
        elif hasattr(e, 'code'):
            code = e.code
        else:
            code = 500
        message = '<could not make error message>'
        try:
            message = '%s: %s' % (name, e)
        except:
            pass
        data = {'message': message}
        if code == 500:      # for HTTP 500 errors return my custom response
            log.exception(e)
            try:
                tb = traceback.format_exc()
            except:
                tb = '<could not make traceback>'
            data['traceback'] = tb
            # Log 500 to statsd with unique code.
            tb_lines = tb.split('\n')
            last_ocean_line = None
            for tb_line in tb_lines:
                if settings.NAME in tb_line:
                    last_ocean_line = tb_line
            if not last_ocean_line:
                last_ocean_line = tb_lines[-1]
            incr('500 %s %s' % (request.url, last_ocean_line))
            # TODO: Stick the user in the Flask request context so we can
            # get it here if it is already there.
            # sentry_user_data = {
            #     'id': user.uuid.hex,
            #     'username': user.user_name,
            #   ...'email', 'ip_address'
            # }
            # sentry.get_client().user_context(sentry_user_data)

            data['sentry_event_id'] = sentry.get_client().captureException(
                tags={
                    'name': settings.NAME,
                    'mode': settings.MODE,
                    'version': settings.VERSION,
                    'is_worker': settings.IS_WORKER
                })
            data['sentry_public_dsn'] = sentry.get_client().client.get_public_dsn('https')

        response = jsonify(data)
        response.status_code = code
        log.debug("handling_error {}".format(message))
        return response

# -- Blueprint and API --------------------------------------------------------

api_blueprint = Blueprint('api', __name__, template_folder='templates')
api = OceanApi(api_blueprint, version=settings.VERSION, title='LocalPicTourney API',
          description='LocalPicTourney - Vote on the hottest people in your city',
              doc='/doc')

# -- Helpers ------------------------------------------------------------------

TEST_TAG_POSTFIX = 'testxxx'  # All tags ending with this string do not appear
# in the UI unless the request is given with HTTP params is_test=True

class ocean_unicode(unicode):
    """If we give voluptuous a regular 'unicode' type it barfs on emojis."""
    def __new__(cls, s):
        if isinstance(s, unicode):
            return s
        return unicode.__new__(cls, s, encoding='utf8')

def get_location_and_geo():
    """Get Location and Geo objects from the request's Geo-Position header."""
    position_header = request.headers.get('Geo-Position')
    if not position_header:
        raise InvalidAPIUsage('Geo-Position header not present')
    geo = Geo.from_string(position_header)
    location = Location.from_geo(geo)
    return location, geo

def reg_in(user, reg_statuses=['ok']):
    if not reg_statuses:
        raise ValueError('reg_status cannot be empty')
    if user.registration not in reg_statuses:
        if len(reg_statuses) == 1:
            msg = "User registration status was '{got}', needs to be '{need}'"
            msg = msg.format(got=user.registration, need=reg_statuses[0])
        else:
            msg = "User registration status was '{got}', needs to be in '{need}'"
            msg = msg.format(got=user.registration, need="', '".join(reg_statuses))
        raise InsufficientAuthorization(msg)

def reg_ok(user):
    """Aborts if user's reg status is not 'ok'"""
    reg_in(user, reg_statuses=['ok'])

# -- Models -------------------------------------------------------------------
# These api.model objects are used to marshal results. RestPlus generates
# swagger docs from these.

def _strip_nones(d):
    """Return copy of dict with None-valued keys removed (recursive.)"""
    if isinstance(d, (dict, OrderedDict)):
        new_result = {}
        for key, value in d.items():
            new_value = _strip_nones(value)
            if new_value:
                new_result[key] = new_value
        return new_result
    elif isinstance(d, list):
        return [x for x in map(_strip_nones, d) if x]
    else:
        return d

class marshal_with(object):
    """Wrapper that wraps flask_restplus.api and strips nulls from results."""
    def __init__(self, fields, as_list=False, code=200, description=None, envelope=None):
        self.fields = fields
        self.as_list = as_list
        self.code = code
        self.description = description
        self.envelope = envelope

    def __call__(self, f):
        # This is copied from flask_restplus.api.Api.marshal_with
        doc = {
            'responses': {
                self.code: (self.description, [self.fields]) if self.as_list else (self.description, self.fields)
            }
        }
        f.__apidoc__ = merge(getattr(f, '__apidoc__', {}), doc)
        resolved = getattr(self.fields, 'resolved', self.fields)
        @wraps(f)
        def wrapper(*args, **kwargs):
            # This is copied from flask_restful.marshal_with
            resp = f(*args, **kwargs)
            # What changed is that we strip nones from the result of marshal.
            if isinstance(resp, tuple):
                data, code, headers = unpack(resp)
                data = _strip_nones(marshal(data, resolved, self.envelope))
                return data, code, headers
            else:
                return _strip_nones(marshal(resp, resolved, self.envelope))
        return wrapper

from flask.ext.restful.fields import get_value, marshal

class NestedLookup(fields.Nested):
    """A Nested field with a 'getter' kwarg for getting sub-objects."""
    # NOTE: THIS IS NOT YET FUNCTIONAL.
    def __init__(self, model_obj, *args, **kwargs):
        #self.getter = kwargs['getter']
        #del kwargs['getter']
        self.model_obj = model_obj
        super(NestedLookup, self).__init__(*args, **kwargs)

    def output(self, key, obj):
        _key = key if self.attribute is None else self.attribute
        try:
            stashed_value = get_value('_' + _key, obj)
        except AttributeError:
            pass
        else:
            return marshal(stashed_value, self.nested)
        value = get_value(_key, obj)
        if value is None:
            if self.allow_null:
                return None
            elif self.default is not None:
                return self.default
        else:
            value = self.model_obj.get(value)
            setattr('_' + _key, value)
        return marshal(value, self.nested)

user_id_model = api.model('User ID Only', {
    'user_name': fields.String(description="user's name"),
    'first_name': fields.String(description="user's common screen name"),
    'last_name': fields.String(description="user's last name"),
    'website': fields.String(description="user's website"),
    'biography': fields.String(description="user's biography"),
    'uuid': fields.String(required=True, description='user uuid (hex)'),
})

version_model = api.model('Version', {
    'version': fields.String(required=True, description='server version')
})

new_user_model = api.model('NewUser', {
    'uuid': fields.String(required=True, description='hex uuid of new user'),
    'token': fields.String(required=True, description="new user's auth token")
})

photo_model = api.model('Photo', {
    'id': fields.String(required=True, description='uuid (hex) of photo'),
    'post_date': fields.Integer(
        required=True,
        description='unix time at which photo was posted'),
    'url_small': fields.String(
        required=True,
        description='url for small version of photo'),
    'url_medium': fields.String(
        required=True,
        description='url for medium version of photo'),
    'url_large': fields.String(
        required=True,
        description='url for large version of photo'),
    'url_original': fields.String(
        required=True,
        description='url for original version of photo'),
    'share_url': fields.String(
        required=True,
        description="external URL referring to this photo's share page"),
    'location': fields.String(
        required=True,
        description='uuid (hex) of location in which photo was posted'),
    'location_name': fields.String(
        required=True,
        description='name of location in which photo was posted'),
    'score': fields.Integer(required=True, description="photo's score"),
    'user': fields.Nested(user_id_model, required=True,
                          description='User who posted photo'),
    'user_name': fields.String(description='user name of user'),
    'first_name': fields.String(description='common screen name of user'),
    'last_name': fields.String(description='last name of user'),
    'tags': fields.List(
        fields.String(description='tags on this photo'),
    )
})

token_model = api.model('UserAuthToken', {
    'uuid': fields.String(required=True, description='hex uuid of user'),
    'token': fields.String(required=True,
                           description='token for use in auth header')
})

user_model = api.model('User', {
    'uuid': fields.String(required=True, description='hex uuid of user'),
    'user_name': fields.String(description='user name of user'),
    'first_name': fields.String(description='common screen name of user'),
    'last_name': fields.String(description='last name of user'),
    'biography': fields.String(description='biography of user'),
    'snapchat': fields.String(description='snapchat path of user'),
    'instagram': fields.String(description='instagram path of user'),
    'website': fields.String(description='user website'),
    'facebook_id': fields.String(description='facebook id for user'),
    'last_facebook_event_date': fields.Integer(
        description='unix time at which facebook event occurred'),
    'last_facebook_event': fields.String(
            description='result of last offline facebook activity'),
    'gender': fields.String(
        required=True,
        description="'male' if user posts male photos, 'female' if user posts female photos",
        enum=['male', 'female']),
    'view_gender': fields.String(
        required=True,
        description="'male' if user views male photos, 'female' if user views female photos",
        enum=['male', 'female']),
    'location': fields.String(
        description='hex uuid of user location, blank if no location'),
    'location_name': fields.String(
        description='name of user location, blank if no location'),
    'registered': fields.String(
        required=True,
        description='NOT IMPLEMENTED - True if registered'),
    'user_agent': fields.String(
        required=True,
        description='User-Agent header supplied when user registered'
    ),
    'photo': fields.Nested(photo_model, description='user profile photo'),
    'photos': fields.List(
        fields.Nested(photo_model,
                      required=True,
                      description='photos posted by this user')),
    'win_loss_ratio': fields.Integer(description='user win loss ratio')
})

location_model = api.model('Location', {
    'name': fields.String(required=True, description='name of location'),
    'id': fields.String(required=True, description='id of location')
})

user_photo_model = api.model('User Photo', {
    'url': fields.String(required=True, description='url of user photo')
})

comment_model = api.model('Photo Comment', {
    'uuid': fields.String(required=True, description='comment uuid (hex)'),
    'text': fields.String(required=True, description='text of comment'),
    'user_name': fields.String(required=True,
                               description="commenting user's user name"),
    'first_name': fields.String(required=True,
                                description="commenting user's screen name"),
    'last_name': fields.String(required=True,
                                description="commenting user's last name"),
    'user_uuid': fields.String(required=True,
                               description='commenting user uuid (hex)'),
    'url': fields.String(required=True, description='url of user photo'),
    'url_small': fields.String(
        required=True,
        description='url for small photo of commenting user'),
    'url_medium': fields.String(
        required=True,
        description='url for medium photo of commenting user'),
    'url_large': fields.String(
        required=True,
        description='url for large photo of commenting user'),
    'score': fields.Integer(required=True,
                            description="commenting user's photo's score"),
    'posted_at': fields.Integer(
        required=True,
        description='unix time at which comment was posted'),
    'location': fields.String(
        required=True,
        description='uuid (hex) of location in which comment was posted'),
    'location_name': fields.String(
        required=True,
        description='name of location in which comment was posted'),
    'gender': fields.String(
        required=True,
        description="'male' if user posts male photos, 'female' if user posts female photos",
        enum=['male', 'female'])
})

comment_list_model = api.model('Photo Comment List', {
    'comments': fields.List(fields.Nested(comment_model,
                                          required=True,
                                          description='photo comments'),
                            required=True)
})

match_model = api.model('Match', {
    'match_id': fields.String(required=True, description='match uuid (hex)'),
    't': fields.String(required=True,
                       description="type of match 'n'=normal (see tournament)"),
    'photo_a': fields.Nested(photo_model,
                             required=True,
                             description="'a' contestant photo in the Match"),
    'a_win_delta': fields.Integer(
        required=True,
        description="Estimated change to photo a's score if a wins"),
    'a_lose_delta': fields.Integer(
        required=True,
        description="Estimated change to photo a's score if a loses"),
    'photo_b': fields.Nested(photo_model,
                             required=True,
                             description="'b' contestant photo in the Match"),
    'a_win_delta': fields.Integer(
        required=True,
        description="Estimated change to photo b's score if b wins"),
    'a_lose_delta': fields.Integer(
        required=True,
        description="Estimated change to photo b's score if b loses")
})

tournament_model = api.model('Tournament', {
    't': fields.String(required=True,
                       description='tournament kind: local, regional, global'),
    'uuid': fields.String(required=True,
                          description='uuid (hex) of tournament'),
    'one': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'one'"),
    'two': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'two'"),
    'three': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'three'"),
    'four': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'four'"),
    'five': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'five'"),
    'six': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'six'"),
    'seven': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'seven'"),
    'eight': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'eight'"),
    'nine': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'nine'"),
    'ten': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'ten'"),
    'eleven': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'eleven'"),
    'twelve': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'twelve'"),
    'thirteen': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'thirteen'"),
    'fourteen': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'fourteen'"),
    'fifteen': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'fifteen'"),
    'sixteen': fields.Nested(photo_model, required=True,
                         description="photo in seed position 'sixteen'")
})

match_list_model = api.model('Match List', {
    'matches': fields.List(fields.Nested(
        match_model,
        required=True,
        description='Matches for auth user to judge'))
})

award_model = api.model('Award', {
    'uuid': fields.String(required=True,
                          description='uuid (hex) of award'),
    'photo_uuid': fields.String(required=True,
                                description='uuid (hex) of photo'),
    'kind': fields.String(required=True,
                          description='name of Award type'),
    'awarded_on': fields.Integer(
            required=True,
            description='unix time at which award was given')
})

# award_list_model = api.model('Awards List',
#                              fields.List(fields.Nested(award_model,
#                                   required=True,
#                                   description='photo awards'),
#                              required=True))

tournament_input_model = api.model('TournamentInput', {
    'one_vs_two': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'three_vs_four': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'five_vs_six': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'seven_vs_eight': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'nine_vs_ten': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'eleven_vs_twelve': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'thirteen_vs_fourteen': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'fifteen_vs_sixteen': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'one_two_vs_three_four': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'five_six_vs_seven_eight': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'nine_ten_vs_eleven_twelve': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'thirteen_fourteen_vs_fifteen_sixteen': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'one_four_vs_five_eight': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'nine_twelve_vs_thirteen_sixteen': fields.String(
        required=True,
        description="uuid (hex) of winner of this bracket"),
    'winner': fields.String(
        required=True,
        description="uuid (hex) of winner of this Tournament")})

leaderboard_model = api.model('Leaderboard', {
    'photos': fields.List(fields.Nested(photo_model,
                                        required=True,
                                        description='leaderboard photos'))
})

leaderboard_list_element = api.model('LeaderboardElement', {
    'name': fields.String(
        required=True,
        description='name of this Leaderboard (location)'),
    'gender_location': fields.String(
        required=True,
        description='gender_location of this Leaderboard'),
    'photos': fields.List(fields.Nested(photo_model,
                                        required=True,
                                        description='leaderboard photos'))
})

leaderboard_list_model = api.model('LeaderboardList', {
    'leaderboards': fields.List(fields.Nested(
                        leaderboard_list_element,
                        required=True,
                        description='Leaderboards available'))})

following_input_model = api.model('FollowingInput', {
    'followed': fields.String(
        required=True,
        description='UUID (hex) of user the auth user is now following.')
})

flag_model = api.model('Flag', {
    'kind_id': fields.String(
        required=True,
        description='table name and uuid (hex) of flagged item'),
    'user_id': fields.String(
        required=True,
        description='uuid (hex) of user who created flag'),
    'created_on': fields.Integer(
        required=True,
        description='unix time at which Flag was created'),
    'reason': fields.String(
        required=True,
        description='Reason item was flagged.'),
    'ip': fields.String(
        required=True,
        description='IP Address from which Flag was created.')
})

flag_input_model = api.model('FlagInput', {
    'reason': fields.String(
        required=True,
        description='Reason item was flagged')
})

notification_settings_model = api.model('NotificationSettings', {
    'new_photo': fields.Boolean(
        description="True if user wants 'NewPhoto' notifications",
        attribute='notify_new_photo'),
    'new_comment': fields.Boolean(
        description="True if user wants 'NewComment notifications",
        attribute='notify_new_comment'),
    'won_tournament': fields.Boolean(
        description="True if user wants 'WonTournament' notifications",
        attribute='notify_won_tournament'),
    'you_won_tournament': fields.Boolean(
        description="True if user wants 'YouWonTournament' notifications",
        attribute='notify_you_won_tournament'),
    'new_follower': fields.Boolean(
        description="True if user wants 'NewFollower notifications",
        attribute='notify_new_follower')
})

# -- Renderers ----------------------------------------------------------------
# These render PynamoDB model objects to dicts that are ready for marshaling
# (and JSON encoding.) A goal is to get the marshaller to do all of this
# declaratively.

def pscore(score):
    return int(score)

def render_user_info(user, photo=None, photos=None, for_others=True,
                     request_authorized=False):
    # for_others == True means no secret information is rendered, like tokens.
    # request_authorized == True means requesting user is fully registered
    #                       and can view semi-private fields like snapchat and
    #                       instagram.
    result = {
        'uuid': user.uuid.hex,
    }
    if user.is_test:
        result['is_test'] = True

    if not for_others:
        result['registered'] = user.registration
        result['user_agent'] = user.user_agent
        if user.view_gender_male is not None:
            if user.view_gender_male:
                result['view_gender'] = 'male'
            else:
                result['view_gender'] = 'female'
        if user.show_gender_male is not None:
            if user.show_gender_male:
                result['gender'] = 'male'
            else:
                result['gender'] = 'female'
        if user.location is not None:
            result['location'] = user.location.hex
            result['location_name'] = get_location_name(user.location)
        if user.facebook_api_token is not None:
            result['facebook_api_token'] = user.facebook_api_token
        if user.facebook_id is not None:
            result['facebook_id'] = user.facebook_id
        result['win_loss_ratio'] = user.get_win_loss_ratio()
        if user.last_facebook_event_date:
            result['last_facebook_event_date'] = unix_time(user.last_facebook_event_date)
        if user.last_facebook_event:
            result['last_facebook_event'] = user.last_facebook_event

    if photo is not None:
        rendered_photo = render_photo(photo, user)
        if rendered_photo is not None:
            result['photo'] = rendered_photo
    if photos:
        result['photos'] = [p for p in (render_photo(p, user) for p in photos) if p is not None]
    if user.user_name is not None:
        result['user_name'] = user.user_name
    if user.first_name is not None:
        result['first_name'] = user.first_name
    if user.last_name is not None:
        result['last_name'] = user.last_name
    if user.biography is not None:
        result['biography'] = user.biography
    if request_authorized or not for_others:
        if user.snapchat is not None:
            result['snapchat'] = user.snapchat
        if user.instagram is not None:
            result['instagram'] = user.instagram
    if user.website is not None:
        result['website'] = user.website
    return result

def render_comment(comment, user=None):
    if user is None:
        try:
            user = model.User.get(comment.user_uuid, consistent_read=False)
        except DoesNotExist:
            return None
    user_photo = user.get_photo()
    result = {
        'uuid': comment.uuid.hex,
        'user_uuid': user.uuid.hex,
        'text': comment.text,
        'posted_at': unix_time(comment.posted_at),
        'gender': 'male' if user.show_gender_male else 'female'
    }
    if user.user_name is not None:
        result['user_name'] = user.user_name
    if user.first_name is not None:
        result['first_name'] = user.first_name
    if user.last_name is not None:
        result['last_name'] = user.last_name
    if user_photo and user_photo.copy_complete:
        photo_url = '%s/%s_%%s' % (settings.SERVE_BUCKET_URL,
                                   user_photo.file_name)
        result['url_small'] = photo_url % '240x240'
        result['url_medium'] = photo_url % '480x480'
        result['url_large'] = photo_url % '960x960'
        try:
            score = user_photo.score
        except AttributeError:
            # ProfileOnlyPhotos do not have a score and raise this error.
            pass
        else:
            result['score'] = pscore(score)
    if comment.location:
        result['location'] = comment.location.hex
        result['location_name'] = get_location_name(comment.location)
    return result

# Photo render example
# photo_render_example = {
#     'id': uuid.hex,
#     'url_small': unicode,
#     'url_medium': unicode,
#     'url_large': unicode,
#     'location': uuid.hex,
#     'location_name': unicode,
#     'score': 1500.00,
#     'comment_count': 0,
#     'user': {
#         'uuid': unicode,
#         'gender': 'male' or 'female',
#         'win_loss_ratio': 50.0,
#         'user_name': unicode,
#         'first_name': unicode,
#         'last_name': unicode,
#         'photo': {
#             'id': uuid.hex,
#             'url_small': unicode,
#             'url_medium': unicode,
#             'url_large': unicode,
#             'location': uuid.hex,
#             'location_name': unicode,
#             'score': 1500.00,
#             'comment_count': 0
#             'user': {
#                 'uuid': unicode,
#                 'gender': 'male' or 'female',
#                 'win_loss_ratio': 50.0,
#                 'user_name': unicode,
#                 'first_name': unicode,
#                 'last_name': unicode,
#             },
#         },
#     }
# }
def render_photo(photo, user=None, render_user_photo=True):
    if user is None:
        try:
            user = model.User.get(photo.user_uuid, consistent_read=False)
        except DoesNotExist:
            return None
    photo_url = '%s/%s_%%s' % (settings.SERVE_BUCKET_URL, photo.file_name)
    user_render = {
        'uuid': user.uuid.hex
    }
    if user.show_gender_male is not None:
        user_render['gender'] = user.get_gender_string()
    user_render['win_loss_ratio'] = user.get_win_loss_ratio()
    if user.user_name is not None:
        user_render['user_name'] = user.user_name
    if user.first_name is not None:
        user_render['first_name'] = user.first_name
    if user.last_name is not None:
        user_render['last_name'] = user.last_name
    if user.website is not None:
        user_render['website'] = user.website
    if user.biography is not None:
        user_render['biography'] = user.biography
    if render_user_photo:
        user_photo = user.get_photo()
        if user_photo:
            user_photo_render = render_photo(user_photo,
                                             user=user,
                                             render_user_photo=False)
            user_render['photo'] = user_photo_render
    media_type = photo.media_type if photo.media_type is not None else 'photo'
    result = {
        'id': photo.uuid.hex,
        'post_date': unix_time(photo.post_date),
        'url_small': photo_url % '240x240',
        'url_medium': photo_url % '480x480',
        'url_large': photo_url % '960x960',
        'url_original': photo_url % 'original',
        'user': user_render,
        'media_type': media_type
    }
    if not photo.is_profile_only():
        result['share_url'] = photo.get_share_url()

    try:
        location = photo.location
    except AttributeError:
        # ProfileOnlyPhotos do not have a location and raise this error.
        pass
    else:
        if location:
            result['location'] = location.hex
            result['location_name'] = get_location_name(location)
    try:
        score = photo.score
    except AttributeError:
        # ProfileOnlyPhotos do not have a score and raise this error.
        pass
    else:
        result['score'] = pscore(score)
    comment_count = photo.get_comment_count()
    if comment_count is not None:
        result['comment_count'] = comment_count
    tags = photo.get_tags()
    if tags:
        result['tags'] = list(tags)
    return result


def render_tag(gender, tag_with_case, photo):
    data = {
        'tag': tag_with_case,
        'gender': gender
    }
    if photo is not None:
        data['cover_photo'] = render_photo(photo)
    return data


def render_tag_count(gender, tag_with_case, count):
    return {
        'tag': tag_with_case,
        'gender': gender,
        'count': str(count)
    }


def render_feed_activity(feed_activity, request_authorized=False):
    data = {
        'id': feed_activity.uuid.hex,
        'created_on': unix_time(feed_activity.created_on),
        'activity': feed_activity.activity,
        'read': feed_activity.read,
    }
    if feed_activity.user:
        data['user'] = render_user_info(get_user(feed_activity.user),
                                        request_authorized=request_authorized)
    if feed_activity.photo:
        data['photo'] = render_photo(get_photo(feed_activity.photo))
    if feed_activity.comment:
        data['comment'] = render_comment(model.get_one(model.PhotoComment,
                                                       'uuid_index',
                                                       feed_activity.comment,
                                                       consistent_read=False))
    return data

def render_match(photo_a, a_win_delta, a_lose_delta,
                 photo_b, b_win_delta, b_lose_delta):
    db_match_hex = photo_a.uuid.hex + photo_b.uuid.hex
    return {
        'match_id': db_match_hex,
        't': 'regular',
        'photo_a': render_photo(photo_a),
        'a_win_delta': pscore(a_win_delta),
        'a_lose_delta': pscore(a_lose_delta),
        'photo_b': render_photo(photo_b),
        'b_win_delta': pscore(b_win_delta),
        'b_lose_delta': pscore(b_lose_delta)
    }

def render_tournament(gender_location, tournament):
    # TODO: Broken - g_l arg won't work for cross regional tournament
    try:
        return {
            't': tournament.kind,
            'uuid': tournament.uuid.hex,
            'one': render_photo(model.Photo.get(gender_location, tournament.one)),
            'two': render_photo(model.Photo.get(gender_location, tournament.two)),
            'three': render_photo(model.Photo.get(gender_location,
                                                  tournament.three)),
            'four': render_photo(model.Photo.get(gender_location,
                                                 tournament.four)),
            'five': render_photo(model.Photo.get(gender_location,
                                                 tournament.five)),
            'six': render_photo(model.Photo.get(gender_location,
                                                tournament.six)),
            'seven': render_photo(model.Photo.get(gender_location,
                                                  tournament.seven)),
            'eight': render_photo(model.Photo.get(gender_location,
                                                  tournament.eight)),
            'nine': render_photo(model.Photo.get(gender_location,
                                                 tournament.nine)),
            'ten': render_photo(model.Photo.get(gender_location,
                                                tournament.ten)),
            'eleven': render_photo(model.Photo.get(gender_location,
                                                   tournament.eleven)),
            'twelve': render_photo(model.Photo.get(gender_location,
                                                   tournament.twelve)),
            'thirteen': render_photo(model.Photo.get(gender_location,
                                                     tournament.thirteen)),
            'fourteen': render_photo(model.Photo.get(gender_location,
                                                     tournament.fourteen)),
            'fifteen': render_photo(model.Photo.get(gender_location,
                                                    tournament.fifteen)),
            'sixteen': render_photo(model.Photo.get(gender_location,
                                                    tournament.sixteen)),
        }
    except DoesNotExist:
        return None

def render_wins(wins):
    result = []
    for win in wins:
        win_photo = get_photo(win.win_photo)
        lose_photo = get_photo(win.lose_photo)
        result.append(render_win(win, win_photo, lose_photo))

    return result

def render_win(win, win_photo, lose_photo):
    return {
        'win_photo': render_photo(win_photo),
        'lose_photo': render_photo(lose_photo),
        'created_on': unix_time(win.created_on),
        'id': win.uuid.hex
    }

def render_flag(flag):
    return {
        'kind_id': flag.kind_id,
        'user_id': flag.user_id.hex,
        'created_on': unix_time(flag.created_on),
        'reason': flag.reason,
        'ip': flag.ip
    }

def render_award(award):
    return {
        'uuid': award.uuid.hex,
        'photo_uuid': award.photo_uuid.hex,
        'kind': award.kind,
        'awarded_on': unix_time(award.awarded_on)
    }

# -- Schema -------------------------------------------------------------------
# These Voluptuous validation schema are used to clean and validate incoming
# data. They have no interaction with RestPlus or Swagger.

facebook_token_schema = Schema({
    Required('token'): All(unicode,
                           Length(min=1, max=10000)),#,
                           #Coerce(ocean_unicode)),
    Required('force_set_as_profile_photo', default=False): bool
}, required=False)

facebook_token_lookup_schema = Schema({
    Required('facebook_api_token'): All(unicode,
                           Length(min=1, max=10000)),
})

name_unicode_schema = Schema(All(unicode, Length(min=1)),
                             required=True)

name_schema = Schema(All(str,
                         Length(min=1, max=42),
                         Coerce(ocean_unicode)),
                     required=True)

geo_schema = Schema(All(str,
                        Length(min=1, max=255),
                        Coerce(Geo.from_string)),
                    required=True)

gender_schema = Schema(Any('male', 'female'), required=True)

token_schema = Schema(All(str,
                          Length(min=1, max=255),
                          Coerce(ocean_unicode)),
                      required=True)

photo_schema = Schema({
    Required('set_as_profile_photo', default=False): bool,
    Required('enter_in_tournament', default=True): bool,
    'tags': [All(unicode, Length(min=1))],
    Required('media_type', default='photo'): Any('photo', 'movie')
})

comment_schema = Schema(All(str,
                            Length(min=1, max=300),
                            Coerce(ocean_unicode)),
                        required=True)

uuid_schema = Schema(All(unicode,
                         Length(32),
                         Coerce(uuid.UUID)),
                     required=True)

user_info_schema = Schema({
    'gender': Any('male', 'female'),
    'view_gender': Any('male', 'female'),
    'user_name': All(unicode, Length(min=1, max=42)),
    'first_name': All(unicode, Length(min=1, max=255)),
    'last_name': All(unicode, Length(min=0, max=255)),
    'location': All(unicode, Length(min=1, max=255), Coerce(Geo.from_string)),
    'token': All(unicode, Length(min=1, max=255)),
    'biography': All(unicode, Length(min=0, max=255)),
    'snapchat': All(unicode, Length(min=0, max=255)),
    'instagram': All(unicode, Length(min=0, max=255)),
    'website': All(unicode, Length(min=0, max=255)),
    'apn_device_id': All(unicode, Length(min=0, max=2000)),
})

vote_schema = Schema(Any('a', 'b'), required=True)

match_id_schema = Schema(All(unicode, Length(64)), required=True)

tournament_schema = Schema({
    'one_vs_two': All(unicode, Length(32), Coerce(uuid.UUID)),
    'three_vs_four': All(unicode, Length(32), Coerce(uuid.UUID)),
    'five_vs_six': All(unicode, Length(32), Coerce(uuid.UUID)),
    'seven_vs_eight': All(unicode, Length(32), Coerce(uuid.UUID)),
    'nine_vs_ten': All(unicode, Length(32), Coerce(uuid.UUID)),
    'eleven_vs_twelve': All(unicode, Length(32), Coerce(uuid.UUID)),
    'thirteen_vs_fourteen': All(unicode, Length(32), Coerce(uuid.UUID)),
    'fifteen_vs_sixteen': All(unicode, Length(32), Coerce(uuid.UUID)),
    'one_two_vs_three_four': All(unicode, Length(32), Coerce(uuid.UUID)),
    'five_six_vs_seven_eight': All(unicode, Length(32), Coerce(uuid.UUID)),
    'nine_ten_vs_eleven_twelve': All(unicode, Length(32), Coerce(uuid.UUID)),
    'thirteen_fourteen_vs_fifteen_sixteen': All(unicode, Length(32), Coerce(uuid.UUID)),
    'one_four_vs_five_eight': All(unicode, Length(32), Coerce(uuid.UUID)),
    'nine_twelve_vs_thirteen_sixteen': All(unicode, Length(32), Coerce(uuid.UUID)),
    'winner': All(unicode, Length(32), Coerce(uuid.UUID))}, required=True)

following_schema = Schema({'followed' : All(unicode, Length(32), Coerce(uuid.UUID))})

flag_schema = Schema(All(str, Length(min=1, max=255)), required=True)

notification_settings_schema = Schema({
    'new_photo': bool,
    'new_comment': bool,
    'won_tournament': bool,
    'you_won_tournament': bool,
    'new_follower': bool
})

# -- Misc Resources -----------------------------------------------------------

ns_test = api.namespace('misc',
                        description='Health-Check and System Operations',
                        path='/')

@ns_test.route('/test')
class Test(Resource):
    @timingIncrDecorator('GET /test')
    #@marshal_with(version_model)
    def get(self):
        """Health check"""
        # Check Dynamodb
        result = {'version': settings.VERSION}

        fail = False
        log.info("IN HEALTH CHECK /test")
        log.info("CHECK dynamodb")
        log.info("LOCATION DB HOST setting {}".format(settings.LOCATION_DB_HOST))
        try:
            model.assert_model()
        except Exception as e:
            log.exception(e)
            msg = str(e)
            result['dynamodb'] = msg
            fail = True
        else:
            result['dynamodb'] = 'OK'

        log.info("CHECK s3")
        if settings.S3_ENABLED:
            try:
                from logic import s3
                s3.assert_connection()
            except Exception as e:
                log.exception(e)
                msg = str(e)
                result['s3'] = msg
                fail = True
            else:
                result['s3'] = 'OK'

        log.info("CHECK SQS")
        if settings.SQS_ENABLED:
            try:
                from logic import sqs
                sqs.assert_connection()
            except Exception as e:
                log.exception(e)
                msg = str(e)
                result['sqs'] = msg
                fail = True
            else:
                result['sqs'] = 'OK'

        log.info("CHECK kinesis")
        if settings.KINESIS_ENABLED:
            try:
                from logic import kinesis
                kinesis.assert_connection()
            except Exception as e:
                log.exception(e)
                msg = str(e)
                result['kinesis'] = msg
                fail = True
            else:
                result['kinesis'] = 'OK'

        # log.info("CHECK location db")
        # if settings.LOCATION_DB_ENABLED:
        #     try:
        #         from logic import location
        #         location.assert_connection()
        #     except Exception as e:
        #         log.exception(e)
        #         msg = str(e)
        #         result['location'] = msg
        #         fail = True
        #     else:
        #         result['location'] = 'OK'
        #
        #     # NOTE: Search runs on the Location DB's Postgres connection,
        #     # therefore we only test it when LOCATION_DB_ENABLED = True
        #     log.info("check search")
        #     try:
        #         from logic import search
        #         search.assert_connection()
        #     except Exception as e:
        #         log.exception(e)
        #         msg = str(e)
        #         result['search'] = msg
        #         fail = True
        #     else:
        #         result['search'] = 'OK'

        log.info('return for test')
        log.info(fail)
        log.info(result)
        result['test'] = True
        if fail:
            return result, 400
        return result

@ns_test.route('/test_auth')
class TestAuth(Resource):
    @timingIncrDecorator('GET /test_auth')
    @marshal_with(version_model)
    def get(self):
        """Health Check that requires auth."""
        current_api_user()
        return {'version': settings.VERSION}

@ns_test.route('/config')
class Config(Resource):
    @timingIncrDecorator('GET /config')
    @marshal_with(version_model)
    def get(self):
        """Get global config info for clients.  (currently there is none.)"""
        return {'version': settings.VERSION}

@ns_test.route('/test_500')
class Test500(Resource):
    @timingIncrDecorator('GET /test_500')
    def get(self):
        """Raises a 500 to demonstrate error handling."""
        {}['intentionally_missing_key']

@ns_test.route('/test_location_db')
class TestLocationDb(Resource):
    @timingIncrDecorator('GET /test_location_db')
    def get(self):
        """Location DB accessible if 200"""
        la_str = "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"
        geo = Geo.from_string(la_str)
        location = Location.from_geo(geo)
        results = {
            'location': location.uuid.hex,
            'location_name': location.accent_city
        }
        return results

@ns_test.route('/init_location_db')
class InitLocationDb(Resource):
    @timingIncrDecorator('GET /init_location_db')
    def get(self):
        """Call init routine on location db."""
        # Not automaticly called so as to avoid thundering herd.
        from logic import location
        location._init_location_db()

@ns_test.route('/test_search_db')
class TestSearchDb(Resource):
    @timingIncrDecorator('GET /test_search_db')
    def get(self):
        """Search DB accessible if 200"""
        from logic import search
        search.search('f', 'local')
        return 'OK'

@ns_test.route('/init_search_db')
class InitSearchDb(Resource):
    @timingIncrDecorator('GET /init_search_db')
    def get(self):
        """Call init routine on search db."""
        # Not automaticly called so as to avoid thundering herd.
        from logic import search
        search._drop_search_db()
        search._init_search_db()

@ns_test.route('/test_push')
class TestNotification(Resource):
    @timingIncrDecorator('POST /test_push')
    def post(self):
        """POST data will be sent to auth user as apn push notification.

        User must have a valid apn_device_id, which is set using
        PATCH /users/me {'apn_device_id': 12345}

        """
        user = current_api_user()
        # TODO: This should be reg_ok on production.
        #reg_ok(user)
        from logic.sns import send_push
        send_push(user, request.data)

@ns_test.route('/test_push_new_photo')
class TestNotification(Resource):
    @timingIncrDecorator('POST /test_push_new_photo')
    def post(self):
        """Send posting user a New Photo push message, as if they were a
        user they were following who had posted a new photo.

        User must have a valid apn_device_id, which is set using
        PATCH /users/me {'apn_device_id': 12345}

        """
        user = current_api_user()
        # TODO: This should be reg_ok on production.
        #reg_ok(user)

        # Get a photo, any photo will do, so long as it's not yours.
        photo = None
        for p in model.Photo.all_score_index.query('all', limit=20,
                                                   consistent_read=False):
            photo = p
            if photo.user_uuid != user.uuid:
                break

        photo_user = model.User.get(photo.user_uuid)

        # The usual SNS call distributes to your followers, so we simulate
        # the message just to the calling user.
        from logic.sns import _send_push_new_photo
        _send_push_new_photo(user, photo_user.user_name, photo.user_uuid,
                             photo.uuid)

@ns_test.route('/test_push_new_comment')
class TestNotification(Resource):
    @timingIncrDecorator('POST /test_push_new_comment')
    def post(self):
        """Send posting user a New Comment push message, as if they were
        the commenter.

        User must have a valid apn_device_id, which is set using
        PATCH /users/me {'apn_device_id': 12345}

        """
        user = current_api_user()
        # TODO: This should be reg_ok on production.
        #reg_ok(user)

        # Get a comment, any comment will do, so long as it's not yours.
        comment = None
        q = model.Photo.all_score_index.query('all', limit=200,
                                              consistent_read=False)
        done = False
        while not done:
            try:
                photo = q.next()
            except StopIteration:
                break
            for c in model.PhotoComment.query(photo.uuid, limit=200,
                                              consistent_read=False):
                comment = c
                if comment.user_uuid != user.uuid:
                    done = True
        commenter = model.User.get(comment.user_uuid)

        from logic.sns import _send_push_new_comment
        _send_push_new_comment(user, commenter.user_name, comment.user_uuid,
                               comment.photo_uuid, comment.uuid)

@ns_test.route('/test_push_new_follower')
class TestNotification(Resource):
    @timingIncrDecorator('POST /test_push_new_follower')
    def post(self):
        """Send posting user a New Follower push message, as if they
        are their new follower.

        User must have a valid apn_device_id, which is set using
        PATCH /users/me {'apn_device_id': 12345}

        """
        user = current_api_user()
        # TODO: This should be reg_ok on production.
        #reg_ok(user)

        # Get a user, any user will do, so long as it's not you.
        follower_uuid = None
        for p in model.Photo.all_score_index.query('all', limit=20,
                                                   consistent_read=False):
            follower_uuid = p.user_uuid
            if follower_uuid != user.uuid:
                break
        follower = model.User.get(follower_uuid)

        from logic.sns import _send_push_new_follower
        _send_push_new_follower(user, follower.user_name, follower_uuid)

# @ns_test.route('/snscallback')
# class SNSCallback(Resource):
#     @timingIncrDecorator('GET /snscallback')
#     @marshal_with(version_model)
#     def get(self):
#         log.info("GET snscallback")
#         return {'version': settings.VERSION}
#
#     @timingIncrDecorator('POST /snscallback')
#     @marshal_with(version_model)
#     def post(self):
#         log.info("POST snscallback")
#         message_type = request.headers.get('x-amz-sns-message-type')
#         log.info("AWS message type %s" % message_type)
#         try:
#             data = request.json
#             log.info(request.json)
#         except:
#             data = {}
#             log.info("/snscallback could not get request.json")
#         if message_type == 'SubscriptionConfirmation':
#             pass
#         elif message_type == 'Notification':
#             pass
#         elif message_type == 'UnsubscribeConfirmation':
#             pass
#         return {'version': settings.VERSION}

# -- User ---------------------------------------------------------------------

ns_user = api.namespace('user', description='User operations', path='/')

test_parser = reqparse.RequestParser()
test_parser.add_argument('is_test',
                         type=str,
                         help="value 'True' means object created is considered a test object",
                         required=False,
                         location='values',
                         default='')


@ns_user.route('/users')
class UserList(Resource):
    @timingIncrDecorator('POST /users')
    @marshal_with(new_user_model, code=201)
    def post(self):
        """Generate a user record and a token for a new user."""
        is_test = test_parser.parse_args().get('is_test', '') == 'True'
        new_user = create_user(user_agent=request.headers.get('User-Agent'),
                               is_test=is_test)

        return {'uuid': new_user.uuid.hex,
                'token': new_user.token}, 201

@ns_user.route('/users_by_facebook/<string:facebook_id>')
@api.doc(params={'token': "facebook api token of user to get"})
class UserByFacebook(Resource):
    @timingIncrDecorator('GET /users_by_facebook')
    @marshal_with(token_model)
    def get(self, facebook_id):
        """Get user auth token from user's facebook id."""
        # TODO ESCAPE THIS! It goes into a facebook parameter, be sure it is safe.
        facebook_auth_token = request.args.get('token')

        got_facebook_id = get_facebook_id(facebook_auth_token)
        if got_facebook_id is None:
            raise InvalidAPIUsage('Could not get facebook id for token')

        if got_facebook_id != facebook_id:
            raise InsufficientAuthorization('Facebook token did not match facebook id')

        got = model.get_one(model.User, 'facebook_id_index', facebook_id,
                            consistent_read=False)
        if not got:
            raise NotFound('Could not find user with that facebook id')

        return got

@ns_user.route('/users/<string:user_uuid_str>')
@api.doc(params={'user_uuid_str': "UUID (hex) of User to update or 'me' for the User in the Auth header"})
class User(Resource):
    @timingIncrDecorator('GET /users/me')
    @marshal_with(user_model)
    def get(self, user_uuid_str):
        """Get info about the specified user.

        Auth header required.

        """
        # TODO: Could certain requirements be bundled with the entity that
        # adds them to the docs? -- like, a decorator that adds the
        # auth header check, and also adds that to the docs.
        # Could I patent that? (ha ha, j/k)
        auth_user = current_api_user()
        if 'me' == user_uuid_str:
            user = auth_user
        else:
            try:
                user = model.User.get(uuid_schema(user_uuid_str),
                                      consistent_read=False)
            except DoesNotExist:
                abort(404)

        photo = user.get_photo()
        photos = list(model.Photo.user_index.query(user.uuid, limit=21,
                                                   scan_index_forward=False,
                                                   copy_complete__eq=True,
                                                   consistent_read=False))
        if any(p for p in photos if not p.copy_complete):
            log.debug("copy_complete__eq not working, photos with copy_complete!=True in users/uuid-query results")
        # TODO: This is an example of a multithreaded loading/rendering
        # opportunity.
        photos = [p for p in photos if p.copy_complete][:20]
        # We do this to fix a race on DynamoDB writes.
        update_registration_status(user)

        # TODO: Provision - this caused a provision error on the API server.
        return render_user_info(user, photo, photos,
                                for_others=(auth_user != user),
                                request_authorized=auth_user.registration=='ok')

    @timingIncrDecorator('PATCH /users/me')
    @api.doc(description="""Body of PATCH may contain any or all of:
    {
        'gender': 'male' or 'female', set gender of this user,
         'view_gender': 'male' or 'female', set gender of photos this user votes on,
         'user_name': new name for this user, pending uniqueness check,
         'first_name': new first name of user,
         'last_name': new last name of user,
         'location': location metadata, sets this user's location,
         'token': new auth token for this user,
         'biography': set this users biography,
         'instagram': set this users instagram token,
         'snapchat': set this users snapchat token,
         'website': set this user's personal website,
         'apn_device_id': device id for push notification,
    }
    """,
             responses={415: 'Content-Type header must be application/json',
                        204: 'User updated'},
             params={'user_uuid_str': "UUID (hex) of User to update or 'me' for the User in the Auth header"})
    def patch(self, user_uuid_str):
        """Update info about the specified user.

        Auth header required.

        """
        auth_user = current_api_user()
        if 'me' == user_uuid_str:
            user = auth_user
        else:
            try:
                user = model.User.get(uuid_schema(user_uuid_str),
                                      consistent_read=False)
            except DoesNotExist:
                abort(404)

        if auth_user != user:
            msg = "user_uuid_str '%s' must match '%s' found in auth header."
            msg = msg % (user_uuid_str, auth_user.uuid.hex)
            log.debug(msg)
            raise InvalidAPIUsage(msg)

        if 'application/json' != request.headers.get('Content-Type'):
            abort(415)

        if 'me' == user_uuid_str:
            user = auth_user
        else:
            try:
                user = model.User.get(uuid_schema(user_uuid_str))
            except DoesNotExist:
                abort(404)

        data = request.json
        data = user_info_schema(data)
        results = {}

        gender = data.get('gender')
        if gender:
            user.show_gender_male = (gender == 'male')
            user.save()
        view_gender = data.get('view_gender')
        if view_gender:
            user.view_gender_male = (view_gender == 'male')
            user.save()
        name = data.get('user_name')
        if name:
            change_user_name(user, name)
        first_name = data.get('first_name')
        if first_name:
            user.first_name = first_name
            user.save()
        last_name = data.get('last_name')
        if last_name is not None:
            user.last_name = last_name
            user.save()

        geo = data.get('location')
        if geo:
            user.lat = geo.lat
            user.lon = geo.lon
            user.geodata = geo.meta
            location = Location.from_geo(geo)
            user.location = location.uuid
            results.update({
                'location': location.uuid.hex,
                'location_name': location.accent_city
            })

        token = data.get('token')
        if token:
            user.token = token

        biography = data.get('biography')
        if biography is not None:
            user.biography = biography

        instagram = data.get('instagram')
        if instagram is not None:
            user.instagram = instagram

        snapchat = data.get('snapchat')
        if snapchat is not None:
            user.snapchat = snapchat

        website = data.get('website')
        if website is not None:
            user.website = website

        apn_device_id = data.get('apn_device_id')
        if apn_device_id is not None:
            user.apn_device_id = apn_device_id

        update_registration_status(user)
        user.save()

        if results:
            return results
        else:
            return None, 204

    @timingIncrDecorator('DELETE /users/me')
    @api.doc(responses={204: 'User deleted'})
    def delete(self, user_uuid_str):
        """Delete the auth User.

        Auth header required.

        Note: must actually call DELETE /users/me, DELETE/users/(user_uuid) not supported

        Makes re-registering with the same data possible.

        disable auth for that user so accidental usage of the old token will appear as an error to the client.
        dis-associate the facebook_id so registering a new one won't make for ambiguous lookups
        delete the username so a preferred username can be re-used.
        """
        auth_user = current_api_user()
        if 'me' == user_uuid_str:
            user = auth_user
        else:
            try:
                user = model.User.get(uuid_schema(user_uuid_str),
                                      consistent_read=False)
            except DoesNotExist:
                abort(404)

        if auth_user != user:
            msg = "user_uuid_str '%s' must match '%s' found in auth header."
            msg = msg % (user_uuid_str, auth_user.uuid.hex)
            log.debug(msg)
            raise InvalidAPIUsage(msg)

        delete_user(user)

        return None, 204


user_photos_parser = reqparse.RequestParser()
user_photos_parser.add_argument('exclusive_start_key',
                         type=str,
                         help='uuid (hex) of photo to start after, blank for beginning',
                         required=False,
                         location='values',
                         default='')
user_photos_parser.add_argument('count',
                         type=int,
                         help='number of results to return',
                         required=False,
                         location='values',
                         default=25)
@ns_user.route('/users/<string:user_uuid_str>/photos')
@api.doc(params={'user_uuid_str': "UUID (hex) of User or 'me' for the User in the Auth header"})
class UserPhotoList(Resource):
    @timingIncrDecorator('GET /users/me/photos')
    @api.doc(description="""Returns list of user Photos

    /users/me/photos?exclusive_start_key=b073eec2f10b11e5b312c8e0eb16059b?count=25

    default params are
    exclusive_start_key=''
    count=25

    }
    """)
    def get(self, user_uuid_str):
        """Get photos for the specified user.

        Auth header required.

        """
        auth_user = current_api_user()
        if 'me' == user_uuid_str:
            user = auth_user
        else:
            try:
                user = model.User.get(uuid_schema(user_uuid_str),
                                      consistent_read=False)
            except DoesNotExist:
                abort(404)

        args = user_photos_parser.parse_args()
        exclusive_start_key = args['exclusive_start_key']
        count = args['count']

        def exclusive_start_key_check(i):
            if exclusive_start_key == '':
                while True:
                    yield i.next()
            else:
                while True:
                    item = i.next()
                    if exclusive_start_key == item.uuid.hex:
                        for item in i:
                            yield item

        # scan_index_forward=False returns most recent first.
        # (scan_index_forward default is None, which is treated as True by
        # DynamoDB, which returns results in ascending order)
        photos = model.Photo.user_index.query(user.uuid,
                                              scan_index_forward=False,
 #                                             last_evaluated_key=start_key,
                                              copy_complete__eq=True,
                                              consistent_read=False)

        photos = count_iter(
                exclusive_start_key_check(
                        query_filter_check(photos, "GET /users/me/photos")),
                count)

        return [render_photo(p, user) for p in photos]


@ns_user.route('/user_names/<string:user_name_str>')
@api.doc(responses={404: 'Username available', 204: 'User name in use'},
         params={'user_name_str': 'User name to check'})
class UserName(Resource):
    @timingIncrDecorator('GET /user_names/*')
    def get(self, user_name_str):
        """Return 404 if username is available, 204 if not."""
        user_name_str = name_unicode_schema(user_name_str)
        if not user_name_str:
            abort(400)
        try:
            model.UserName.get(user_name_str, consistent_read=False)
        except DoesNotExist:
            abort(404)
        return None, 204

@ns_user.route('/users_by_name/<string:user_name_str>')
@api.doc(responses={404: 'User not found', 200: 'User info'},
         params={'user_name_str': 'Get the User with this user_name, if any'})
class UsersByName(Resource):
    @timingIncrDecorator('GET /users_by_name/*')
    def get(self, user_name_str):
        """Find user info by user_name.

        an auth header and registration status 'ok' required.

        """
        user = current_api_user()
        reg_ok(user)
        user_name_str = name_unicode_schema(user_name_str)
        if not user_name_str:
            abort(400)
        try:
            user_name = model.UserName.get(user_name_str,
                                           consistent_read=False)
        except DoesNotExist:
            abort(404)

        try:
            name_user = model.User.get(user_name.user_uuid,
                                       consistent_read=False)
        except DoesNotExist:
            abort(404)

        return render_user_info(name_user, request_authorized=True)

@ns_user.route('/users/me/facebook')
class UserFacebook(Resource):
    @timingIncrDecorator('PUT /users/me/facebook')
    def put(self):
        """Register user with facebook using given token, queue photo snarf.

        facebook token is body of post, input is
        {
         'token': (api token),
         (optional, default=False)'force_set_as_profile_photo': False
        }

        The user's facebook profile photo will become their ocean profile
        photo if they do not already have a ocean profile photo or if
        force_set_as_profile_photo is True

        Auth header required.

        """
        user = current_api_user()
        if 'application/json' != request.headers.get('Content-Type'):
            abort(415)
        data = facebook_token_schema(request.json)
        token = data['token']
        force_set_as_profile_photo = data['force_set_as_profile_photo']

        # Check if we already have a user using this id.
        facebook_id = get_facebook_id(token)
        if facebook_id is None:
            raise InvalidAPIUsage('Facebook did not recognize token')

        got = model.get_one(model.User, 'facebook_id_index', facebook_id)

        if got and got.uuid != user.uuid:
            raise InvalidAPIUsage('Already have user with this facebook id')

        user.facebook_id = facebook_id
        user.facebook_api_token = token
        update_registration_status(user)
        user.save()
        # This validates, and gets the facebook_photo if necessary.
        get_facebook_photo(user.uuid, force_set_as_profile_photo)
        return None, 204

@ns_user.route('/users/me/photos/small')
@api.doc(responses={404: 'No photo for user'})
class UserPhotoSmall(Resource):
    @timingIncrDecorator('GET /users/me/photos/small')
    @marshal_with(user_photo_model)
    def get(self):
        """Get URL of photo's small sized image.

        Auth header required.

        """
        user = current_api_user()
        photo = user.get_photo()
        if photo and photo.copy_complete:
            return {'url': '%s/%s_240x240' % (settings.SERVE_BUCKET_URL,
                                              photo.file_name)}
        abort(404)

@ns_user.route('/users/me/photos/medium')
class UserPhotoMedium(Resource):
    @timingIncrDecorator('GET /users/me/photos/medium')
    @marshal_with(user_photo_model)
    def get(self):
        """Get URL of photo's medium sized image.

        Auth header required.

        """
        user = current_api_user()
        photo = user.get_photo()
        if photo and photo.copy_complete:
            return {'url': '%s/%s_480x480' % (settings.SERVE_BUCKET_URL,
                                              photo.file_name)}
        abort(404)

@ns_user.route('/users/me/photos/game')
class UserPhotoGame(Resource):
    @timingIncrDecorator('GET /users/me/photos/game')
    @marshal_with(user_photo_model)
    def get(self):
        """Get URL of photo's game sized image.

        Auth header required.

        """
        user = current_api_user()
        photo = user.get_photo()
        if photo and photo.copy_complete:
            return {'url': '%s/%s_960x960' % (settings.SERVE_BUCKET_URL,
                                              photo.file_name)}
        abort(404)

@ns_user.route('/users/me/photos/original')
class UserPhotoGame(Resource):
    @timingIncrDecorator('GET /users/me/photos/original')
    @marshal_with(user_photo_model)
    def get(self):
        """Get URL of photo's original image.

        Auth header required.

        """
        user = current_api_user()
        photo = user.get_photo()
        if photo and photo.copy_complete:
            return {'url': '%s/%s_original' % (settings.SERVE_BUCKET_URL,
                                               photo.file_name)}
        abort(404)

PERSONAL_ACTIVITIES = ['Joined', 'YouWonTournament', 'NewFollower',
                       'NewComment']
@ns_user.route('/users/me/activity')
class Activity(Resource):
    @api.doc(description="""Returns a list of:
    {
        'activity': activity type, 'new follower' or 'joined'
        'id': unique id (uuid) of activity item,
        'created_on': time of activity,
        'read': True if item has not appeared in a previous GET request
        (optional) 'description': text description of activity,
        (optional) 'user' : standard API photo rendering of user this activity pertains to
    }

    Auth header required.
""")
    @timingIncrDecorator('GET /users/me/activity')
    def get(self):
        """Activity history for this user."""
        user = current_api_user()
        activity = model.FeedActivity.query(user.uuid,
                                            scan_index_forward=False,
                                            limit=200, consistent_read=False)
        activity = [a for a in activity if a.activity in PERSONAL_ACTIVITIES]
        auth = user.registration == 'ok'
        rendering = [render_feed_activity(a, request_authorized=auth) for a in activity]
        for f in activity:
            if not f.read:
                f.read = True
                f.save()
        return rendering


# -- Photo --------------------------------------------------------------------

ns_photo = api.namespace('photo', description='Photo operations', path='/')

@ns_photo.route('/photos')
class Photo(Resource):
    @timingIncrDecorator('POST /photos')
#    @marshal_with(photo_upload_model)
    @api.doc(description="""
POST arguments
'set_as_profile_photo':
    'True if this photo is to be the users new profile photo, default False',
'enter_in_tournament':
    'True if this photo is to be entered in match-judging, default True'
either set_as_profile_photo or enter_in_tournament must be True or InvalidAPIUsage is raised
'tags' (optional): a list of unicode tags to apply to this photo. tags ignored if enter_in_tournament is False.
'media_type': 'photo' or 'movie', default is 'photo'

 Return looks like this:
{'action': 'http://ocean-inbox.s3.amazonaws.com/',
 'fields': [
 {'name': 'x-amz-storage-class', 'value': 'STANDARD'},
 {'name': 'policy',
  'value': 'aoeutshaoesuthaosetuhasoetuhasoteuhsatoheu='},
 {'name': 'AWSAccessKeyId', 'value': 'aoesuthasoeuthasoetuh'},
 {'name': 'signature', 'value': u'aoesuthasoeuthasoetuh='},
 {'name': 'key', 'value': 'test1.jpg'}]}

 if set_as_profile=True, an auth header is required
 if set_as_profile=False, an auth header and registration status 'ok' required.

 return HTTP code 202 on success

 """)
    def post(self):
        """Create a new photo record and get a signed URL for upload.        """
        # see: http://stackoverflow.com/questions/10044151/
        #           how-to-generate-a-temporary-url-to-upload-file-to-
        #           amazon-s3-with-boto-library
        # Post a new photo, get back an s3 upload URL.
        # When you create a pre-signed URL, you must provide your security
        # credentials, specify a bucket name an object key, an HTTP method
        # (PUT of uploading objects) and an expiration date and time. The
        # pre-signed URLs are valid only for the specified duration.
        if 'application/json' != request.headers.get('Content-Type'):
            abort(415)

        is_test = test_parser.parse_args().get('is_test', '') == 'True'

        if request.data:
            input = request.json
        else:
            input = {}
        groomed_input = photo_schema(input)
        set_as_profile_photo = groomed_input['set_as_profile_photo']
        enter_in_tournament = groomed_input['enter_in_tournament']
        media_type = groomed_input['media_type']
        tags = groomed_input.get('tags', [])
        if(not set_as_profile_photo and not enter_in_tournament):
            m = 'set_as_profile_photo and/or enter_in_tournament must be True'
            log.debug(m)
            raise InvalidAPIUsage(m)

        user = current_api_user()
        if not set_as_profile_photo:
            # To post without making it your profile photo requires reg.
            reg_ok(user)

        if enter_in_tournament and not user.location:
            raise InvalidAPIUsage('Cannot post photo with empty user location')
        if enter_in_tournament:
            if user.show_gender_male is None:
                msg = 'Cannot enter tournament with empty user gender'
                raise InvalidAPIUsage(msg)
            location, geo = get_location_and_geo()
        else:
            location, geo = None, None

        photo = create_photo(user, user.get_location(), geo,
                             set_as_profile_photo, enter_in_tournament,
                             tags=tags,
                             is_test=is_test,
                             media_type=media_type)
        file_name = photo.file_name

        result = {
            'id': photo.uuid.hex,
            'share_url': photo.get_share_url(),
            'post_form_args': get_s3_connection().build_post_form_args(
                                    settings.S3_INCOMING_BUCKET_NAME,
                                    file_name,
                                    http_method='https',
                                    max_content_length=20000000) # 20MB
        }

        # {'action': 'http://ocean-inbox.s3.amazonaws.com/',
        #  'fields': [
        #    {'name': 'x-amz-storage-class', 'value': 'STANDARD'},
        #    {'name': 'policy',
        #     'value': 'aoeutshaoesuthaosetuhasoetuhasoteuhsatoheu='},
        #    {'name': 'AWSAccessKeyId', 'value': 'aoesuthasoeuthasoetuh'},
        #    {'name': 'signature', 'value': u'aoesuthasoeuthasoetuh='},
        #    {'name': 'key', 'value': 'test1.jpg'}]}
        return result, 202
        # return get_s3_connection().build_post_form_args(
        #     settings.S3_INCOMING_BUCKET_NAME,
        #     file_name,
        #     http_method='https')

@ns_photo.route('/photos/<string:photo_id>/comments')
@api.doc(params={'photo_id': 'uuid (hex) of photo on which to comment'})
class PhotoCommentList(Resource):
    @timingIncrDecorator('POST /photos/<photo_id>/comments')
    #@marshal_with(comment_model)
    def post(self, photo_id):
        """Auth User posts a new Comment for this Photo.

        The Post body is the text of the comment. It is not json-encoded.

        Auth header and registration status 'ok' required.

        return HTTP status code 201 on success

        """
        user = current_api_user()
        reg_ok(user)
        photo_uuid = uuid_schema(photo_id)
        photo = get_photo(photo_uuid)
        if photo is None:
            abort(404)
        location, geo = get_location_and_geo()
        posted_at = now()
        text = comment_schema(request.data)
        comment = model.PhotoComment(photo_uuid,
                                     posted_at=posted_at,
                                     user_uuid=user.uuid,
                                     uuid = uuid.uuid1(),
                                     text=text,
                                     lat=geo.lat,
                                     lon=geo.lon,
                                     geodata=geo.meta,
                                     location=location.uuid)
        comment.save()
        # Feed and notify, but not for a self-comment.
        if photo.user_uuid != user.uuid:
            feed_new_comment(photo.user_uuid, comment.user_uuid, photo.uuid,
                             comment.uuid)
            sns.push_new_comment(photo.user_uuid, comment.user_uuid, photo.uuid,
                                 comment.uuid)
        return render_comment(comment, user), 201


    @timingIncrDecorator('GET /photos/<photo_id>/comments')
    @marshal_with(comment_list_model)
    def get(self, photo_id):
        """Get the list of comments for this photo."""
        photo_uuid = uuid_schema(photo_id)
        user = current_api_user()
        from util import epoch
        query = model.PhotoComment.query(photo_uuid, posted_at__gt=epoch,
                                         consistent_read=False)
        comments = list(islice(query, 200))
        return {'comments': [render_comment(c) for c in comments]}

@ns_photo.route('/photos/<string:photo_id>/awards')
@api.doc(params={'photo_id': 'uuid (hex) of photo to get awards for'})
class AwardList(Resource):
    @timingIncrDecorator('GET /photos/<photo_id>/awards')
    # TODO: This barfs bc its a list? See if can fix.
#    @marshal_with(award_list_model)
    def get(self, photo_id):
        """Get the list of awards for this photo.

        Auth header required.

        """
        photo_uuid = uuid_schema(photo_id)
        user = current_api_user()
        photo = get_photo(photo_uuid)
        if photo.user_uuid != user.uuid:
            msg = "Cant get awards for other user's photos"
            log.debug(msg)
            raise InvalidAPIUsage(msg)
        awards = photo.get_awards()
        return [render_award(a) for a in awards]

@ns_photo.route('/photos/<string:photo_id>')
@api.doc(params={'photo_id': 'uuid (hex) of photo to get'})
class PhotoSingle(Resource):
    @timingIncrDecorator('GET /photos/*')
    def get(self, photo_id):
        """Get the share page for this photo.

        Auth header required.

        """
        photo_uuid = uuid_schema(photo_id)
        user = current_api_user()
        photo = get_photo(photo_uuid)
        if photo is None:
            msg = 'Could not find photo with uuid %s' % photo_uuid.hex
            response = jsonify({'message': msg})
            response.status_code = 404
            return response
        return render_photo(photo)

@ns_photo.route('/photo_share/<string:photo_id>')
@api.doc(params={'photo_id': 'uuid (hex) of photo to get share page for'})
class AwardList(Resource):
    @timingIncrDecorator('GET /photo_share')
    def get(self, photo_id):
        """Get the share page for this photo.

        Auth header required.

        """
        photo_uuid = uuid_schema(photo_id)
        user = current_api_user()
        photo = get_photo(photo_uuid)
        return render_photo(photo)


# -- Tags ---------------------------------------------------------------------

ns_tag = api.namespace('tag', description='Tag operations', path='/')


@ns_tag.route('/tags/<string:gender>')
class TagList(Resource):
    @timingIncrDecorator('GET /tags/<gender>')
    @api.doc(description="""Get a curated list of male tags with cover photo

    Auth header required.

    gender must be 'm' or 'f'

    """)
    def get(self, gender):
        user = current_api_user()
        if gender not in ['m', 'f']:
            abort(404)
        if gender == 'm':
            top_tags = tags.TOP_TAGS_MALE
        else:
            top_tags = tags.TOP_TAGS_FEMALE
        result = []
        for tag in top_tags:
            gender_tag = '{}_{}'.format(gender, tag)
            cover_photo = tags.get_cover_photo(gender_tag)
            result.append(render_tag(gender, tag, cover_photo))
        return result


tags_parser = reqparse.RequestParser()
tags_parser.add_argument('exclusive_start_key',
                         type=str,
                         help='uuid (hex) of photo record to start after',
                         required=False,
                         location='values',
                         default='')
tags_parser.add_argument('count',
                         type=int,
                         help='number of results to return',
                         required=False,
                         location='values',
                         default=25)
@ns_tag.route('/tags/<string:gender>/<string:tag>')
class TagPhotos(Resource):
    @timingIncrDecorator('GET /tags/<gender>/<tag>')
    @api.doc(
        description="""Get paginated list of photos ordered by score

    Auth header required.

    gender must be 'm' or 'f'

    paging supported, ex: /tags/f/#selfie?exclusive_start_key=c86371aef0e511e59004c8e0eb16059b&count=30
    default paging params are
    exclusive_start_key=''
    count=25
    if exclusive_start_key is blank results start from beginning.
""")
    def get(self, gender, tag):
        user = current_api_user()
        if gender not in ['m', 'f']:
            abort(404)
        gender_tag = '{}_{}'.format(gender, tag.lower())

        args = tags_parser.parse_args()

        exclusive_start_key = args['exclusive_start_key']
        count = args['count']

        def exclusive_start_key_check(i):
            if exclusive_start_key == '':
                while True:
                    yield i.next()
            else:
                while True:
                    item = i.next()
                    if exclusive_start_key == item.uuid.hex:
                        for item in i:
                            yield item

        # Use an iterator to filter out out-of-date entries.
        # TODO: is it possible to give an offset in a query?
        tags_by_score = model.PhotoGenderTag.score_index.query(
            gender_tag, scan_index_forward=False, consistent_read=False)

        tags_by_score = count_iter(exclusive_start_key_check(tags_by_score),
                                   count)
        photos = [get_photo(t.uuid) for t in tags_by_score]
        return [render_photo(p) for p in photos]

tag_trend_parser = reqparse.RequestParser()
tag_trend_parser.add_argument('exclusive_start_key',
                         type=unicode,
                         help='tag',
                         required=False,
                         location='values',
                         default='')
tag_trend_parser.add_argument('count',
                         type=int,
                         help='number of results to return',
                         required=False,
                         location='values',
                         default=25)
@ns_tag.route('/tags/trending/<string:gender>')
class TrendingTags(Resource):
    @timingIncrDecorator('GET /tags/trending/<gender>')
    @api.doc(
        description="""Get paginated list of trending tags ordered by popularity.

    Auth header required.

    gender must be 'm' or 'f'

    paging supported, ex: /tags/trending/f?exclusive_start_key=#foobar&count=30
    default paging params are
    exclusive_start_key=''
    count=25
    if exclusive_start_key is blank results start from beginning.
""")
    def get(self, gender):
        user = current_api_user()
        is_test = test_parser.parse_args().get('is_test', '') == 'True'
        if gender not in ['m', 'f']:
            abort(404)
        args = tag_trend_parser.parse_args()
        exclusive_start_key = args['exclusive_start_key'].lower()
        count = args['count']

        def exclusive_start_key_check(i):
            if exclusive_start_key == '':
                while True:
                    yield i.next()
            else:
                while True:
                    item = i.next()
                    if exclusive_start_key == item.tag_with_case.lower():
                        for item in i:
                            yield item

        def test_check(i):
            for item in i:
                if not t.tag_with_case.endswith(TEST_TAG_POSTFIX) or is_test:
                    yield item

        # TODO: is it possible to give an offset in a query?
        tags_by_score = model.GenderTagTrend.query(
            gender, scan_index_forward=False, consistent_read=False)
        tags_by_score = count_iter(exclusive_start_key_check(tags_by_score),
                                   count)
        return [render_tag_count(gender,
                                 t.tag_with_case,
                                 t.rank) for t in tags_by_score]

@ns_tag.route('/tags/<string:tag>/<string:photo_uuid>')
class TagPhotoCreate(Resource):
    @timingIncrDecorator('PUT /tags/<tag>/<photo>')
    @api.doc(
        description="""Add a tag to an existing photo.

    Auth header required.
""")
    def put(self, tag, photo_uuid):
        photo_uuid = uuid_schema(photo_uuid)
        user = current_api_user()
        photo = get_photo(photo_uuid)
        if photo is None:
            msg = 'Could not find photo with uuid %s' % photo_uuid.hex
            response = jsonify({'message': msg})
            response.status_code = 404
            return response
        if photo.user_uuid != user.uuid:
            msg = 'Can only tag your own photos'
            response = jsonify({'message': msg})
            response.status_code = 401
            return response
        log.info('PUT /tags/<tag>/<photo> got tag {}'.format(tag))
        tags.add_tag(user, photo, tag)
        return None, 204

@ns_tag.route('/tags/search/<string:gender>/<string:tag>')
class TagSearch(Resource):
    @timingIncrDecorator('GET /tags/search/<gender>/<tag>')
    @api.doc(description="""Get a list of matching tags matching with photo

    Auth header required.

    gender must be 'm' or 'f'

    """)
    def get(self, gender, tag):
        user = current_api_user()
        is_test = test_parser.parse_args().get('is_test', '') == 'True'
        if gender not in ['m', 'f']:
            abort(404)

        search_results = search.search(gender, tag)
        result = []
        for search_result in search_results:
            gender_tag = '{}_{}'.format(gender, search_result.lower())
            photo_gender_tags = list(model.PhotoGenderTag.query(gender_tag,
                                                                limit=2))
            if photo_gender_tags:
                photo_gender_tag = photo_gender_tags[0].tag_with_case
                if not photo_gender_tag.endswith(TEST_TAG_POSTFIX) or is_test:
                    render = render_tag(gender,
                                        photo_gender_tag,
                                        tags.get_cover_photo(photo_gender_tag))
                    result.append(render)
        return result


# -- Matches ------------------------------------------------------------------

ns_match = api.namespace('match', description='Match operations', path='/')

matches_parser = reqparse.RequestParser()
matches_parser.add_argument('reset_tournament_status',
                            type=str,
                            help='True or False, default False',
                            required=False,
                            location='values',
                            default='False')
# Rest notes:
# GET /users/me/matches
# REST implies it is idempotent, so we'd need to include the index, like so
# GET /users/me/matches?index=12345,limit=20
# What we will do is to use a default if index is not provided as this means
# there is less state for the client to manage.
# So, apologies to REST, this means the URL will return different results each
# time it is called, and is not idempotent.
# A most rest friendly option to achieve a similar low-client-state result
# would be that when the server exposes the interface it includes the index
# that is next.
# We do this "implicit default" method because it is easiest for us and this
# is a private client-server relationship.
@ns_match.route('/users/me/matches')
class MatchList(Resource):
    @timingIncrDecorator('GET /users/me/matches')
    #@marshal_with(match_list_model)
    @api.doc(model=match_list_model,
             params={'reset_tournament_status': 'if True tournament status is set to initial values, default False'},
             description="""the returned list can be either a Match or a Tournament, tournaments are like:
    't': string, 'tournament kind: local, regional, global'
    'uuid': string, 'uuid (hex) of tournament',
    'one': photo, (same as in Match),
    'two': photo, (same as in Match),
    'three': photo, (same as in Match),
    'four': photo, (same as in Match),
    'five': photo, (same as in Match),
    'six': photo, (same as in Match),
    'seven': photo, (same as in Match),
    'eight': photo, (same as in Match),
    'nine': photo, (same as in Match),
    'ten': photo, (same as in Match),
    'eleven': photo, (same as in Match),
    'twelve': photo, (same as in Match),
    'thirteen': photo, (same as in Match),
    'fourteen': photo, (same as in Match),
    'fifteen': photo, (same as in Match),
    'sixteen': photo, (same as in Match)
    also autodocs are wrong, matches are not optional

    Matches are generated from the user's categories. if post data of the
    form {'categories': ['Wildlife', 'Nature']} is given those categories
    will be used instead.

    Auth header required
})""")
    def get(self):
        """Get a list of Matches for the auth user to judge. Not idempotent."""
        user = current_api_user()
        g_l = user.get_view_gender_location()

        if 'application/json' != request.headers.get('Content-Type'):
            abort(415)

        is_test = test_parser.parse_args().get('is_test', '') == 'True'

        args = matches_parser.parse_args()
        if args['reset_tournament_status'] == 'True':
            log.info("resetting tournament status")
            init_tournament_status(user)

        # Get (up to) next 200 photos so we can select from that 200 at random.
        load_kwargs = {
            'scan_index_forward': False,
            'copy_complete__eq': True,
            'consistent_read': False
        }
        if not is_test:
            load_kwargs['is_test__ne'] = True

        photos = set(
            islice(model.Photo.post_date_index.query(
                    g_l, **load_kwargs),
                   200))
        bad_photos = [p for p in photos if not p.copy_complete]
        if bad_photos:
            log.debug("copy_complete__eq not working, photos with copy_complete!=True in match-query results")
        photos = [p for p in photos if p.copy_complete]
        if len(photos) < 2:
            log.info("not enough photos to make matches in %s", g_l)
            response = jsonify(
                {'message': 'Not enough photos in %s' % g_l})
            response.status_code = 404
            return response

        # Special Case - If the user posts and views the same gender, then
        # we get their just-posted picture into their next Match request.
        match_bump_photo = None
        # This is a hack requested by @mcgraw, that everybody posts as female.
#        if user.view_gender_male == user.show_gender_male:
        if True:
            # Get this user's most recent photo.
            user_photos = model.Photo.user_index.query(
                    user.uuid,
                    limit=4,
                    scan_index_forward=False,
                    copy_complete__eq=True)

            user_photos = list(user_photos)
            if any(p for p in user_photos if not p.copy_complete):
                log.debug("copy_complete__eq not working, photos with copy_complete!=True in leaderboards-query results")
            user_photos = [p for p in user_photos if p.copy_complete]
            if user_photos:
                photo = user_photos[0]
                # If we have not already bumped this photo.
                if not photo.match_bumped:
                    if photo.copy_complete:
                        match_bump_photo = photo

        # Do not mix movie and photo in same match.
        pic_photos = []
        movie_photos = []
        for p in photos:
            if p.media_type == 'movie':
                movie_photos.append(p)
            else:
                pic_photos.append(p)

        first_pair = []
        if match_bump_photo:
            pop_index = None
            for i, photo in enumerate(photos):
                if match_bump_photo.uuid == photo.uuid:
                    pop_index = i
                    break
            if pop_index is not None:
                photos.pop(pop_index)
            if match_bump_photo.media_type == 'movie':
                rando_index = randrange(len(movie_photos))
                rando = movie_photos[rando_index]
                movie_photos.pop(rando_index)
            else:
                rando_index = randrange(len(pic_photos))
                rando = pic_photos[rando_index]
                pic_photos.pop(rando_index)
            first_pair.append((match_bump_photo, rando))
            match_bump_photo.match_bumped = True
            match_bump_photo.save()
            # Remove the randomly selected photo.
            for i, photo in enumerate(photos):
                if rando.uuid == photo.uuid:
                    pop_index = i
                    break
            photos.pop(pop_index)

        # We need to balance the incidence of movie and pic, to do so, we'll
        # track incidence in photos.

        def switched_iter(photos, movie_photos, pic_photos):
            movie_iter = random_by_twos(movie_photos)
            pic_iter = random_by_twos(pic_photos)
            for photo in photos:
                if photo.media_type == 'movie':
                    yield movie_iter.next()
                else:
                    yield pic_iter.next()

        matches = chain(first_pair,
                        switched_iter(photos, movie_photos, pic_photos))
        result = []
        remaining = 10

        while remaining != 0:
            if tournament_is_next(user):
                tournament = get_next_tournament(user)
                # TODO: Broken - g_l arg won't work for cross regional tournament
                result.append(render_tournament(g_l, tournament))
                break  # Tournament loading is slow, so stop here.
            else:
                try:
                    match = matches.next()
                except StopIteration:
                    break

                photo_a = match[0]
                photo_b = match[1]
                # Do not allow a user to compete against themselves.
                if photo_a.user_uuid == photo_b.user_uuid:
                    continue
                # Very important!  Match.photo_a.uuid < Match.photo_b.uuid
                if photo_a.uuid.hex > photo_b.uuid.hex:
                    photo_a, photo_b = photo_b, photo_a

                # See if user has already made a match before suggesting it.
                try:
                    db_match = model.Match.get((photo_a.uuid, photo_b.uuid),
                                               user.uuid)
                except DoesNotExist:
                    pass
                else:
                    continue
                # Increment the tournament counter for this user.
                tournament_status_log_match(user)
                match = create_match(photo_a, photo_b, user)
                result.append(render_match(photo_a, match.a_win_delta,
                                           match.a_lose_delta, photo_b,
                                           match.b_win_delta,
                                           match.b_lose_delta))
                remaining -= 1
        if len(result) == 0:
            msg = "not enough unique matches in %s for this user" % g_l
            log.info(msg)
            response = jsonify({'message': msg})
            response.status_code = 404
            return response

        return {
            'matches': result
        }

@ns_match.route('/users/me/tag_matches/<string:tag>')
class TagMatchList(Resource):
    @timingIncrDecorator('GET /users/me/tag_matches/<tag>')
    #@marshal_with(match_list_model)
    @api.doc(model=match_list_model,
             params={'reset_tournament_status': 'if True tournament status is set to initial values, default False'},
             description="""Get Matches from photos with the given tag.

    Auth header required
})""")
    def get(self, tag):
        """Get Matches from Photos with the given tag for the auth user to judge. Not idempotent."""
        user = current_api_user()
        is_test = test_parser.parse_args().get('is_test', '') == 'True'
        gender = 'f' # TODO use real view gender this is a hack
        gender_tag = '{}_{}'.format(gender, tag.lower())

        if 'application/json' != request.headers.get('Content-Type'):
            abort(415)

        args = matches_parser.parse_args()

        if args['reset_tournament_status'] == 'True':
            log.info("resetting tournament status")
            init_tournament_status(user)

        # Get (up to) next 200 photos so we can select from that 200 at random.
        photo_gender_tags = set(
            islice(model.PhotoGenderTag.query(
                    gender_tag,
                    consistent_read=False),
                   200))

        photos = []
        for photo_gender_tag in photo_gender_tags:
            photo = get_photo(photo_gender_tag.uuid)
            if photo:
                if not photo.is_test or is_test:
                    photos.append(photo)

        if len(photos) < 2:
            log.info("not enough photos to make matches in %s", gender_tag)
            response = jsonify(
                {'message': 'Not enough photos in {} {}'.format(gender, tag)})
            response.status_code = 404
            return response

        # Special Case - If the user posts and views the same gender, then
        # we get their just-posted picture into their next Match request.
        match_bump_photo = None
        # TODO: put back
        # This is a hack requested by @mcgraw, that everybody posts as female.
#        if user.view_gender_male == user.show_gender_male:
        if True:
            # Get this user's most recent photo.
            user_photos = model.Photo.user_index.query(
                    user.uuid,
                    limit=4,
                    scan_index_forward=False,
                    copy_complete__eq=True)

            user_photos = list(user_photos)
            if any(p for p in user_photos if not p.copy_complete):
                log.debug("copy_complete__eq not working, photos with copy_complete!=True in leaderboards-query results")
            user_photos = [p for p in user_photos if p.copy_complete and tag in p.get_tags()]
            if user_photos:
                photo = user_photos[0]
                # If we have not already bumped this photo.
                if not photo.match_bumped:
                    if photo.copy_complete:
                        match_bump_photo = photo

        first_pair = []
        if match_bump_photo:
            pop_index = None
            for i, photo in enumerate(photos):
                if match_bump_photo.uuid == photo.uuid:
                    pop_index = i
                    break
            if pop_index is not None:
                photos.pop(pop_index)
            rando_index = randrange(len(photos))
            rando = photos[rando_index]
            photos.pop(rando_index)
            first_pair.append((match_bump_photo, rando))
            match_bump_photo.match_bumped = True
            match_bump_photo.save()

        matches = chain(first_pair, random_by_twos(photos))
        result = []
        remaining = 10

        while remaining != 0:
            if False: #tournament_is_next(user):   # -- no tag tournaments yet.
                pass
                # tournament = get_next_tournament(user)
                # # TODO: Broken - g_l arg won't work for cross regional tournament
                # result.append(render_tournament(g_l, tournament))
                # break  # Tournament loading is slow, so stop here.
            else:
                try:
                    match = matches.next()
                except StopIteration:
                    break

                photo_a = match[0]
                photo_b = match[1]
                # Do not allow a user to compete against themselves.
                if photo_a.user_uuid == photo_b.user_uuid:
                    continue
                # Very important!  Match.photo_a.uuid < Match.photo_b.uuid
                if photo_a.uuid.hex > photo_b.uuid.hex:
                    photo_a, photo_b = photo_b, photo_a

                # TODO: Double-check, I think we already test this in the
                # iterator.
                # See if user has already made a match before suggesting it.
                try:
                    db_match = model.Match.get((photo_a.uuid, photo_b.uuid),
                                               user.uuid)
                except DoesNotExist:
                    pass
                else:
                    continue
                # Increment the tournament counter for this user.
                tournament_status_log_match(user)
                match = create_match(photo_a, photo_b, user)
                result.append(render_match(photo_a, match.a_win_delta,
                                           match.a_lose_delta, photo_b,
                                           match.b_win_delta,
                                           match.b_lose_delta))
                remaining -= 1
        if len(result) == 0:
            msg = "not enough unique matches in %s for this user" % tag
            log.info(msg)
            response = jsonify({'message': msg})
            response.status_code = 404
            return response

        return {
            'matches': result
        }

@ns_match.route('/users/me/matches/<string:match_id>')
class Match(Resource):
    @timingIncrDecorator('PUT /users/me/matches/*')
    @api.doc(params={'match_id': 'uuid (hex) of Match being voted on'},
             description="Payload is winner of Match, 'a' or 'b'")
    @api.expect(fields.String(description="Winner of Match",
                              required=True,
                              enum=['a', 'b']))
    def put(self, match_id):
        """Auth user votes on a Match.  Input is either 'a' or 'b'.

        Auth header required.

        """
        match_id = match_id_schema(match_id)
        try:
            photo_a_uuid = uuid.UUID(match_id[:32])
        except ValueError:
            raise InvalidAPIUsage('Illegal photo_a uuid')
        try:
            photo_b_uuid = uuid.UUID(match_id[32:])
        except ValueError:
            raise InvalidAPIUsage('Illegal photo_b uuid')
        user = current_api_user()
        try:
            match = model.Match.get((photo_a_uuid, photo_b_uuid), user.uuid,
                                    consistent_read=False)
        except DoesNotExist:
            abort(404)
        if match.judged:
            raise InvalidAPIUsage('Match already judged')
        winner = vote_schema(request.data)
        if 'a' == winner:
            match.a_won = True
            win_photo_uuid = photo_a_uuid
            lose_photo_uuid = photo_b_uuid
        else:
            match.a_won = False
            win_photo_uuid = photo_b_uuid
            lose_photo_uuid = photo_a_uuid
        log_match_for_scoring(match)
        match.judged = True
        match.judged_date = now()
        match.scored_date = now()
        match.save()
        winner_uuid = get_photo(win_photo_uuid).user_uuid
        win = model.Win(winner_uuid,
                        created_on=now(),
                        win_photo=win_photo_uuid,
                        lose_photo=lose_photo_uuid,
                        uuid=uuid.uuid1())
        win.save()

    @timingIncrDecorator('GET /users/me/matches/*')
    @api.doc(params={'match_id': 'uuid (hex) of Photo to get a match for'})
    def get(self, match_id):
        """Request a match to go with the given photo id.

        returns 404 if no match candidate can be found, or if photo
        can't be found.

        Auth header required.

        """
        user = current_api_user()
        is_test = test_parser.parse_args().get('is_test', '') == 'True'

        if 'application/json' != request.headers.get('Content-Type'):
            abort(415)

        # requesting match with this photo.
        photo_uuid = uuid_schema(match_id)
        photo = get_photo(photo_uuid)
        if photo is None:
            msg = 'Could not find photo with uuid %s' % photo_uuid.hex
            response = jsonify({'message': msg})
            response.status_code = 404
            return response

        load_kwargs = {
            'scan_index_forward': False,
            'copy_complete__eq': True,
            'consistent_read': False
        }
        if not is_test:
            load_kwargs['is_test__ne'] = True
        photos = set(islice(
                model.Photo.post_date_index.query(
                    photo.gender_location, **load_kwargs),
                200))

        found_photo = False
        for p in photos:
            # No same-on-same matches.
            if p.uuid == photo_uuid:
                continue
            photo_a = photo
            photo_b = p
            # Do not allow a user to compete against themselves.
            if photo_a.user_uuid == photo_b.user_uuid:
                continue
            # Very important!  Match.photo_a.uuid < Match.photo_b.uuid
            if photo_a.uuid.hex > photo_b.uuid.hex:
                photo_a, photo_b = photo_b, photo_a

            # See if user has already made a match before
            # suggesting it.
            try:
                db_match = model.Match.get(
                    (photo_a.uuid, photo_b.uuid),
                    user.uuid, consistent_read=False)
            except DoesNotExist:
                found_photo = True
                break
            else:
                continue

        if not found_photo:
            # Retry, without dupe-check.
            photos = list(model.Photo.post_date_index.query(
                        photo.gender_location,
                        scan_index_forward=False,
                        limit=11,
                        copy_complete__eq=True,
                        consistent_read=False))
            shuffle(photos)
            for p in photos:
                if p.uuid == photo_uuid:
                    continue
                photo_a = photo
                photo_b = p
                # Very important!  Match.photo_a.uuid < Match.photo_b.uuid
                if photo_a.uuid.hex > photo_b.uuid.hex:
                    photo_a, photo_b = photo_b, photo_a
                found_photo = True

        if not found_photo:
            msg = 'Could not find photo to match with %s' % photo_uuid.hex
            response = jsonify({'message': msg})
            response.status_code = 404
            return response

        match = create_match(photo_a, photo_b, user)
        return render_match(photo_a, match.a_win_delta,
                            match.a_lose_delta, photo_b,
                            match.b_win_delta, match.b_lose_delta)

@ns_match.route('users/me/tournaments/<string:tournament_id>')
class Tournament(Resource):
    @timingIncrDecorator('PUT /users/me/tournaments/*')
    @api.expect(tournament_input_model)
    def put(self, tournament_id):
        """Auth user votes on a Tournament.  Input is dict of bracket.

        Auth header required.

        """
        tournament_uuid = uuid_schema(tournament_id)
        user = current_api_user()
        try:
            tournament = model.Tournament.get(user.uuid, tournament_uuid)
        except DoesNotExist:
            abort(404)
        if tournament.judged:
            raise InvalidAPIUsage('Match already judged')
        input = tournament_schema(request.json)
        tournament.judged = True
        tournament.judged_date = now()
        tournament.one_vs_two = input['one_vs_two']
        tournament.three_vs_four = input['three_vs_four']
        tournament.five_vs_six = input['five_vs_six']
        tournament.seven_vs_eight = input['seven_vs_eight']
        tournament.nine_vs_ten = input['nine_vs_ten']
        tournament.eleven_vs_twelve = input['eleven_vs_twelve']
        tournament.thirteen_vs_fourteen = input['thirteen_vs_fourteen']
        tournament.fifteen_vs_sixteen = input['fifteen_vs_sixteen']
        tournament.one_two_vs_three_four = input['one_two_vs_three_four']
        tournament.five_six_vs_seven_eight = input['five_six_vs_seven_eight']
        tournament.nine_ten_vs_eleven_twelve = input['nine_ten_vs_eleven_twelve']
        tournament.thirteen_fourteen_vs_fifteen_sixteen = input['thirteen_fourteen_vs_fifteen_sixteen']
        tournament.one_four_vs_five_eight = input['one_four_vs_five_eight']
        tournament.nine_twelve_vs_thirteen_sixteen = input['nine_twelve_vs_thirteen_sixteen']
        tournament.winner = input['winner']
        tournament.save()
        winning_photo = get_photo(tournament.winner)
        feed_tournament_win(winning_photo.user_uuid, tournament.winner)

# Hand-curated list of Leaderboards
top_leaderboards_male = [
    ('Los Angeles, CA', '67f22847ecf311e4a264c8e0eb16059b'),
    ('San Diego, CA', '67f22d14ecf311e48bcbc8e0eb16059b'),
    ('San Francisco, CA', '67f22e57ecf311e488c0c8e0eb16059b'),
    ('Denver, CO', '67f2357aecf311e4ac58c8e0eb16059b'),
    ('Washington, DC', '67f2366becf311e49745c8e0eb16059b'),
    ('Miami, FL', '67f238cfecf311e4b850c8e0eb16059b'),
    ('Atlanta, GA', '67f23b26ecf311e49098c8e0eb16059b'),
    ('Honolulu, HI', '67f23c1eecf311e4adf5c8e0eb16059b'),
    ('Chicago, IL', '67f23d4cecf311e482fbc8e0eb16059b'),
    ('New Orleans, LA', '67f24330ecf311e4bfc2c8e0eb16059b'),
    ('Boston, MA', '67f24557ecf311e48f1ac8e0eb16059b'),
    ('Detroit, MI', '67f2468cecf311e48b01c8e0eb16059b'),
    ('Las Vegas, NV', '67f24d28ecf311e482e2c8e0eb16059b'),
    ('New York, NY', '67f2524fecf311e496c6c8e0eb16059b'),
    ('Portland, OR', '67f25cbdecf311e4949ec8e0eb16059b'),
    ('Philadelphia, PA', '67f25de8ecf311e4a96bc8e0eb16059b'),
    ('Memphis, TN', '67f2613decf311e4b1a2c8e0eb16059b'),
    ('Austin, TX', '67f264caecf311e4b4ebc8e0eb16059b'),
    ('Seattle, WA', '67f26f23ecf311e493c3c8e0eb16059b')
]
top_leaderboards_female = [
    ('Los Angeles, CA', '67f22847ecf311e4a264c8e0eb16059b'),
    ('San Diego, CA', '67f22d14ecf311e48bcbc8e0eb16059b'),
    ('San Francisco, CA', '67f22e57ecf311e488c0c8e0eb16059b'),
    ('Denver, CO', '67f2357aecf311e4ac58c8e0eb16059b'),
    ('Washington, DC', '67f2366becf311e49745c8e0eb16059b'),
    ('Miami, FL', '67f238cfecf311e4b850c8e0eb16059b'),
    ('Atlanta, GA', '67f23b26ecf311e49098c8e0eb16059b'),
    ('Honolulu, HI', '67f23c1eecf311e4adf5c8e0eb16059b'),
    ('Chicago, IL', '67f23d4cecf311e482fbc8e0eb16059b'),
    ('New Orleans, LA', '67f24330ecf311e4bfc2c8e0eb16059b'),
    ('Boston, MA', '67f24557ecf311e48f1ac8e0eb16059b'),
    ('Detroit, MI', '67f2468cecf311e48b01c8e0eb16059b'),
    ('Las Vegas, NV', '67f24d28ecf311e482e2c8e0eb16059b'),
    ('New York, NY', '67f2524fecf311e496c6c8e0eb16059b'),
    ('Portland, OR', '67f25cbdecf311e4949ec8e0eb16059b'),
    ('Philadelphia, PA', '67f25de8ecf311e4a96bc8e0eb16059b'),
    ('Memphis, TN', '67f2613decf311e4b1a2c8e0eb16059b'),
    ('Austin, TX', '67f264caecf311e4b4ebc8e0eb16059b'),
    ('Seattle, WA', '67f26f23ecf311e493c3c8e0eb16059b')
]

@ns_match.route('/leaderboards/m')
class LeaderBoardsMale(Resource):
    @timingIncrDecorator('GET /leaderboards/m')
#    @marshal_with(leaderboard_list_model)
    @api.doc(description="autodocs are wrong, 'photos' is not optional")
    def get(self):
        """Get the name and location id of all leaderboards."""
        result = []
        for name, location_uuid_hex in top_leaderboards_male:
            gender_location = 'm%s' % (location_uuid_hex)

            top = list(model.Photo.score_index.query(gender_location,
                                                     limit=50,
                                                     scan_index_forward=False,
                                                     consistent_read=False,
                                                     copy_complete__eq=True))
            if any(p for p in top if not p.copy_complete):
                log.debug("copy_complete__eq not working, photos with copy_complete!=True in leaderboards/m-query results")
            top = [p for p in top if p.copy_complete]
            result.append({
                'name': name,
                'gender_location': gender_location,
                'photos': [render_photo(p) for p in top]
            })
        return {'leaderboards': result}

@ns_match.route('/leaderboards/f')
class LeaderBoardsFemale(Resource):
    @timingIncrDecorator('GET /leaderboards/f')
#    @marshal_with(leaderboard_list_model)
    @api.doc(description="autodocs are wrong, 'photos' is not optional")
    def get(self):
        """Get the name and location id of all show_female top leaderboards."""
        result = []
        is_test = test_parser.parse_args().get('is_test', '') == 'True'
        for name, location_uuid_hex in top_leaderboards_female:
            gender_location = 'f%s' % (location_uuid_hex)
            load_kwargs = {
                'copy_complete__eq': True,
                'limit': 50,
                'scan_index_forward': False,
                'consistent_read': False
            }
            if not is_test:
                load_kwargs['is_test__ne'] = True
            top = list(model.Photo.score_index.query(gender_location,
                                                     **load_kwargs))
            if any(p for p in top if not p.copy_complete):
                log.debug("copy_complete__eq not working, photos with copy_complete!=True in leaderboards/f-query results")
            top = [p for p in top if p.copy_complete]
            result.append({
                'name': name,
                'gender_location': gender_location,
                'photos': [render_photo(p) for p in top]
            })
        return {'leaderboards': result}

leaderboard_parser = reqparse.RequestParser()
leaderboard_parser.add_argument('when',
                                type=str,
                                help="'alltime', 'thishour', 'today', 'thisweek', 'thismonth', 'thisyear'",
                                required=False,
                                location='values',
                                default='thisweek')
leaderboard_parser.add_argument('exclusive_start_key',
                                type=str,
                                help='uuid (hex) of photo to start results after',
                                required=False,
                                location='values',
                                default='')
leaderboard_parser.add_argument('count',
                                type=int,
                                help='number of results to return',
                                required=False,
                                location='values',
                                default=50)
@ns_match.route('/leaderboards/<string:gender_location>')
@api.doc(params={'gender_location': 'Gender location identifying leaderboard'})
class LeaderBoard(Resource):
    @timingIncrDecorator('GET /leaderboards/*')
    #@marshal_with(leaderboard_model)
    @api.doc(description="""autodocs are wrong, 'photos' is not optional
    filter leaderboards like so
    /leaderboards/?when=today?exclusive_start_key=b073eec2f10b11e5b312c8e0eb16059b?count=25

default params are
when=thisweek
exclusive_start_key=''
count=50

when can be alltime, thishour, today, thisweek, thismonth, thisyear""")
    def get(self, gender_location):
        """Get the Leaderboard for this gender and location, also 'all' for alltogether."""
        is_test = test_parser.parse_args().get('is_test', '') == 'True'
        args = leaderboard_parser.parse_args()
        kind = args['when']
        if kind not in ['alltime', 'thishour', 'today', 'thisweek',
                        'thismonth', 'thisyear']:
            raise InvalidAPIUsage("unrecognized filter '{filter}'".format(
                    filter=kind
            ))

        exclusive_start_key = args['exclusive_start_key']
        count = args['count']

        def exclusive_start_key_check(i):
            if exclusive_start_key == '':
                while True:
                    yield i.next()
            else:
                while True:
                    item = i.next()
                    if exclusive_start_key == item.uuid.hex:
                        for item in i:
                            yield item

        model_class = {
            'alltime': model.Photo,
            'thishour': model.HourLeaderboard,
            'today': model.TodayLeaderboard,
            'thisweek': model.WeekLeaderboard,
            'thismonth': model.MonthLeaderboard,
            'thisyear': model.YearLeaderboard
        }[kind]

        if 'all' == gender_location:
            index_name = 'all_score_index'
        else:
            index_name = 'score_index'

        query = getattr(model_class, index_name).query

        # Note that Photo needs to check copy_complete, but the Leaderboard
        # copies don't because they don't get created until after the copy
        # is complete. The Leaderboard copies do not have a copy_complete
        # attribute.
        kwargs = {
            'scan_index_forward': False,
            'consistent_read': False
        }
        if 'alltime' == kind:
            kwargs['copy_complete__eq'] = True
            if not is_test:
                kwargs['is_test__ne'] = True

        top = query(gender_location, **kwargs)

        if 'alltime' == kind:
            top = query_filter_check(top, "GET /leaderboards")

        top = count_iter(exclusive_start_key_check(top), count)

        if kind != 'alltime':
            top = (x for x in (get_photo(p.uuid) for p in top) if x is not None)
            top = (x for x in top if not x.is_test or is_test)
        return {
            'photos': [render_photo(p) for p in top]
        }

wins_parser = reqparse.RequestParser()
wins_parser.add_argument('exclusive_start_key',
                         type=str,
                         help='uuid (hex) of win record to start after',
                         required=False,
                         location='values',
                         default='')
wins_parser.add_argument('count',
                         type=int,
                         help='number of results to return',
                         required=False,
                         location='values',
                         default=25)
@ns_match.route('/users/me/wins')
class WinList(Resource):
    @timingIncrDecorator('GET /users/me/wins')
    @api.doc(description="""Returns list of
    {
    'created_on': time of win,
    'win_photo': standard API photo rendering of winning photo,
    'lose_photo': standard API photo rendering of losing photo.
    'id': uuid (hex) of the win

    /users/me/wins?exclusive_start_key=b073eec2f10b11e5b312c8e0eb16059b?count=25

    default params are
    exclusive_start_key=''
    count=25

    }
    """)
    def get(self):
        """Get Win history for User."""
        user = current_api_user()

        args = wins_parser.parse_args()

        exclusive_start_key = args['exclusive_start_key']
        count = args['count']

        def exclusive_start_key_check(i):
            if exclusive_start_key == '':
                while True:
                    yield i.next()
            else:
                while True:
                    item = i.next()
                    if exclusive_start_key == item.uuid.hex:
                        for item in i:
                            yield item

        # Use an iterator to filter out out-of-date entries.
        # TODO: is it possible to give an offset in a query?
        # Note that Photo needs to check copy_complete, but the Leaderboard
        # copies don't because they don't get created until after the copy
        # is complete. The Leaderboard copies do not have a copy_complete
        # attribute.
        wins = model.Win.query(user.uuid, scan_index_forward=False,
                               consistent_read=False)

        wins = count_iter(exclusive_start_key_check(wins), count)

        return render_wins(wins)

# -- Following ----------------------------------------------------------------

ns_follow = api.namespace('following',
                          description='Following operations',
                          path='/')

@ns_follow.route('/users/me/following')
class Following(Resource):
    @timingIncrDecorator('GET /users/me/following')
    @api.doc(descritpion="returns list of photos, each representing who follows you, format is same as all photos in API")
    def get(self):
        """Get a list of the users you follow."""
        user = current_api_user()
        following = list(model.Following.query(user.uuid, limit=100,
                                               consistent_read=False))
        result = []
        for f in following:
            followed = get_user(f.followed)
            photo = followed.get_photo()
            result.append(render_user_info(followed, photo,
                                           request_authorized=user.registration=='ok'))
        return result

    @timingIncrDecorator('POST /users/me/following')
    @api.doc(description="""Post a {'followed': 'uuid(hex) of user to be followed'} in request body""")
    @api.expect(following_input_model)
    def post(self):
        """Auth user follows the given user.

        Auth header and registration status 'ok' required.

        """
        user = current_api_user()
        reg_ok(user)
        if 'application/json' != request.headers.get('Content-Type'):
            abort(415)
        following_data = following_schema(request.json)
        followed_uuid = following_data['followed']
        following = model.Following(
            user.uuid,
            followed=followed_uuid,
            created_on=now())
        following.save()
        follower = model.Follower(
            followed_uuid,
            follower=user.uuid,
            created_on=now())
        feed_activity(followed_uuid, 'NewFollower', user_uuid=user.uuid)
        sns.push_new_follower(followed_uuid=followed_uuid,
                              follower_uuid=user.uuid)
        follower.save()

    @timingIncrDecorator('DELETE /users/me/following')
    @api.doc(description="""Send a {'followed': 'uuid(hex) of user to be unfollowed'} in request body""")
    @api.expect(following_input_model)
    def delete(self):
        """Auth user unfollows the given user.

        Auth header and registration status 'ok' required.

        """
        user = current_api_user()
        reg_ok(user)
        if 'application/json' != request.headers.get('Content-Type'):
            abort(415)
        following_data = following_schema(request.json)
        followed_uuid = following_data['followed']

        try:
            following = model.Following.get(user.uuid, followed_uuid,
                                            consistent_read=False)
        except DoesNotExist:
            following = None
            found_following = False
        else:
            found_following = True
        if found_following:
            following.delete()

        try:
            follower = model.Follower.get(followed_uuid, user.uuid,
                                          consistent_read=False)
        except DoesNotExist:
            follower = None
            found_follower = False
        else:
            found_follower = True
        if found_follower:
            follower.delete()

        if found_following and found_follower:
            return None, 204
        else:
            msgs = []
            if not found_following:
                msgs.append('Could not find Following object')
            if not found_follower:
                msgs.append('Could not find Follower object')
            msg = ', '.join(msgs)
            response = jsonify({'message': msg})
            response.status_code = 404
            return response


@ns_follow.route('/users/me/followers')
class Followed(Resource):
    @timingIncrDecorator('GET /users/me/followers')
    @api.doc(descritpion="returns list of photos, each representing user you follow, format is same as all photos in API")
    def get(self):
        """Get a list of users that follow you."""
        user = current_api_user()
        followers = list(model.Follower.query(user.uuid, limit=100,
                                              consistent_read=False))
        result = []
        for f in followers:
            follower = get_user(f.follower)
            photo = follower.get_photo()
            result.append(render_user_info(follower, photo,
                                           request_authorized=user.registration=='ok'))
        return result

@ns_follow.route('/users/me/followers/<string:follower_uuid_hex>')
class FollowedIndividual(Resource):
    @timingIncrDecorator('GET /users/me/followers/<follower_uuid_hex>')
    @api.doc(descritpion="returns 204 if follower present, else 404.")
    def get(self, follower_uuid_hex):
        """returns 204 if follower present, else 404."""
        user = current_api_user()
        follower_uuid = uuid_schema(follower_uuid_hex)
        followers = list(model.Follower.query(user.uuid,
                                              follower__eq=follower_uuid,
                                              limit=2,
                                              consistent_read=False))
        if followers:
            return None, 204
        else:
            return None, 404

feed_parser = reqparse.RequestParser()
feed_parser.add_argument('exclusive_start_key',
                         type=str,
                         help='uuid (hex) of record to start after',
                         required=False,
                         location='values',
                         default='')
feed_parser.add_argument('count',
                         type=int,
                         help='number of results to return',
                         required=False,
                         location='values',
                         default=50)
@ns_follow.route('/users/me/feed')
class Feed(Resource):
    @timingIncrDecorator('GET /users/me/feed')
    @api.doc(description="""Return is a list photos posted by users you follow.

    List is in newest-to-oldest order. Photos are rendered in usual way.

    paging supported, ex: users/me/feed?exclusive_start_key=c86371aef0e511e59004c8e0eb16059b&count=30
    default paging params are
    exclusive_start_key=''
    count=50
    if exclusive_start_key is blank results start from beginning.

    """)
    def get(self):
        """Get list of activity of users you follow."""
        follower = current_api_user()

        args = feed_parser.parse_args()
        exclusive_start_key = args['exclusive_start_key']
        count = args['count']

        # scan_index_forward=False returns most recent first.
        # (scan_index_forward default is None, which is treated as True by
        # DynamoDB, which returns results in ascending order)
        feed = model.FeedActivity.query(follower.uuid, #limit=end,
                                        scan_index_forward=False,
                                        activity__eq=u'NewPhoto',
                                        consistent_read=False)
        def exclusive_start_key_check(i):
            if exclusive_start_key == '':
                while True:
                    yield i.next()
            else:
                while True:
                    item = i.next()
                    if exclusive_start_key == item.photo.hex:
                        for item in i:
                            yield item

        def query_filter_check(i):
            for item in i:
                if item.activity != u'NewPhoto':
                    msg = 'query does not match activity__eq correctly'
                    log.warn(msg)
                    sentry.get_client().captureMessage(msg)
                else:
                    yield item

        feed = count_iter(exclusive_start_key_check(query_filter_check(feed)),
                          count)

        result = []
        for f in feed:
            photo = get_photo(f.photo)
            if photo:
                result.append(render_photo(photo))
            else:
                log.warn("feed has missing photo FeedActivity(%s) Photo(%s)",
                            f, f.photo)
        return result


notification_history_parser = reqparse.RequestParser()
notification_history_parser.add_argument('exclusive_start_key',
                                         type=str,
                                         help='uuid (hex) of record to start after',
                                         required=False,
                                         location='values',
                                         default='')
notification_history_parser.add_argument('count',
                                type=int,
                                help='number of results to return',
                                required=False,
                                location='values',
                                default=20)
@ns_follow.route('/users/me/notification_history')
class NotificationHistory(Resource):
    @timingIncrDecorator('GET /users/me/notification_history')
    @api.doc(description="""Return is a list of items like this:
    new comment on your photo
    {
    'activity': 'NewPhoto',
    'id': unique id (uuid) of this notification item.
    'user': {  // options
        'uuid': (uuid hex of photo's user)
    },
    'photo': {
        (usual photo rendering)
    },
    'comment': {
        (usual comment rendering)
    },
    'created_on': unix time of activity,
    'read': true if not first time item appearing in feed,
    }

    Joined - You joined LocalPicTourney
    NewPhoto - A person you follow posted a new photo
    NewComment - A photo of yours has a new comment
    WonTournament - A person you follow won a tournament
    YouWonTournament - A photo of yours won a tournament
    NewFollower - You have a new follower

    List is in newest-to-oldest order.

    paging supported, ex: users/me/notification_history?exclusive_start_key=c86371aef0e511e59004c8e0eb16059b&count=3
    default paging params are
    exclusive_start_key=''
    count=50

    """)
    def get(self):
        """Get list of activity of users you follow."""
        follower = current_api_user()
        follower_uuid = follower.uuid

        args = notification_history_parser.parse_args()
        exclusive_start_key = args['exclusive_start_key']
        count = args['count']

        def exclusive_start_key_check(i):
            if exclusive_start_key == '':
                while True:
                    yield i.next()
            else:
                while True:
                    item = i.next()
                    if exclusive_start_key == item.uuid.hex:
                        for item in i:
                            yield item

        def own_photo_filter(i):
            for item in i:
                if not ('NewPhoto' == item.activity and
                        follower_uuid == item.user):
                    yield item

        def unique_filter(i):
            """Find FeedActivity duplicates and delete (not yield) them."""
            hashes = set()
            for item in i:
                hash = item.unique_hash()
                if hash is not None:
                    if hash in hashes:
                        item.delete()
                        continue
                    hashes.add(hash)
                yield item

        # scan_index_forward=False returns most recent first.
        # (scan_index_forward default is None, which is treated as True by
        # DynamoDB, which returns results in ascending order)
        feed = model.FeedActivity.query(follower.uuid, #limit=end,
                                        scan_index_forward=False,
                                        consistent_read=False)
        feed = count_iter(
                exclusive_start_key_check(
                        own_photo_filter(
                                unique_filter(feed))),
               count)
        feed = list(feed)

        auth = follower.registration == 'ok'
        result = [render_feed_activity(f, request_authorized=auth) for f in feed]
        # Mark everything as read.
        for f in feed:
            if not f.read:
                f.read = True
                f.save()
        return result

# -- Notifications ------------------------------------------------------------

@ns_user.route('/users/me/notification_settings')
class NotificationSettings(Resource):
    @timingIncrDecorator('GET /users/me/notification_settings')
    @marshal_with(notification_settings_model)
    def get(self):
        """Get your notification settings."""
        return current_api_user()

    @timingIncrDecorator('PATCH /users/me/notification_settings')
    @api.doc(description="""Body of PATCH may contain any or all of:
    'new_photo'
    'new_comment',
    'won_tournament',
    'you_won_tournament',
    'new_follower'

    if the value is set to True the authenticated user will get those
    notifications, if it is set to False they will not.
    """,
             responses={415: 'Content-Type header must be application/json',
                        204: 'User updated'},
             params={'user_uuid_str': "UUID (hex) of User to update or 'me' for the User in the Auth header"})
    def patch(self):
        """Update your notification settings."""
        user = current_api_user()

        if 'application/json' != request.headers.get('Content-Type'):
            abort(415)

        data = notification_settings_schema(request.json)

        if u'new_photo' in data:
            user.notify_new_photo = data[u'new_photo']
        if u'new_comment' in data:
            user.notify_new_comment = data[u'new_comment']
        if u'won_tournament' in data:
            user.notify_won_tournament = data[u'won_tournament']
        if u'you_won_tournament' in data:
            user.notify_you_won_tournament = data[u'you_won_tournament']
        if u'new_follower' in data:
            user.notify_new_follower = data[u'new_follower']
        user.save()

        return None, 204

# -- Flagging -----------------------------------------------------------------

@ns_photo.route('/photos/<string:photo_id>/flags')
@api.doc(params={'photo_id': 'uuid (hex) of photo to flag'})
class PhotoFlagList(Resource):
    @timingIncrDecorator('POST /photos/<photo_id>/flags')
    @api.expect(flag_input_model)
    @marshal_with(flag_model)
    def post(self, photo_id):
        """Auth User flags a Photo."""
        user = current_api_user()
        reason = flag_schema(request.data)
        flag = flag_photo(uuid_schema(photo_id),
                          current_api_user().uuid,
                          reason,
                          request.remote_addr)
        return render_flag(flag)

@ns_photo.route('/photo_comments/<string:comment_id>/flags')
@api.doc(params={'comment_id': 'uuid (hex) of comment to flag'})
class CommentFlagList(Resource):
    @timingIncrDecorator('POST /photo_comments/<comment_id>/flags')
    @api.expect(flag_input_model)
    @marshal_with(flag_model)
    def post(self, comment_id):
        """Auth User flags a Photo Comment."""
        user = current_api_user()
        reason = flag_schema(request.data)
        flag = flag_comment(uuid_schema(comment_id),
                         current_api_user().uuid,
                         reason,
                         request.remote_addr)
        return render_flag(flag)

@ns_user.route('/users/<string:user_id>/flags')
@api.doc(params={'user_id': 'uuid (hex) of user to flag'})
class UserFlagList(Resource):
    @timingIncrDecorator('POST /users/<user_id>/flags')
    @api.expect(flag_input_model)
    @marshal_with(flag_model)
    def post(self, user_id):
        """Auth User flags a User."""
        user = current_api_user()
        reason = flag_schema(request.data)
        flag = flag_user(uuid_schema(user_id),
                         current_api_user().uuid,
                         reason,
                         request.remote_addr)
        return render_flag(flag)

# -- Helpers and Misc ---------------------------------------------------------

def query_filter_check(i, name):
    for item in i:
        if not item.copy_complete:
            msg = "copy_complete__eq not working, photos with copy_complete!=True in {} results".format(name)
            log.warn(msg)
            sentry.get_client().captureMessage(msg)
        else:
            yield item
