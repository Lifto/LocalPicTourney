from __future__ import division, absolute_import, unicode_literals



import base64
import json
import os
import pprint
import random
import subprocess
import urllib2
import uuid

import requests

from util import generate_random_string

# Command Line tools for LocalPicTourney.
# Auth is achieved by setting global variables USER_UUID_HEX and AUTH_TOKEN.
# If you call get_token() you will register a new user and those globals will
# be set.  After that calls to 'open_auth' will access the server using those
# credentials.  'open' is for opening public URLs.  Photos can be posted using
# 'test_photo_upload' and 'post_folder'.
URL = 'localpictourney-0-2-api-dev.schpmmpm25.us-west-2.elasticbeanstalk.com/'
# These are valid until we drop the DB again.
DAN_UUID_HEX = '1687cdce8f4511e58c49025ca80a5bcf'  # not valid
DAVE_UUID_HEX = 'cabc00a4d73d11e586e706e42940248b'
ELLIS_UUID_HEX = '58b38056f16711e5a8f50a1e0e6f5195'
ELLIS_TOKEN = u'_a_.4GAb7NblargblargQqg4cN4z'
TEST_USER_TOKEN = u'_a_.iAGF7wblargblargwyKqgwMw3G'
TEST_USER_UUID_HEX = u'80268ae46e2a11e6ab59060a3b9f8ce7'

USER_UUID_HEX = u'2a3c3fc2859811e6b3b50a612b374805'
AUTH_TOKEN = '_a_.RhwblargblargKB2'
LOCATION_STRING = "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"

def url(input, version='v0.2'):
    if version is not None:
        return 'http://{base}/{version}/{input}'.format(base=URL,
                                                        version=version,
                                                        input=input)
    else:
        return 'http://{base}/{input}'.format(base=URL, input=input)

def auth_header_value(user_uuid, token):
    # Username and password are combined into a string "username:password"
    val = u'%s:%s' % (user_uuid, token)
    val_encoded = base64.b64encode(val)
    header = u'Authorization: Basic %s' % val_encoded
    return header

def headers_with_auth(USER_UUID_HEX, token):
    return {'Content-Type': 'application/json',
            'Authorization': auth_header_value(USER_UUID_HEX, token),
            'Geo-Position': LOCATION_STRING}

def open_url(url, post=None):
    """Open a public LocalPicTourney url."""
    full_url = 'http://%s/%s' % (URL, url)
    request = urllib2.Request(full_url, post)
    try:
        result = urllib2.urlopen(request)
    except urllib2.HTTPError as error:
        result = error
    try:
        data = json.loads(result.read())
    except ValueError:
        print 'WARNING: RESULT WAS NOT JSON'
        data = result.read()
    return data

def open_auth(url, post=None, is_put=False, is_patch=False,
              user_uuid_hex=USER_UUID_HEX, auth_token=AUTH_TOKEN):
    """Open a LocalPicTourney url as a registered user."""
    if not user_uuid_hex:
        print 'need USER_UUID_HEX call get_token'
        raise ValueError
    if not auth_token:
        print 'need AUTH_TOKEN call get_token'
        raise ValueError
    full_url = 'http://%s/%s' % (URL, url)
    request = urllib2.Request(
        full_url,
        post,
        headers_with_auth(user_uuid_hex, auth_token))
    if is_put:
        request.get_method = lambda: 'PUT'
    elif is_patch:
        request.get_method = lambda: 'PATCH'
    # request.add_header('Content-Type', 'your/contenttype')
    try:
        result = urllib2.urlopen(request)
    except urllib2.HTTPError as e:
        print '{} request had error'.format(url)
        print e
        print e.reason
        print e.geturl()
        print e.info()
        result = e
    else:
        print '{} result code {}'.format(url, result.code)
    if result.code == 204:
        data = None
    else:
        data = json.loads(result.read())

    return data

def get_token():
    """Register a new user and set this module's globals to its credentials."""
    request = urllib2.Request('http://%s/v0.2/users' % URL,
                              json.dumps({'is_test_user': 'true'}),
                              headers={'Geo-Position': LOCATION_STRING,
                                       'Content-Type': 'application/json'})
    result = urllib2.urlopen(request)
    data = json.loads(result.read())
    global USER_UUID_HEX, AUTH_TOKEN
    USER_UUID_HEX = data["uuid"]
    AUTH_TOKEN = data["token"]
    print USER_UUID_HEX
    print AUTH_TOKEN
    return data

def test_photo_upload(enter_in_tournament=False,
                      filename='data/testphoto',
                      user_uuid_hex=None, auth_token=None,
                      test_comments=True,
                      media_type=None):
    """Will upload a test photo to the server using a signed URL."""
    #global AUTH_TOKEN, USER_UUID_HEX
    #print 'getting token...'
    #get_token()
    # print 'got token.'
    # print 'setting location...'
    # data = json.dumps({'location':
    #                    "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"})
    # open_auth('users/me', data, is_patch=True)
    # print 'location set'
    # print 'setting gender'
    # data = json.dumps({'gender': 'male'})
    # open_auth('users/me', data, is_patch=True)
    # print 'show_gender set'

    print('getting upload URL')
    raw_data = {'set_as_profile_photo': True}
    raw_data['enter_in_tournament'] = enter_in_tournament
    if media_type is not None:
        raw_data['media_type'] = media_type
    data = json.dumps(raw_data)

    photo_result = open_auth('/v0.1/photos', data,
                             user_uuid_hex=user_uuid_hex, auth_token=auth_token)
    pprint.pprint(photo_result)
    print 'got URL'

    try:
        fields = photo_result[u'post_form_args'][u'fields']
    except KeyError:
        print 'photo had key error'
        pprint.pprint(photo_result)
        return 1
    key = None
    for field in fields:
        if field[u'name'] == u'key':
            key = field[u'value']
    photo_uuid_hex = key[-32:]
    photo_url = photo_result[u'post_form_args']['action']
    form_arg = ['-F %s=%s' % (x['name'], x['value']) for x in photo_result[u'post_form_args']['fields']]
    form_arg.append('-F file=@%s' % filename)
    cmd = 'curl -i -L -X POST "%s" %s' % (photo_url, ' '.join(form_arg))
    print 'calling Curl'
    subprocess.call(cmd, shell=True)
    print 'Curl done'
    print ''
    print 'getting back thumbnail'
    from time import sleep
    sleep(5)
    result = open_auth('/v0.1/users/me',
                       user_uuid_hex=user_uuid_hex, auth_token=auth_token)
    pprint.pprint(result)
    result = open_auth('/v0.1/users/me/photos/small',
                       user_uuid_hex=user_uuid_hex, auth_token=auth_token)
    if 'url' not in result:
        print 'ERROR url not in result'
        pprint.pprint(result)
        return
    else:
        check_url = result['url']
    print check_url
    print 'photo get status %s' % urllib2.urlopen(check_url).code
    result = open_auth('/v0.1/users/me/photos/medium',
                       user_uuid_hex=user_uuid_hex, auth_token=auth_token)
    if 'url' not in result:
        print 'ERROR url not in result'
        pprint.pprint(result)
        return
    else:
        check_url = result['url']
    print check_url
    print 'photo get status %s' % urllib2.urlopen(check_url).code
    result = open_auth('v0.1/users/me/photos/game',
                       user_uuid_hex=user_uuid_hex, auth_token=auth_token)
    if 'url' not in result:
        print 'ERROR url not in result'
        pprint.pprint(result)
        return
    else:
        check_url = result['url']
    print check_url
    print 'photo get status %s' % urllib2.urlopen(check_url).code
    result = open_auth('v0.1/users/me/photos/original',
                       user_uuid_hex=user_uuid_hex, auth_token=auth_token)
    if 'url' not in result:
        print 'ERROR url not in result'
        pprint.pprint(result)
        return
    else:
        check_url = result['url']
    print check_url
    print 'photo get status %s' % urllib2.urlopen(check_url).code
    return
    print 'preparing new user to post comments'

    def confirm_comments(result, num):
        num_comments = len(result.get('comments', []))
        if num_comments == num:
            print "OK"
        else:
            print "ERROR"
            print '    got comments %s, expected %s' % (num_comments, num)

    # Post some comments and read them back.
    if not test_comments:
        return
    get_token()
    user_2_token = AUTH_TOKEN
    user_2_uuid = USER_UUID_HEX
    data = json.dumps({'location':
                       "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"})
    open_auth('/v0.1/users/me', data, is_patch=True)
    data = json.dumps({'view_gender': 'male'})
    open_auth('users/me', data, is_patch=True)
    comment_url = '/v0.1/photos/%s/comments' % photo_uuid_hex
    print 'confirming no comments'
    confirm_comments(open_auth(comment_url), 0)

    print 'posting comment 1'
    open_auth(comment_url, post="test comment 1")
    confirm_comments(open_auth(comment_url), 1)

    get_token()
    user_3_token = AUTH_TOKEN
    user_3_uuid = USER_UUID_HEX
    data = json.dumps({'location':
                       "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"})
    open_auth('/v0.1/users/me', data, is_patch=True)
    data = json.dumps({'view_gender': 'male'})
    open_auth('v0.1/users/me', data, is_patch=True)
    print 'posting comment 2'
    open_auth(comment_url, post="test comment 2")
    confirm_comments(open_auth(comment_url), 2)

    AUTH_TOKEN = user_2_token
    USER_UUID_HEX = user_2_uuid
    print 'posting comment 3'
    open_auth(comment_url, post="test comment 3")
    confirm_comments(open_auth(comment_url), 3)

    print 'posting comment 4'
    open_auth(comment_url, post="test comment 4")
    confirm_comments(open_auth(comment_url), 4)

    AUTH_TOKEN = user_3_token
    USER_UUID_HEX = user_3_uuid
    print 'posting comment 5'
    open_auth(comment_url, post="test comment 5")
    confirm_comments(open_auth(comment_url), 5)

def test_judging():
    result = open_auth('v0.2/users/me/matches')
    print(result)
    matches = result['matches']
    import random
    for match in matches:
        print('voting on match')
        data = random.choice(['a', 'b'])
        open_auth('v0.2/users/me/matches/%s' % match['match_id'], data,
                  is_put=True)

def test_flagging():
    global AUTH_TOKEN, USER_UUID_HEX
    print 'getting token...'
    get_token()
    print 'got token.'
    print 'setting location...'
    open_auth('users/me/location',
              "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0",
              is_put=True)
    print 'location set'
    print 'setting gender...'
    open_auth('users/me/gender', 'male', is_put=True)

    result = open_auth('users/me/matches')
    matches = result['matches']
    match = matches[0]
    photo_uuid = match['photo_a']['id']
    user_uuid = match['photo_a']['user']

    open_auth('users/%s/flags' % user_uuid, 'test flag')
    open_auth('photos/%s/flags' % photo_uuid, 'test flag')


def test_b64(count=1000000):
    """Test whether b64 preserves order of order packed time uuids."""
    from logic.timeuuid import pack_timeuuid_binary
    from uuid import uuid1
    from base64 import b64encode
    for x in xrange(count):
        u1 = pack_timeuuid_binary(uuid1())
        bu1 = b64encode(u1)
        u2 = pack_timeuuid_binary(uuid1())
        bu2 = b64encode(u2)
        if u1 > u2 and bu2 < bu1:
            print 'error found on count %s' % x
            raise ValueError
    print 'done'

def post_seed_data(user_uuids=[
        uuid.UUID('57756fded5be11e5a45506e42940248b'),
        uuid.UUID('b603bb00d5be11e5858806e42940248b'),
        uuid.UUID('245baa36d5bf11e5a45506e42940248b')]):
    # Must be run with real aws credentials and dynamodb connection
    if not user_uuids:
        user_uuids = [uuid.UUID(TEST_USER_UUID_HEX)]
    import model
    users = [model.User.get(user_uuid) for user_uuid in user_uuids]
    # We need to fudge the user's registration status, as our test users
    # do not have facebook ids.
    from logic import user
    for x in users:
        if not x.facebook_api_token:
            x.facebook_api_token = 'fakeapitoken'
        if not x.user_name:
            x.user_name = 'user test user name ' + generate_random_string(5)
        if not x.get_categories():
            x.sub_landscape = True
        user.update_registration_status(x)

    covers = {}
    import os
    from time import sleep
    global USER_UUID_HEX, AUTH_TOKEN
    USER_UUID_HEX = TEST_USER_UUID_HEX
    AUTH_TOKEN = TEST_USER_TOKEN
    for user in os.listdir('./seed_users'):
        for file_name in os.listdir('./data/seed_data/' + category):
            print 'post for %s, %s' % (category, file_name)
            sleep(2)
            try:
                print 'getting upload URL'
                data = json.dumps({'category': category})
                photo_result = open_auth('photos', data)
                print 'got URL'

                photo_url = photo_result['action']
                form_arg = ['-F %s=%s' % (x['name'], x['value']) for x in photo_result['fields']]
                #filename = '69635f54b15411e4a19fc8e0eb16059b'
                form_arg.append('-F file=@%s' % '%s/%s' % ('./data/seed_data/' + category, file_name))
                cmd = 'curl -i -L -X POST "%s" %s' % (photo_url, ' '.join(form_arg))
                print 'calling Curl with command:'
                print cmd
                subprocess.call(cmd, shell=True)
                print 'Curl done'
                if 'cover' in file_name:
                    fields = photo_result['fields']
                    for item in fields:
                        if item['name'] == 'key':
                            covers[category] = item['value']
            except:
                print "got error for %s, skipping" % file_name

    import pprint
    pprint.pprint(covers)

def post_seed_data():
    covers = {}
    import os
    from time import sleep
    global USER_UUID_HEX, USER_ACCESS_TOKEN
    USER_UUID_HEX = TEST_USER_UUID_HEX
    USER_ACCESS_TOKEN = TEST_USER_TOKEN
    for file_name in os.listdir(path):
        sleep(2)
        try:
            print 'getting upload URL'
            data = json.dumps({'category': category})
            photo_result = open_auth('photos', data)
            print 'got URL'

            photo_url = photo_result['action']
            form_arg = ['-F %s=%s' % (x['name'], x['value']) for x in photo_result['fields']]
            #filename = '69635f54b15411e4a19fc8e0eb16059b'
            form_arg.append('-F file=@%s' % '%s/%s' % (path, file_name))
            cmd = 'curl -i -L -X POST "%s" %s' % (photo_url, ' '.join(form_arg))
            print 'calling Curl with command:'
            print cmd
            subprocess.call(cmd, shell=True)
            print 'Curl done'
        except:
            print "got error for %s, skipping" % file_name

# def test_emoji():
#     """Will make an account with an emoji username."""
#     print('getting token...')
#     get_token()
#     print('got token.')
#     # OK HAND SIGN == u'\U0001F44C'
#     name = generate_random_string(2) + \
#            " " + \
#            generate_random_string(3) + \
#            u'\U0001F44C'
#     assert(unicode == type(name))
#     print("setting user name to '%s'..." % name)
#     #encoded_name = name.encode('unicode-escape')
#     encoded_name = name.encode('utf8')
#     #from urllib import quote_plus
#     #encoded_name = quote_plus(encoded_name)
#     assert(str == type(encoded_name))
#     print(encoded_name)
#     #foo = "encoded name is '%s'" % encoded_name
#     #print(foo)
# #    cmd = 'curl -i -L -X PUT --data "%s" --header "Content-Type: text/plain; charset=utf-8" %s' % encoded_name
# #             print 'calling Curl with command:'
# #             print cmd
# #             subprocess.call(cmd, shell=True)
# #             print 'Curl done'
#     open_auth('users/me/name', encoded_name, is_put=True)
#     print('reading back name')
#     from time import sleep
#     sleep(1)
#     data = open_auth('users/me')
#     result_name = data['name']
#     print("got back (%s) '%s'" % (type(result_name), result_name))
#     if(result_name == encoded_name):
#         print("success")
#     else:
#         print("FAIL")


# Note. We get our city information from a Google doc that is exported as csv.
# We read this csv and put it into a postgres db. For convenience when we
# update the city list we drop the DB and recreate it, it takes about 45
# seconds.
# Important to note is that a location is identified by its UUID. The CSV
# does not contain UUIDs.
def reset_cities_db():
    import codecs
    import psycopg2
    with codecs.open('locations.csv', 'r', 'utf-8') as f:
        print 'file opened'
        conn = psycopg2.connect(database='loc',
                                user='awsuser',
                                password='secretpassword',
                                host='localpictourneylocationdev.somehost.us-west-2.rds.amazonaws.com')
        print 'connection established'
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        print 'cursor created'
        try:
            cur.execute("DROP TABLE cities;")
            print "table 'cities' dropped"
        except psycopg2.ProgrammingError:
            print "table 'cities' was not in database"
        cur.execute("""
          CREATE TABLE cities (
              id UUID PRIMARY KEY,
              geom GEOMETRY(Point, 4326),
              country VARCHAR(128),
              city VARCHAR(128),
              accent_city VARCHAR(128),
              region VARCHAR(128),
              population BIGINT,
              notes VARCHAR(128));
          """)

        cur.execute("CREATE INDEX cities_gix ON cities USING GIST (geom);")

        while True:
            l = f.readline()
            if not l:
                break
            should_include, country, city, accent_city, region, population, lat, lon, uuid, notes = l.split(',')

            if should_include == 't':
                cmd = "INSERT INTO cities (id, geom, country, city, accent_city, region, population, notes) VALUES ('{uuid}', ST_GeomFromText('POINT({lat} {lon})', 4326), '{country}', '{city}', '{accent_city}', '{region}', '{population}', '{notes}');".format(
                    uuid=uuid, country=country, city=city, accent_city=accent_city,
                    region=region, population=int(population), lat=lat, lon=lon,
                    notes=notes)
                print cmd
                cur.execute(cmd)

        cur.close()
        conn.close()

def make_csv():
    """Given the exported CSV from google docs, add uuids to each row.
    This was used only once, but is here in case it is useful in the future."""
    import codecs
    from uuid import uuid1
    with codecs.open('locations.csv', 'r', 'utf-8') as f:
        with codecs.open('output.csv', 'w', 'utf-8') as o:
            while True:
                l = f.readline()
                if not l:
                    break
                #should_include, country, city, accent_city, region, population, lat, lon, notes = l.split(',')
                line = l.split(',')
                uuid = uuid1().hex
                line.insert(-1, uuid)
                o.write(','.join(line))

def make_leaderboards_template():
    """Given 'locations_csv', create the template to be rendered when
     GET /leaderboards is requested."""
    import codecs
    output = []
    with codecs.open('locations.csv', 'r', 'utf-8') as f:
        while True:
            l = f.readline()
            if not l:
                break
            should_include, country, city, accent_city, region, population, lat, lon, loc_uuid, notes = l.split(',')
            if should_include == 't':
                output.append({
                    'country': country,
                    'city': city,
                    'accent_city': accent_city,
                    'region': region,
                    'population': population,
                    'lat': lat,
                    'lon': lon,
                    'uuid': loc_uuid
                })

    with codecs.open('leaderboards.json', 'w', 'utf-8') as o:
        o.write(json.dumps(output))

def filter_table(source, for_each=lambda(source): None, auto_save=True):
    iter = source.scan()
    count = 0
    for item in iter:
        count += 1
        print count
        for_each(item)
        if auto_save:
            item.save()

def copy_table(source, target, for_each=None, sleep_seconds=1.0):
    from time import sleep
    hash_key = None
    range_key = None
    attrs = set()
    for name, attr in source._get_attributes().iteritems():
        #if name == 'dupe_hash':
        #    continue
        if attr.is_hash_key:
            hash_key = attr
        elif attr.is_range_key:
            range_key = attr
        else:
            attrs.add(name)

    iter = source.scan()
    count = 0
    for item in iter:
        count += 1
        print count
        if range_key:
            new = target(getattr(item, hash_key.attr_name),
                         getattr(item, range_key.attr_name))
        else:
            new = target(getattr(item, hash_key.attr_name))
        for name in attrs:
            setattr(new, name, getattr(item, name))
        if for_each:
            for_each(item, new)
        new.save()
        sleep(sleep_seconds)

# Note: You need the prod credentials for this, not the local ones.
def iter_fix():
    """Scan all tables for missing locations and add them."""
    from logic import location
    geo = location.Geo.from_string(LOCATION_STRING)
    loc = location.Location.from_geo(geo)
    # print 'got geo and loc'
    import datetime
    d = datetime.datetime(1982, 4, 1)
    from uuid import UUID

    def do_class(cls):
        print 'scanning %s' % cls
        iter = cls.scan()
        count = 0
        for item in iter:
            count += 1
            print count
            file_name = item.photo_file_name
            # exists = None
            # try:
            #     exists = Photo.get(item.gender_location, item.uuid)
            # except:
            #     pass
            if not file_name:
                print 'skipping, no file name'
                continue
            photo_uuid = None
            try:
                photo_uuid = UUID(file_name[-32:])
            except:
                pass
            if not photo_uuid:
                print 'skipping, no uuid from file name'
                continue
            item.photo = photo_uuid
            if not item.last_tournament_status_access:
                item.last_tournament_status_access = d
            if not item.matches_until_next_tournament:
                item.matches_until_next_tournament = 10
            if not item.next_tournament:
                item.next_tournament = 'local'
            item.save()


    import model
#    do_class(model.User)
    do_class(model.User)
 #   do_class(model.PhotoComment)
  #  do_class(model.Match)
   # do_class(model.Tournament)

# TODO use api to make our own test users.
# This is from facebook docs.
# Testing your App
# Apps can create test users and use them to make API calls.
#  Use These APIs
#
# /{app-id}/accounts/test-users to create and associate test users.
# /{test-user-id} to update a test user's password or name.
FB_ID = '12345'
FB_SECRET = '1234567890'

# To generate an app access token, you need to make a Graph API call:
get_new_token_url = "https://graph.facebook.com/v2.4/oauth/access_token?client_id={fb_id}&client_secret={fb_secret}&grant_type=client_credentials".format(
    fb_id=FB_ID,
    fb_secret=FB_SECRET)

FB_TOKEN = u'1234567890'
# or, the docs say for an access token you can just append fb_id | fb_secret

get_test_users_url = "https://graph.facebook.com/v2.4/{app_id}/accounts/test-users?&access_token={token}".format(app_id=FB_ID, token=FB_TOKEN)

# https://graph.facebook.com/APP_ID/accounts/test-users?
#   installed=true
#   &name=FULL_NAME
#   &permissions=read_stream
#   &method=post
#   &access_token=APP_ACCESS_TOKEN
generate_test_user_url = "https://graph.facebook.com/v2.4/{app_id}/accounts/test-users?installed=true&permissions=read_stream&method=post&access_token={token}".format(app_id=FB_ID, token=FB_TOKEN)

post_photo_to_fb_url = "https://graph.facebook.com/v2.4/me/photos&access_token={{token}}"

USER_ACCESS_TOKEN = u'123456789'


FB_FIELDS = ','.join(['id', 'about', 'address', 'age_range', 'bio', 'birthday',
             'context', 'currency', 'devices', 'education', 'email',
             'favorite_athletes', 'favorite_teams', 'first_name', 'gender',
             'hometown', 'inspirational_people', 'install_type', 'installed',
             'interested_in', 'is_shared_login', 'is_verified', 'languages',
             'last_name', 'link', 'location', 'locale', 'meeting_for',
             'middle_name', 'name', 'name_format', #'payment_pricepoints',
             'test_group', 'political', 'relationship_status', 'religion',
             'security_settings', 'significant_other', 'sports', 'quotes',
             'third_party_id', 'timezone', #'token_for_business',
             'updated_time', 'shared_login_upgrade_required_by', 'verified',
             'video_upload_limits', 'viewer_can_send_gift', 'website', 'work',
             'public_key', 'cover'])
# url = "https://graph.facebook.com/v2.4/me?fields={fields}&access_token={user_token}".format(
#    user_token=USER_ACCESS_TOKEN, fields=FB_FIELDS)

# Test results for general info we asked for.
# {u'name': u'Will Alajaciaajacb Romanescu', u'id': u'125980107749959'}

def open_fb(url, post=None):
    try:
        result = urllib2.urlopen(url, post)
    except urllib2.HTTPError as error:
        result = error
    try:
        data = json.loads(result.read())
    except ValueError:
        print 'WARNING: RESULT WAS NOT JSON'
        data = result.read()
    return data

# TODO: Leave just one user in our test users, give it the big photo by hand.
# Hardcode the id here (or find it be getting the list of test users)
# then get a new access token for that user with the api (possible? not sure.
# It's not possible, need to use web interface.) then run the test.
def test_facebook_reg():
    """Will upload a test photo to the server using a signed URL."""
    #global AUTH_TOKEN, USER_UUID_HEX
    # Note: It looks like we can't get a new access token for the user through
    # the api, we need to do it through the web interface.
    # print "listing facebook users"
    # test_user_data = open_fb(get_test_users_url)
    # users = test_user_data[u'data']
    # test_user_facebook_id = None
    # if users:
    #     test_user_facebook_id = users[0][u'id']
    # print 'test user facebook id %s' % test_user_facebook_id
    # import pprint
    # pprint.pprint(test_user_data)
    # return

#    print "creating new facebook user"
#    test_user_data = open_fb(generate_test_user_url)
#    print test_user_data
    # Looks like this:
    # {u'access_token': u'123456',
    #  u'email': u'user_name@texample.net',
    #  u'id': u'106077929749348',
    #  u'login_url': u'https://developers.facebook.com/checkpoint/test-user-login/212121232435/',
    #  u'password': u'123456789'}

    facebook_token = USER_ACCESS_TOKEN #test_user_data[u'access_token']

    # This does not set the profile photo.
#    url = post_photo_to_fb_url.format(token=facebook_token)
#    # TODO: It seems you can upload the pic this way, but you can't set
#    # a profile pic this way. Could I try logging in using curl and uploading
#    # that way?
#    cmd = 'curl -i -L -X POST "{url}" -F file=@test2.jpg'.format(url=url)
#    print 'uploading photo with curl: {cmd}'.format(cmd=cmd)
#    print subprocess.check_output(cmd, shell=True)
#    print 'Curl done'

    #print 'getting token...'
    #get_token()
    #print 'got token.'
    # print 'setting location and name...'
    # from util import generate_random_string as rand_string
    # data = json.dumps({'location':
    #                    "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0",
    #                    'user_name': 'test name 1 ' + rand_string(20)})
    # open_auth('users/me', data, is_patch=True)
    # print 'checking location and name...'
    # result = open_auth('users/me')
    # print "location was '%s'" % result.get('location')
    # print "name was '%s'" % result.get('name')
    # print 'location and name set'
    # print 'setting gender'
    # data = json.dumps({'gender': 'male'})
    # open_auth('users/me', data, is_patch=True)
    # print 'show_gender set'
    #
    # result = open_auth('users/me')
    # pprint.pprint(result)

    print 'setting facebook token'
    open_auth('users/me/facebook',
              json.dumps({'token': facebook_token}),
              is_put=True)
    from time import sleep
    sleep(10)
    result = open_auth('users/me')
    pprint.pprint(result)
    registration = result['registered']
    if registration != 'ok':
        print "FAIL registration state was '%s' expected 'ok'" % registration
    else:
        print "OK"
    facebook_id = result.get('facebook_id')
    if not facebook_id:
        print "FAIL facebook_id was not in results"
        return

#    data = json.dumps({'facebook_api_token': USER_ACCESS_TOKEN})
    import requests
    full_url = 'http://%s/users_by_facebook/%s' % (URL, facebook_id)
    result = requests.get(full_url,
                          json={'facebook_api_token': USER_ACCESS_TOKEN})
    print result
    data = result.json()
    pprint.pprint(data)
 #   result = open_url('users_by_facebook/%s' % facebook_id, post=data)
    if data and data.get('token') == AUTH_TOKEN:
        print 'OK - got token with facebook api token'
    else:
        pprint.pprint(result)
        print AUTH_TOKEN
        print 'FAIL - result auth token not equal to got auth token we got'


def for_each_photo():
    import os
    from PIL import Image
    from logic.s3 import get_serve_bucket
    bucket = get_serve_bucket()
    photo_dir = '/Users/lifto/Documents/Development/LocalPicTourney/temp/'
    keys = list(bucket.list())
    i = 1
    for key in bucket.list():
        print 'photo %s of %s' % (i, len(keys))
        key_name = key.name.encode('utf-8')
        photo_path = '%s%s' % (photo_dir, key_name)
        key.get_contents_to_filename(photo_path)
        try:
            im = Image.open(photo_path)
            im.save(photo_path + 'out', "JPEG", progressive=True)
        except IOError as e:
            print e
            print 'Cannot create thumbnail for %s', photo_path+'out'

        key.set_contents_from_filename(photo_path+'out')

        os.remove(photo_path)
        os.remove(photo_path+'out')
        i += 1

# migrate user.name => user.user_name, add user.real_name
def m_user_names():
    from model import User
    from util import now
    def for_each(user):
        user.user_name = user.name
        if user.last_tournament_status_access is None:
            user.last_tournament_status_access = now()
        if user.matches_until_next_tournament is None:
            user.matches_until_next_tournament = 10
        if user.next_tournament is None:
            user.next_tournament = 'local'
        user.save()
    filter_table(User, for_each)


def fix_missing_photos():
    # https://github.com/LocalPicTourneyInc/localpictourney-issues/issues/63
    # Iterate through User table, if find a photo that is not in s3, set
    # to None
    import settings
    def set_none_if_bad(photo):
        if photo.copy_complete:
            photo_url = '%s/%s_%%s' % (settings.SERVE_BUCKET_URL, photo.file_name)
            url_small = photo_url % '240x240'
            cmd = 'curl -i -L "%s"' % (url_small)
            if subprocess.check_output(cmd, shell=True)[:12] == 'HTTP/1.1 403':
                print 'found bad photo %s, disabling' % photo.uuid
                photo.live = False
                photo.uploaded = False
                photo.copy_complete = False
                try:
                    if photo.set_as_profile_photo is None:
                        print "set_as_profile_photo was None, setting False"
                        photo.set_as_profile_photo = False
                except AttributeError:
                    pass
                photo.save()
                user = model.User.get(photo.user_uuid)
                if user.photo == photo.uuid:
                    print 'photo was user photo, setting to None'
                    user.photo = None
                    user.save()
    import model
    print "filter on Photo"
    filter_table(model.Photo, set_none_if_bad, auto_save=False)
    print "filter on ProfileOnlyPhoto"
    filter_table(model.ProfileOnlyPhoto, set_none_if_bad, auto_save=False)

def fix_photos():
    """Iter all photos, get user, if user has no profile photo, set it here."""
    from model import User
    import uuid
    exempt = uuid.UUID('3e9fbd32-d260-11e4-bd4e-02fc1f809594')
    def user_print(user):
        if user.geodata == "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0" and user.uuid != exempt:
            print '%s, %s, %s' % (user.uuid, user.geodata, user.user_name)
    filter_table(User, user_print, auto_save=False)

LOREM_BIO = """Lorem ipsum dolor sit amet, consectetur adipiscing elit. Nunc ante est, fermentum eu est tristique, interdum euismod lorem. Proin sed lobortis quam."""
LA_UUID = uuid.UUID('67f22847-ecf3-11e4-a264-c8e0eb16059b')

def add_mp4s():
    """Put the mp4s I added to test data into the server."""
    path = './data/seed_data/f/%s' % LA_UUID.hex
    for user_name in os.listdir(path):
        if user_name.startswith('.'):  # .DS_Store I'm looking at you.
            continue
        user_path = '%s/%s' % (path, user_name)
        print(user_name)
        data_path = '%s/%s/data' % (path, user_name)
        with open(data_path) as f:
            user_data = json.load(f)

        api_user_name = user_data['user_name']
        import model
        from pynamodb.exceptions import DoesNotExist

        try:
            user_name = model.UserName.get(api_user_name)
        except DoesNotExist:
            print('could not load user by name {}'.format(api_user_name))
            continue
        try:
            user = model.User.get(user_name.user_uuid)
        except DoesNotExist:
            print('could not load user by uuid {}'.format(user_name.user_uuid.hex))
            continue
        headers = headers_with_auth(user.uuid.hex, user.token)

        photo_file_names = [x for x in os.listdir(user_path) if x.startswith('ex')]


        # Get an upload URL for our image
        for photo_file_name in photo_file_names:
            print 'photo_file_name %s' % photo_file_name
            data = {
                'set_as_profile_photo': True,
                'enter_in_tournament': True,
                'media_type': 'movie'
            }
            for i in range(5):
                try:
                    photo_result = requests.post(url('photos'),
                                                 json=data,
                                                 headers=headers)
                except Exception as exp:
                    print 'post photo had exception'
                    print exp
                    photo_result = None
                if photo_result is not None:
                    if not photo_result.ok:
                        print 'photo photo result was not OK'
                        print photo_result
                        from time import sleep
                        sleep(1.0 * (i + 1))
                    else:
                        photo_data = photo_result.json()
                        try:
                            fields = photo_data[u'post_form_args'][u'fields']
                        except KeyError:
                            print 'photo result did not have all data'
                            print photo_data
                            from time import sleep
                            sleep(1.0 * (i + 1))
                        else:
                            break
            else:
                print 'could not post photo, returning'
                return

            key = None
            for field in fields:
                if field[u'name'] == u'key':
                    key = field[u'value']
                    break
            photo_uuid_hex = key[-32:]
            photo_url = photo_data[u'post_form_args']['action']
            form_arg = ['-F %s=%s' % (x['name'], x['value']) for x in fields]
            local_filename = '%s/%s' % (user_path, photo_file_name)
            print 'uploading %s' % local_filename
            form_arg.append('-F file=@%s' % local_filename)
            cmd = 'curl -i -L -X POST "%s" %s' % (photo_url, ' '.join(form_arg))
            for i in range(5):
                try:
                    subprocess.call(cmd, shell=True)
                except Exception as exp:
                    print 'call curl for photo upload had exception'
                    print exp
                    sleep(1 * (i + 1))
                else:
                    break


# def make_test_users(resume_at=None):
#     from location import LOCATIONS
#     count = 0
#     for location_uuid, location in LOCATIONS.items():
#         for gender in ['male', 'female']:
#             location_geo = "{lat};{lon};0.0 hdn=-1.0 spd=0.0".format(
#                 lat=location.lat, lon=location.lon)
#             for x in range(6):
#                 # Confirm is a little slow, so only confirm every 25 users.
#                 if resume_at is None or count > resume_at:
#                     make_random_user(gender, location_geo, confirm=count%25==0)
#                 count += 1
#                 print count

def make_random_user(gender, location_geo, confirm=True):
    # gender must be 'male' or 'female'
    # location is the geo string that would be in the header of the post.
    # ex: "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"
    from time import sleep

    # Get some random user data.
    result = requests.get(
        'https://randomuser.me/api/?gender={gender}&nat=us'.format(gender=gender))
    user_data = result.json()
    user = user_data[u'results'][0][u'user']
    user_first_name = user[u'name'][u'first']
    user_last_name = user[u'name'][u'last']
    user_name = '{first} {last}'.format(first=user_first_name,
                                        last=user_last_name)
    photo_url = user[u'picture'][u'large']

    result = requests.post(url('users'),
                           json={'is_test_user': 'true'},
                           headers={'Geo-Position': location_geo,
                                    'Content-Type': 'application/json'})
    if not result.ok:
         print 'creating user had status {status}'.format(status=result.status_code)
    data = result.json()
    user_uuid_hex = data["uuid"]
    auth_token = data["token"]
    headers = headers_with_auth(user_uuid_hex, auth_token)
    print headers

    # Make sure user creation completed
    while True:
        result = requests.get(url('users/me'), headers=headers)
        if result.ok:
            break
        else:
            print 'user retry'
            sleep(0.5)

    user_data = {
        'location': location_geo,
        'gender': gender,
        'user_name': user_name,
        'first_name': user_first_name,
    }
    if random.random() > .3:
        user_data['biography'] = LOREM_BIO
    if random.random() > .3:
        user_data['snapchat'] = user_first_name + generate_random_string(3)
    if random.random() > .3:
        user_data['instagram'] = user_last_name + generate_random_string(3)
    while True:
        result = requests.patch(url('users/me'),
                                json=user_data,
                                headers=headers)
        if not result.ok:
            print 'user info had status {status}'.format(status=result.status_code)
            if result.status_code == 409:
                print 'user info status code 409, trying different name'
                pprint.pprint(result.json())
                rand = generate_random_string(5)
                user_first_name += rand
                user_last_name += rand
                user_name += rand
                user_data['user_name'] = user_name
                user_data['first_name'] = user_first_name
        else:
            break

    # Get the image from randomuser that we'll upload.
    local_filename = 'tempimage'
    r = requests.get(photo_url, stream=True)
    with open(local_filename, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024):
            if chunk: # filter out keep-alive new chunks
                f.write(chunk)
                #f.flush() commented by recommendation from J.F.Sebastian
    if not result.ok:
        print 'download had status {status}'.format(status=result.status_code)
        return data

    # Make sure location update completed
    while True:
        result = requests.get(url('users/me'), headers=headers)
        if result.ok and result.json().get('location'):
            break
        else:
            print 'location retry'
            sleep(0.5)

    # Get an upload URL for our image
    while True:
        photo_result = requests.post(url('photos'),
                                     json={'set_as_profile_photo': True,
                                           'enter_in_tournament': True},
                                     headers=headers)
        photo_data = photo_result.json()

        try:
            fields = photo_data[u'fields']
        except KeyError:
            print 'photo retry'
            pass
        else:
            break

    key = None
    for field in fields:
        if field[u'name'] == u'key':
            key = field[u'value']
    photo_uuid_hex = key[-32:]
    photo_url = photo_data['action']
    form_arg = ['-F %s=%s' % (x['name'], x['value']) for x in photo_data['fields']]
    form_arg.append('-F file=@%s' % local_filename)
    cmd = 'curl -i -L -X POST "%s" %s' % (photo_url, ' '.join(form_arg))
    subprocess.call(cmd, shell=True)

    if confirm:
        from time import sleep
        sleep(2)

        result = requests.get(url('users/me'), headers=headers)
        user_data = result.json()
        if user_data['user_name'] != user_name:
            print 'user name did not set, expected {expected} got {got}'.format(
                expected=user_name, got=user_data['user_name']
            )
        if user_data['first_name'] != user_first_name:
            print 'first name did not set, expected {expected} got {got}'.format(
                expected=user_first_name, got=user_data['first_name']
            )

        result = requests.get(url('users/me/photos/small'), headers=headers)
        result_json = result.json()
        print result_json
        if 'url' not in result_json:
            print 'ERROR url not in result'
            pprint.pprint(result_json)
            return
        else:
            check_url = result_json['url']
        if not requests.get(check_url).ok:
            print 'photo {check_url} had error'.format(check_url=check_url)

        result = requests.get(url('users/me/photos/medium'), headers=headers)
        result_json = result.json()
        if 'url' not in result_json:
            print 'ERROR url not in result'
            pprint.pprint(result_json)
            return
        else:
            check_url = result_json['url']
        if not requests.get(check_url).ok:
            print 'photo {check_url} had error'.format(check_url=check_url)

        result = requests.get(url('users/me/photos/game'), headers=headers)
        result_json = result.json()
        if 'url' not in result_json:
            print 'ERROR url not in result'
            pprint.pprint(result_json)
            return
        else:
            check_url = result_json['url']
        if not requests.get(check_url).ok:
            print 'photo {check_url} had error'.format(check_url=check_url)

    return data


def add_test_extra_info():
    def do_fix(user):
        if user.is_test_user:
            if random.random() > .3:
                user.biography = LOREM_BIO
            if random.random() > .3:
                if user.first_name:
                    s = user.first_name
                else:
                    s = 'testsnapchat'
                user.snapchat = s + generate_random_string(3)
            if random.random() > .3:
                if user.user_name:
                    s = user.user_name
                else:
                    s = 'testinstagram'
                user.instagram = s + generate_random_string(3)
            print 'user updated'
        else:
            print 'not test user %s' % user.user_name

    from model import User
    filter_table(User, do_fix)

def audit_test_users():
    users_by_location = {}
    bios = []
    snapchats = []
    instagrams = []
    users = []
    test_users = []

    def audit(user):
        users.append(user)
        if not user.is_test_user:
            return
        test_users.append(user)
        try:
            location_users = users_by_location[user.location]
        except KeyError:
            location_users = []
            users_by_location[user.location] = location_users
        location_users.append(user)

        if user.biography:
            bios.append(user)
        if user.snapchat:
            snapchats.append(user)
        if user.instagram:
            instagrams.append(user)

    from model import User
    filter_table(User, audit, auto_save=False)

    print '--location audit--'
    from logic.location import LOCATIONS

#    for k, v in LOCATIONS.items():
#        users_by_location[k] = []

    from util import pluralize
    for location_uuid, location_users in users_by_location.items():
        try:
            location = LOCATIONS[location_uuid].city
        except KeyError:
            location = location_uuid
        print '{name} {users}'.format(
            name=location, users=pluralize(location_users, 'user'))
    print pluralize(users, 'user')
    print pluralize(test_users, 'test user')
    print pluralize(bios, 'biography', 'biographies')
    print pluralize(snapchats, 'snapchat')
    print pluralize(instagrams, 'instagram')

def _create_seed_dirs():
    import os
    from logic import location
    for location_uuid, location in location.LOCATIONS.items():
        for gender in ['m', 'f']:
            os.mkdir('./data/seed_data/%s/%s' % (gender, location_uuid.hex))

def _snarf_randos(count):
    from logic import location
    import os
    total = len(location.LOCATIONS) * count * 2
    for location_uuid, location in location.LOCATIONS.items():
        for gender in ['m', 'f']:
            path = './data/seed_data/%s/%s' % (gender, location.uuid.hex)
            count_needed = max(count - len(os.listdir(path)), 0)
            if count_needed == 0:
                total -= count
                print 'skipping %s, remaining %s' % (path, total)
            else:
                total -= (count - count_needed)
                print 'processing %s, remaining %s' % (path, total)
            for x in range(count_needed):
                total -= 1
                _generate_rando(gender, location)
                print '., remaining %s' % total

def _generate_rando(gender=None, user_location=None):
    """Adds data to /seed_users so it can be used to make test users.

    Probably never needs to be used again, if seed_users has data.
    """
    if gender is None:
        gender = random.choice(['m', 'f'])
    if gender == 'm':
        long_gender = 'male'
    elif gender == 'f':
        long_gender = 'female'
    else:
        print 'unknown gender %s, must be m or f' % gender
    if user_location is None:
        from logic.location import LOCATIONS
        user_location = random.choice(LOCATIONS.values())
    import os
    import os.path
    while True:
        result = requests.get(
            'https://randomuser.me/api/?gender={gender}&nat={nat}'.format(
                gender=long_gender, nat=user_location.country))
        user_data = result.json()
        user = user_data[u'results'][0][u'user']
        user_first_name = user[u'name'][u'first']
        user_last_name = user[u'name'][u'last']
        user_name = '{first} {last}'.format(first=user_first_name,
                                            last=user_last_name)
        path_name = user_name.replace(' ', '_')

        # If the user name is not unique, regenerate.
        path = './seed_users/%s' % path_name
        if not os.path.exists(path):
            break
        print 'path %s taken, trying again' % path
    os.mkdir(path)

    photo_url = user[u'picture'][u'large']

    # Get the image from randomuser.
    pic_path = '%s/pic' % path
    while True:
        r = requests.get(photo_url, stream=True)
        with open(pic_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk: # filter out keep-alive new chunks
                    f.write(chunk)
                    #f.flush() commented by recommendation from J.F.Sebastian
        if result.ok:
            break
        print 'download had status {status}, trying again'.format(status=result.status_code)

    # Make a dict of data.
    location_geo = "{lat};{lon};0.0 hdn=-1.0 spd=0.0".format(
            lat=user_location.lat, lon=user_location.lon)

    user_data = {
        'location': location_geo,
        'gender': long_gender,
        'user_name': user_name,
        'first_name': user_first_name,
        'last_name': user_last_name
    }
    if random.random() > .3:
        user_data['biography'] = LOREM_BIO
    if random.random() > .3:
        user_data['snapchat'] = user_first_name + generate_random_string(3)
    if random.random() > .3:
        user_data['instagram'] = user_last_name + generate_random_string(3)
    if random.random() > .3:
        user_data['website'] = 'http://example.com/%s/%s' % (user_last_name, generate_random_string(3))

    data_path = '%s/data' % path
    with open(data_path, 'w') as f:
        json.dump(user_data, f)

#
# #--------
#     while True:
#         result = requests.patch(url('users/me'),
#                                 json=user_data,
#                                 headers=headers)
#         if not result.ok:
#             print 'user info had status {status}'.format(status=result.status_code)
#             if result.status_code == 409:
#                 print 'user info status code 409, trying different name'
#                 pprint.pprint(result.json())
#                 rand = generate_random_string(5)
#                 user_first_name += rand
#                 user_last_name += rand
#                 user_name += rand
#                 user_data['user_name'] = user_name
#                 user_data['first_name'] = user_first_name
#         else:
#             break
#
#     # Get the image from randomuser that we'll upload.
#     local_filename = 'tempimage'
#     r = requests.get(photo_url, stream=True)
#     with open(local_filename, 'wb') as f:
#         for chunk in r.iter_content(chunk_size=1024):
#             if chunk: # filter out keep-alive new chunks
#                 f.write(chunk)
#                 #f.flush() commented by recommendation from J.F.Sebastian
#     if not result.ok:
#         print 'download had status {status}'.format(status=result.status_code)
#         return data
#
#     # Make sure location update completed
#     while True:
#         result = requests.get(url('users/me'), headers=headers)
#         if result.ok and result.json().get('location'):
#             break
#         else:
#             print 'location retry'
#             sleep(0.5)
#
#     # Get an upload URL for our image
#     while True:
#         photo_result = requests.post(url('photos'),
#                                      json={'set_as_profile_photo': True,
#                                            'enter_in_tournament': True},
#                                      headers=headers)
#         photo_data = photo_result.json()
#
#         try:
#             fields = photo_data[u'fields']
#         except KeyError:
#             print 'photo retry'
#             pass
#         else:
#             break
#
#     key = None
#     for field in fields:
#         if field[u'name'] == u'key':
#             key = field[u'value']
#     photo_uuid_hex = key[-32:]
#     photo_url = photo_data['action']
#     form_arg = ['-F %s=%s' % (x['name'], x['value']) for x in photo_data['fields']]
#     form_arg.append('-F file=@%s' % local_filename)
#     cmd = 'curl -i -L -X POST "%s" %s' % (photo_url, ' '.join(form_arg))
#     subprocess.call(cmd, shell=True)
#
#     if confirm:
#         from time import sleep
#         sleep(2)
#
#         result = requests.get(url('users/me'), headers=headers)
#         user_data = result.json()
#         if user_data['user_name'] != user_name:
#             print 'user name did not set, expected {expected} got {got}'.format(
#                 expected=user_name, got=user_data['user_name']
#             )
#         if user_data['first_name'] != user_first_name:
#             print 'first name did not set, expected {expected} got {got}'.format(
#                 expected=user_first_name, got=user_data['first_name']
#             )
#
#         result = requests.get(url('users/me/photos/small'), headers=headers)
#         result_json = result.json()
#         print result_json
#         if 'url' not in result_json:
#             print 'ERROR url not in result'
#             pprint.pprint(result_json)
#             return
#         else:
#             check_url = result_json['url']
#         if not requests.get(check_url).ok:
#             print 'photo {check_url} had error'.format(check_url=check_url)
#
#         result = requests.get(url('users/me/photos/medium'), headers=headers)
#         result_json = result.json()
#         if 'url' not in result_json:
#             print 'ERROR url not in result'
#             pprint.pprint(result_json)
#             return
#         else:
#             check_url = result_json['url']
#         if not requests.get(check_url).ok:
#             print 'photo {check_url} had error'.format(check_url=check_url)
#
#         result = requests.get(url('users/me/photos/game'), headers=headers)
#         result_json = result.json()
#         if 'url' not in result_json:
#             print 'ERROR url not in result'
#             pprint.pprint(result_json)
#             return
#         else:
#             check_url = result_json['url']
#         if not requests.get(check_url).ok:
#             print 'photo {check_url} had error'.format(check_url=check_url)
#
#     return data

def _confirm_randos(count):
    from logic import location
    import os
    total = len(location.LOCATIONS) * count * 2
    for location_uuid, location in location.LOCATIONS.items():
        for gender in ['m', 'f']:
            path = './data/seed_data/%s/%s' % (gender, location.uuid.hex)
            if not os.path.exists(path):
                print 'missing path %s' % path
            else:
                for subdir in os.listdir(path):
                    subdir = '%s/%s' % (path, subdir)
                    if not 2 == len(os.listdir(subdir)):
                        print 'wrong %s' % subdir

def make_test_users(resume_at=None, force_create=False):
    print('either add tags to this routine or use cli.add_tags')
    import os
    from logic.location import LOCATIONS
    count = 0
    from apps.api.api import top_leaderboards_male, top_leaderboards_female
    from itertools import chain, izip
    la_location = None
    priority_locations = []
    non_priority_locations = []
    priority_location_uuid_hexes = set([x[1] for x in chain(top_leaderboards_male, top_leaderboards_female)])
    for location_uuid, location in LOCATIONS.items():
        if location_uuid == LA_UUID:
            la_location = location
        elif location_uuid.hex in priority_location_uuid_hexes:
            priority_locations.append(location)
        else:
            non_priority_locations.append(location)
    locations = list(chain([la_location], priority_locations, non_priority_locations))

    def imerge(a, b):
        for i, j in izip(a, b):
            yield i
            yield j

    paths = list(imerge(['./data/seed_data/f/%s' % l.uuid.hex for l in locations],
                        ['./data/seed_data/m/%s' % l.uuid.hex for l in locations]))
    estimated = len(paths) * 20
    for path in paths:
        for user_name in os.listdir(path):
            if user_name.startswith('.'):  # .DS_Store I'm looking at you.
                continue
            if resume_at is None or count > resume_at:
                register_test_user('%s/%s' % (path, user_name),
                                   confirm=count%25==0,
                                   force_create=False)
            count += 1
            print ('estimated remaining %s %s %s' % (estimated, count, estimated - count))

def register_test_user(path, confirm=True, force_create=False):
    # gender must be 'male' or 'female'
    # location is the geo string that would be in the header of the post.
    # ex: "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"
    from time import sleep
    from logic.location import LOCATIONS
    dot, data, seed_data, gender, location_uuid_hex, user_name = path.split('/')
    location_uuid = uuid.UUID(location_uuid_hex)
    location = LOCATIONS[location_uuid]
    location_geo_meta = location._make_geo_meta()
    print 'creating user %s %s %s' % (user_name, gender, location.city)

    with open('%s/data' % path) as f:
        user_data = json.load(f)
    # user = user_data[u'results'][0][u'user']
    # user_first_name = user[u'name'][u'first']
    # user_last_name = user[u'name'][u'last']
    # user_name = '{first} {last}'.format(first=user_first_name,
    #                                     last=user_last_name)
    #photo_url = user[u'picture'][u'large']

    if not force_create:  # Don't make users with the same name, we assume
        # we already made them.
        try:
            user_name = user_data['user_name'].replace(' ', '%20')
            request = urllib2.Request(url('user_names/%s' % user_name))
            result = urllib2.urlopen(request)
            if result.getcode() == 204:
                print 'already is a user %s, skipping' % user_data['user_name']
                return
        except:
            pass

    for i in range(5):
        try:
            result = requests.post(url('users'),
                                   json={'is_test_user': 'true'},
                                   headers={'Geo-Position': location_geo_meta,
                                            'Content-Type': 'application/json'})
        except Exception as exp:
            print 'create user got exception {}'.format(exp)
        else:
            if not result.ok:
                code = result.status_code if result is not None else 'None'
                print 'creating user had status {status}'.format(status=code)
            else:
                break
        print '(sleeping 10 seconds)'
        sleep(10)
        print '(sleep done)'
    else:
        print 'could not create user, returning'
        return
    data = result.json()
    user_uuid_hex = data["uuid"]
    print 'user_uuid_hex {}'.format(user_uuid_hex)
    auth_token = data["token"]
    print 'token {}'.format(auth_token)
    headers = headers_with_auth(user_uuid_hex, auth_token)
    headers['Geo-Position'] = location_geo_meta
    print 'headers {}'.format(headers)

    # Make sure user creation completed
    for i in range(5):
        try:
            print url('users/me')
            print 'sending headers {}'.format(headers)
            result = requests.get(url('users/me'), headers=headers)
        except Exception as exp:
            print 'get users/me had exception'
            print exp
            result = None
        if result is not None and result.ok:
            break
        else:
            print 'user retry'
            print result
            sleep(0.5 * (i + 1))
    else:
        print 'Could not verify user creation completed, returning'
        return

    for i in range(5):
        try:
            result = requests.patch(url('users/me'),
                                    json=user_data,
                                    headers=headers)
        except Exception as exp:
            print 'patch users/me had exception'
            print exp
            result = None
        if result is None or not result.ok:
            code = result.status_code if result is not None else 'None'
            print 'user info had status {status}'.format(status=code)
            if code == 409:
                print 'user info status code 409, trying different name'
                pprint.pprint(result.json())
                rand = generate_random_string(5)
                user_data['user_name'] = user_data['user_name'] + rand
                user_data['first_name'] = user_data['first_name'] + rand
            sleep(0.5 * (i + 1))
        else:
            break
    else:
        print 'could not set user name, returning'
        return

    # Make sure location update completed
    for i in range(5):
        try:
            result = requests.get(url('users/me'), headers=headers)
        except:
            result = None
        if result is not None and result.ok and result.json().get('location'):
            break
        else:
            print 'location retry'
            sleep(0.5 * (i + 1))

    # Get the pic files
    photo_file_names = []
    for file_name in os.listdir(path):
        if file_name.startswith('pic') or file_name.statswith('ex'):
            photo_file_names.append(file_name)

    # Get an upload URL for our image
    for photo_file_name in photo_file_names:
        print 'photo_file_name %s' % photo_file_name
        data = {
            'set_as_profile_photo': True,
            'enter_in_tournament': True
        }
        if photo_file_name.startswith('ex'):
            data['media_type'] = 'movie'
        for i in range(5):
            try:
                photo_result = requests.post(url('photos'),
                                             json=data,
                                             headers=headers)
            except Exception as exp:
                print 'post photo had exception'
                print exp
                photo_result = None
            if photo_result is not None:
                if not photo_result.ok:
                    print 'photo photo result was not OK'
                    print photo_result
                    sleep(1.0 * (i + 1))
                else:
                    photo_data = photo_result.json()
                    try:
                        fields = photo_data[u'post_form_args'][u'fields']
                    except KeyError:
                        print 'photo result did not have all data'
                        print photo_data
                        sleep(1.0 * (i + 1))
                    else:
                        break
        else:
            print 'could not post photo, returning'
            return

        key = None
        for field in fields:
            if field[u'name'] == u'key':
                key = field[u'value']
                break
        photo_uuid_hex = key[-32:]
        photo_url = photo_data[u'post_form_args']['action']
        form_arg = ['-F %s=%s' % (x['name'], x['value']) for x in fields]
        local_filename = '%s/%s' % (path, photo_file_name)
        print 'uploading %s' % local_filename
        form_arg.append('-F file=@%s' % local_filename)
        cmd = 'curl -i -L -X POST "%s" %s' % (photo_url, ' '.join(form_arg))
        for i in range(5):
            try:
                subprocess.call(cmd, shell=True)
            except Exception as exp:
                print 'call curl for photo upload had exception'
                print exp
                sleep(1 * (i + 1))
            else:
                break

    if confirm:
        from time import sleep
        sleep(2)

        for i in range(5):
            result = None
            got = None
            try:
                result = requests.get(url('users/me'), headers=headers)
                got = result.json()
            except Exception as exp:
                print 'get users/me had exception'
                print exp
            if result is not None and result.ok:
                break
            else:
                sleep(1.0 + (i + 1))
        else:
            print 'could not get users/me, moving on'

        if user_data['user_name'] != got['user_name']:
            print 'user name did not set, expected {expected} got {got}'.format(
                expected=user_data['user_name'], got=got['user_name']
            )
        if user_data['first_name'] != got['first_name']:
            print 'first name did not set, expected {expected} got {got}'.format(
                expected=user_data['first_name'], got=got['first_name']
            )
        if 'photo' not in got:
            print 'photo not found in user data'

        result = requests.get(url('users/me/photos/small'), headers=headers)
        result_json = result.json()
        print result_json
        if 'url' not in result_json:
            print 'ERROR url not in result'
            pprint.pprint(result_json)
            return
        else:
            check_url = result_json['url']
        if not requests.get(check_url).ok:
            print 'photo {check_url} had error'.format(check_url=check_url)

        result = requests.get(url('users/me/photos/medium'), headers=headers)
        result_json = result.json()
        if 'url' not in result_json:
            print 'ERROR url not in result'
            pprint.pprint(result_json)
            return
        else:
            check_url = result_json['url']
        if not requests.get(check_url).ok:
            print 'photo {check_url} had error'.format(check_url=check_url)

        result = requests.get(url('users/me/photos/game'), headers=headers)
        result_json = result.json()
        if 'url' not in result_json:
            print 'ERROR url not in result'
            pprint.pprint(result_json)
            return
        else:
            check_url = result_json['url']
        if not requests.get(check_url).ok:
            print 'photo {check_url} had error'.format(check_url=check_url)

    return data

def fix_test_users(resume_at=None):
    import os
    from logic.location import LOCATIONS
    count = 0
    paths = ['./data/seed_data/m/%s' % x for x in os.listdir('./data/seed_data/m') if not x.startswith('.')] + ['./data/seed_data/f/%s' % x for x in os.listdir('./data/seed_data/f') if not x.startswith('.')]
    for path in paths:
        for user_name in os.listdir(path):
            if user_name.startswith('.'):
                continue
            data_path = '%s/%s/data' % (path, user_name)
            with open(data_path) as f:
                user_data = json.load(f)
            if user_data['gender'] == 'm':
                user_data['gender'] = 'male'
            elif user_data['gender'] == 'f':
                user_data['gender'] = 'female'
            else:
                print 'GENDER UNKNOWN, was %s' % user_data['gender']
            with open(data_path, 'w') as f:
                json.dump(user_data, f)
            count += 1

def test_api():
    """Calls API endpoints with test user auth header."""
    # to run:
    # python -c 'import cli;cli.test_api()'

    # TODO: change user data, upload picture, sub and unsub categories.
    # can upload picture from seed_users, or could even make data for
    # this test user.
    ok = """  ____  __ __
 / __ \/ //_/
/ /_/ / ,<
\____/_/|_|"""

    fail = """   ____     _ __
  / __/__ _(_) /
 / _// _ `/ / /
/_/  \_,_/_/_/"""

    results = []

    # Create a test user.
    request = urllib2.Request('http://%s/v0.2/users?is_test=True' % URL,
                              json.dumps({}),
                              headers={'Geo-Position': LOCATION_STRING,
                                       'Content-Type': 'application/json'})
    result = urllib2.urlopen(request)
    data = json.loads(result.read())
    user_uuid_hex = data["uuid"]
    token = data["token"]
    counts = {
        'test': 0,
        'fail': 0
    }

    from util import pad_center
    def test(url, post=None, expect_code=200, is_put=False, is_patch=False):
        method = 'GET'
        if post:
            if is_put:
                method = 'PUT'
            elif is_patch:
                method = 'PATCH'
            else:
                method = 'POST'
        counts['test'] += 1
        pad = 60
        print('-'*pad)
        print(pad_center('{} {}'.format(method, url), length=pad))
        print('-'*pad)
        full_url = 'http://%s/%s' % (URL, url)
        request = urllib2.Request(
            full_url,
            post,
            headers_with_auth(user_uuid_hex, token))
        if is_put:
            request.get_method = lambda: 'PUT'
        elif is_patch:
            request.get_method = lambda: 'PATCH'
        try:
            result = urllib2.urlopen(request)
        except urllib2.HTTPError as e:
            result = e
        print('{} {}'.format(url, result.code))
        if result.code == 204:
            data = None
        else:
            try:
                data = json.loads(result.read())
            except:
                data = None
        print(data)
        if result.code == expect_code:
            print(ok)
        else:
            print('expected {} got {}'.format(expect_code, result.code))
            print(fail)
            counts['fail'] += 1

        results.append(data)
        return data

    test('/v0.2/test')
    test('/v0.2/test_auth')
    test('/v0.2/config')
    # test('/test_500') Don't want to log this every time I test, it's a 500.
    # test('/test_notification')  When we enable SNS, this will test it.
    #test('/v0.1/snscallback')  # The snscallback, a nop at the moment.
    # POST /snscallback
    # POST /users
    # test('/users_by_facebook/sometestfacebookid') # if we get a test facebook id, put it here and test.
    # Note: the following test is failing because we are not including our
    # secret facebook token in the post data.
    #test('/v0.1/users_by_facebook/somenonexistenttestfacebookid',
    #     expect_code=404) # test the 404 feature.
    test('/v0.2/users/me')
    first_name = 'Testro_{}'.format(generate_random_string(5))
    last_name = 'Jones_{}'.format(generate_random_string(5))
    user_name = '{}_{}'.format(first_name, last_name)
    test('/v0.2/user_names/{}'.format(user_name), expect_code=404)
    data = {
        'first_name': first_name,
        'last_name': last_name,
        'user_name': user_name,
        'location': LOCATION_STRING,
        'gender': 'female',
        'view_gender': 'female'
    }
    data = test('/v0.2/users/me', json.dumps(data), is_patch=True)
    if data != {u'location_name': u'Los Angeles', u'location': u'67f22847ecf311e4a264c8e0eb16059b'}:
        print(data)
        print(fail)
        counts['fail'] += 1
    from time import sleep
    sleep(1.5)
    test('/v0.2/user_names/{}'.format(user_name), expect_code=204)
    # DELETE /users/me - Not going to test this at the moment.
    # test('/user_names/someusername')  # exists, 200
    test('/v0.2/user_names/someotherusernamethatdoesntexist12345',
         expect_code=404)  # does not exist, 404

    data = json.dumps({
        'set_as_profile_photo': True,
        'enter_in_tournament': True
    })
    photo_result = test('/v0.2/photos?is_test=True', data, expect_code=202)

    try:
        fields = photo_result[u'post_form_args'][u'fields']
    except KeyError:
        print 'photo had key error'
        pprint.pprint(photo_result)
        print(fail)
        fields = []
    key = None
    for field in fields:
        if field[u'name'] == u'key':
            key = field[u'value']
    photo_uuid_hex = key[-32:]
    photo_url = photo_result[u'post_form_args']['action']
    form_arg = ['-F %s=%s' % (x['name'], x['value']) for x in photo_result[u'post_form_args']['fields']]

    # copy the test photo
    from PIL import Image
    import uuid
    new_photo_filename = uuid.uuid1().hex
    s = (random.randrange(50, 3000), random.randrange(50, 3000))
    try:
        im = Image.open('data/testphoto')  # TODO: Random photo for dupe checker.
        im.thumbnail(s, Image.ANTIALIAS)
        # Note default quality is 75. Above 95 should be avoided.
        im.save(new_photo_filename, "JPEG", quality=80, optimize=True,
                progressive=True)
    except IOError as e:
        print(e)
        print('Cannot create thumbnail for %s', new_photo_filename)

    form_arg.append('-F file=@%s' % new_photo_filename)
    cmd = 'curl -i -L -X POST "%s" %s' % (photo_url, ' '.join(form_arg))
    print 'calling Curl'
    subprocess.call(cmd, shell=True)
    print 'Curl done'
    print ''
    os.remove(new_photo_filename)

    from time import sleep
    for i in range(10):
        sleep(2)
        result = test('/v0.2/users/me')
        if 'photo' in result:
            break
    else:
        print(fail)
        counts['fail'] += 1
        print("Photo does not appear in user info - is upload broken?")
        #return

    # PUT /users/me/facebook
    test('/v0.2/users/me/photos/small')
    test('/v0.2/users/me/photos/medium')
    test('/v0.2/users/me/photos/game')
    test('/v0.2/users/me/activity')
    # POST /photos
    # POST /photos/<photo_id>/comments
    # import category
    # photo_uuid = category.CATEGORY_COVER_PHOTO_UUIDS[category.CATEGORIES.keys()[0]]
    # test('/v0.1/photos/%s/comments' % photo_uuid.hex)
    # test('/v0.1/photos/%s/awards' % photo_uuid.hex)
    # test('/v0.1/users/me/matches')
    # # # PUT /users/me/matches/* - Not going to vote on matches with the tester.
    # test('/v0.1/users/me/matches/%s' % photo_uuid.hex)
    # # # PUT /users/me/tournaments/* - Not going to vote on a tournament. disabled.
    # test('/v0.1/categories')
    # test('/v0.1/leaderboards')
    # for c in category.CATEGORIES.keys():
    #     test('/v0.1/leaderboards/%s' % c)
    # test('/v0.1/users/me/wins')
    # test('/v0.1/users/me/following')
    # test('/v0.1/users/me/followers')
    # # test('/users/me/followers/<some user uuid>') # confirm follower, 200
    # # test('/users/me/followers/<some other user uuid>')  # confirm not follower, 404
    # test('/v0.1/users/me/feed')
    # test('/v0.1/users/me/notification_history')
    # test('/v0.1/users/me/notification_settings')
    # # #test('/users/me/notification_settings') # test PATCH
    # # #POST /photos/<photo_id>/flags
    # # #POST /photo_comments/<comment_id>/flags
    # # #POST /users/<user_id>/flags

    # Test movie upload
    data = json.dumps({
        'set_as_profile_photo': True,
        'enter_in_tournament': True,
        'media_type': 'movie'
    })
    photo_result = test('/v0.2/photos?is_test=True', data, expect_code=202)

    try:
        fields = photo_result[u'post_form_args'][u'fields']
    except KeyError:
        print 'photo had key error'
        pprint.pprint(photo_result)
        print(fail)
        fields = []
    key = None
    for field in fields:
        if field[u'name'] == u'key':
            key = field[u'value']
    photo_uuid_hex = key[-32:]
    photo_url = photo_result[u'post_form_args']['action']
    form_arg = ['-F %s=%s' % (x['name'], x['value']) for x in photo_result[u'post_form_args']['fields']]

    # copy the test photo
    from PIL import Image
    import uuid
    new_photo_filename = uuid.uuid1().hex
    import shutil
    shutil.copyfile('data/big_buck_bunny_720p_1mb.mp4', './{}'.format(new_photo_filename))

    form_arg.append('-F file=@%s' % new_photo_filename)
    cmd = 'curl -i -L -X POST "%s" %s' % (photo_url, ' '.join(form_arg))
    print 'calling Curl'
    subprocess.call(cmd, shell=True)
    print 'Curl done'
    print ''
    os.remove(new_photo_filename)

    from time import sleep
    for i in range(10):
        sleep(2)
        result = test('/v0.2/users/me')
        if 'photo' in result:
            break
    else:
        print(fail)
        counts['fail'] += 1
        print("Photo does not appear in user info - is upload broken?")
        #return

    # PUT /users/me/facebook
    test('/v0.2/users/me/photos/small')
    test('/v0.2/users/me/photos/medium')
    test('/v0.2/users/me/photos/game')

    print '------------------------'
    for r in results:
        print r
    from util import pluralize
    print('{} {}'.format(pluralize(counts['test'], 'test'),
                         pluralize(counts['fail'], 'fail')))
    if counts['fail'] == 0:
        print(ok)
        exit()
    else:
        print(fail)
        exit(1)

def test_score():
    """Fail if Leaderboards do not come in score-order."""
    fails = []
    from time import sleep
    def check_order(category, when):
        url = '/v0.1/leaderboards/{}?when={}'.format(category, when)
        result = open_auth(url)
        last = 100000
        fail = False
        for photo in result['photos']:
            if photo['score'] > last:
                print '{} > {}'.format(photo['score'], last)
                fail = True
            last = photo['score']
        if fail:
            name = '{} {}'.format(category, when)
            print 'FAIL - {} - scores out of order'.format(name)
            fails.append(name)
            for photo in result['photos']:
                print photo['score']

    categories = [u'Underwater', u'Sports', u'Pets', u'HairAndMakeup', u'Urban', u'FashionWomen', u'BlackAndWhite', u'Nature', u'Food', u'Automotive', u'Wedding', u'Wildlife', u'FitnessWomen', u'Interiors', u'Timelapse', u'FitnessMen', u'Lingerie', u'Architecture', u'Documentary', u'Bikini', u'FashionMen', u'Portrait', u'ExtremeSports', u'Landscape']
    whens = ['alltime', 'thishour', 'today', 'thisweek', 'thismonth', 'thisyear']
    for category in categories:
        for when in whens:
            check_order(category, when)
            sleep(5)

    for f in fails:
        print f
    return fails

def meta_test_score():
    f1 = test_score()
    f2 = test_score()
    f3 = test_score()

    if f1 != f2:
        print 'FAIL 1 does not equal 2'
    if f1 != f3:
        print 'FAIL 1 does not equal 3'
    if f2 != f3:
        print 'FAIL 2 does not equal 3'

def fix_leaderboard():
    import model
    from time import sleep

    whens = ['thismonth', 'thisyear']
    total = model.Photo.count()
    count = 0
    for photo in model.Photo.scan():
        count += 1
        print '{} of {}  {}'.format(count, total, photo.uuid.hex)
        for when in whens:
            if when == 'thismonth':
                cls = model.MonthLeaderboard
            elif when == 'thisyear':
                cls = model.YearLeaderboard
            else:
                raise ValueError
            item = cls(photo.category,
                       uuid=photo.uuid,
                       post_date=photo.post_date,
                       score=photo.score)
            item.save()
            sleep(1)

def test_score_2():
    # on last leaderboardtest I saw a Year, Wildlife with an out-of-order-
    # score of 1571. I need to run again because I can find no such record.
    # This will be my test, I'll get the leaderboard from the api, then I'll
    # compare to my own queries against dynamodb.
    api_leaderboard = open_auth('/v0.1/leaderboards/Wildlife?when=thisyear')['photos']
    import model
    for item in api_leaderboard:
        photo = model.get_one(model.Photo, 'uuid_index', uuid.UUID(item['id']))
        if int(photo.score) != item['score']:
            print '{} leaderboard score {} photo score {}'.format(item['id'], item['score'], int(photo.score))
        yl = model.YearLeaderboard.get('Wildlife', uuid.UUID(item['id']))
        if int(yl.score) != item['score']:
            print '{} leaderboard score {} yl score {}'.format(item['id'], item['score'], int(yl.score))

def send_ellis_push(message='test APN message'):
    from logic import sns
    import model
    import uuid
    ellis = model.User.get(uuid.UUID(ELLIS_UUID_HEX))
    if ellis is None:
        print 'could not find user, no push'
        return
    from logic.sns import send_push
    send_push(ellis, message)

def send_ellis_push_endpoint(message='test APN message'):
    open_auth('/v0.1/test_push', post=message, user_uuid_hex=ELLIS_UUID_HEX,
              auth_token=ELLIS_TOKEN)

def bucket_trim():
    """Delete every Key in the bucket that does not have a photo record."""
    from logic.s3 import get_serve_bucket
    from category import CATEGORIES
    bucket = get_serve_bucket()
    count = 0
    checked_keys = set()
    for key in bucket.list():
        name = key.name.encode('utf-8')
        name_ok = False
        try:
            first = name.split('_')[0]
        except:
            pass
        else:
            if first in checked_keys:
                continue
            checked_keys.add(first)
            if first == 'pop' or first in CATEGORIES:
                try:
                    uuid_hex = name.split('_')[1]
                    photo_uuid = uuid.UUID(uuid_hex)
                except:
                    pass
                else:
                    base = '{}_{}'.format(first, uuid_hex)
                    if not bucket.get_key(base+'_240x240'):
                        print 'xxx base 240'
                    if not bucket.get_key(base+'_480x480'):
                        print 'xxx base 480'
                    if not bucket.get_key(base+'_960x960'):
                        print 'xxx base 960'
                    if not bucket.get_key(base+'_original'):
                        print 'xxx base 960'
        #             name_ok = True
        #
        # if not name_ok:
        #     print 'bad key - {}'.format(name)
        #     key.delete()

def bucket_trim2():
    """Make an 'original' for any photo that does not have one."""
    from logic.s3 import get_serve_bucket
    from category import CATEGORIES
    bucket = get_serve_bucket()
    count = 0
    checked_keys = set()
    for key in bucket.list():
        name = key.name.encode('utf-8')
        name_ok = False
        try:
            first = name.split('_')[0]
        except:
            pass
        else:
            if first not in checked_keys:
                checked_keys.add(first)
                try:
                    uuid_hex = name.split('_')[1]
                    photo_uuid = uuid.UUID(uuid_hex)
                except:
                    pass
                else:
                    base = '{}_{}'.format(first, uuid_hex)
                    keys_ok = False
                    if bucket.get_key(base+'_240x240') and bucket.get_key(base+'_480x480') and bucket.get_key(base+'_960x960'):
                        keys_ok = True

                    if not keys_ok:
                        print 'missing keys for {}'.format(base)
                        if not bucket.get_key(base+'_240x240'):
                            print 'xxx base 240'
                        if not bucket.get_key(base+'_480x480'):
                            print 'xxx base 480'
                        if not bucket.get_key(base+'_960x960'):
                            print 'xxx base 960'
                    else:
                        key = bucket.get_key(base+'_960x960')
                        key.copy(bucket, base+'_original')

def add_tags(tags=[]):
    """Add the given tags to random photos for the LA Users."""
    path = './data/seed_data/f/{}'.format(LA_UUID.hex)
    from time import sleep
    for user_name in os.listdir(path):
        if user_name.startswith('.'):
            continue
        print(user_name)
        data_path = '%s/%s/data' % (path, user_name)
        with open(data_path) as f:
            user_data = json.load(f)
        api_user_name = user_data['user_name']
        import model
        from pynamodb.exceptions import DoesNotExist
        try:
            user_name = model.UserName.get(api_user_name)
        except DoesNotExist:
            print('could not load user by name {}'.format(api_user_name))
            continue
        try:
            user = model.User.get(user_name.user_uuid)
        except DoesNotExist:
            print('could not load user by uuid {}'.format(user_name.user_uuid.hex))
            continue

        photos_result = open_auth('v0.2/users/me/photos',
                                  user_uuid_hex=user.uuid.hex,
                                  auth_token=user.token)
        photo_ids = [x['id'] for x in photos_result]

        import random
        for tag in tags:
            for photo_uuid_hex in photo_ids:
                if random.random() > 0.722:
                    print(open_auth('v0.2/tags/{}/{}'.format(tag, photo_uuid_hex),
                                    is_put=True,
                                    user_uuid_hex=user.uuid.hex,
                                    auth_token=user.token))
                    print('')
                    sleep(1)

def setup_tags():
    """Call add_tags on the fixed tags."""
    from logic.tags import TOP_TAGS_FEMALE
    add_tags(TOP_TAGS_FEMALE)
