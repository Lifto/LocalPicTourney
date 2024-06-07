from __future__ import division, absolute_import, unicode_literals



import json
import os
import requests

from log import log
from model import FacebookLog
from ocean_exceptions import FacebookError
from logic.photo import create_photo, crop
from logic.user import update_registration_status
from settings import settings
from util import now

# Fields to get when getting a user's facebook data.
FB_FIELDS = ','.join(['id', 'about', 'age_range', 'bio', 'birthday',
             'context', 'currency', 'devices', 'education', 'email',
             'favorite_athletes', 'favorite_teams', 'first_name', 'gender',
             'hometown', 'inspirational_people', 'install_type', 'installed',
             'interested_in', 'is_shared_login', 'is_verified', 'languages',
             'last_name', 'link', 'location', 'locale', 'meeting_for',
             'middle_name', 'name', 'name_format',
             # we don't get this because it seems to be current exchange rates.
             # 'payment_pricepoints',
             'test_group', 'political', 'relationship_status', 'religion',
             'security_settings', 'significant_other', 'sports', 'quotes',
             'third_party_id', 'timezone',
             # we don't get this because it raises an error about the app type.
             # 'token_for_business',
             'updated_time', 'shared_login_upgrade_required_by', 'verified',
             'video_upload_limits', 'viewer_can_send_gift', 'website', 'work',
             'public_key', 'cover'])
FB_URL = "https://graph.facebook.com/v2.5/me?fields={fields}&access_token={{token}}".format(
    fields=FB_FIELDS)
#FB_PIC_INFO_URL = "https://graph.facebook.com/v2.4/me/picture?type=large&redirect=false&access_token={token}"
FB_PIC_INFO_URL = "https://graph.facebook.com/v2.5/me/picture?width=960&height=960&redirect=false&access_token={token}"
#FB_TEST_ID = 'facebook_test_id'
#FB_TEST_TOKEN = 'facebook_test_token'

def get_facebook_data(user, set_as_profile_photo):
    # Get the user's facebook data.
    # If there is a problem set the token to None and update registration
    # status accordingly.
    # TODO: Can these be batched? Facebook graph api has batching.

    try:
        data = _get(FB_URL, user)
    except:
        log.error("get_facebook_data data _get had error, aborting.")
        return

    # Add the data to our log.
    fb_log = FacebookLog(user.uuid, now(), data=json.dumps(data))
    fb_log.save()

    # Decode data to update gender.
    data = data
    gender = data.get('gender')
    log.info('got "%s" in gender in data' % gender)
    user.facebook_gender = gender
    facebook_id = data.get('id')
    log.info('got "%s" in id in data' % facebook_id)
    user.facebook_id = facebook_id
    first_name = data.get('first_name')
    log.info('got "%s" in first_name in data' % first_name)
    last_name = data.get('last_name')
    log.info('got "%s" in last_name in data' % last_name)

    if user.show_gender_male is None and gender is not None:
        # It could be custom, we only take male and female.
        if gender == 'male':
            user.show_gender_male = True
        elif gender == 'female':
            user.show_gender_male = False
    if user.first_name is None and first_name is not None:
        user.first_name = first_name
    if user.last_name is None and last_name is not None:
        user.last_name = last_name
    user.save()

    if set_as_profile_photo:
        # Get pic info to be sure the pic is not the silhouette.

        try:
            data = _get(FB_PIC_INFO_URL, user)
        except:
            log.error("get_facebook_data photo _get had error, aborting.")
            return

        # {u'data':
        #     {u'url': u'https://fbcdn-profile-a.akamaihd.net/<...>',
        #      u'is_silhouette': False}}
        data = data.get(u'data', {})
        is_silhouette = data.get(u'is_silhouette')
        pic_url = data.get(u'url')
        if not is_silhouette and pic_url:
            # user, location, geo, set_as_profile_photo, enter_in_tournament
            photo = create_photo(user, None, None, True, False)
            key_name = photo.file_name

            # In testing we don't actually do this.
            # TODO: Fix working paths so we can manipulate photos in testing.
            if settings.FACEBOOK_ENABLED:
                # Copy the FB photo to local storage.
                photo_path = '%s%s' % (settings.PHOTO_DIR, key_name)
                log.info("{url} to copy to {photo_path}".format(
                    url=pic_url, photo_path=photo_path))
                log.info('calling requests get')
                try:
                    r = requests.get(pic_url, stream=True)
                    with open(photo_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024):
                            if chunk: # filter out keep-alive new chunks
                                f.write(chunk)
                except Exception as e:
                    log.error(e)
                    log.exception(e)
                    raise

                # Do the cropping same as we would with something the user posted to s3
                log.info("Worker copied %s to %s", key_name, photo_path)
                crop(photo_path, key_name, 240)
                crop(photo_path, key_name, 480)
                crop(photo_path, key_name, 960)
                log.info("Crop complete")
                os.remove(photo_path)
                log.info("Working photo removed")
            else:
                log.info('IS_LOCAL_DEV == True so not copying facebook photo')
            # Update the Photo to indicate the thumbnail is copied and available.
            log.info("photo crop done, marking copy complete.")
            photo.copy_complete = True
            photo.save()
            user.photo = photo.uuid
            user.save()

def get_facebook_id(facebook_auth_token):
    if not settings.FACEBOOK_ENABLED:
        return TEST_IDS_BY_TOKEN.get(facebook_auth_token)
    url = "https://graph.facebook.com/v2.5/me?fields=id&access_token={token}"
    url = url.format(token=facebook_auth_token)
    try:
        result = requests.get(url)
        result.raise_for_status()
    except requests.exceptions.RequestException as error:
        log.info("get_facebook_id had RequestException")
        log.exception(error)
        message = ''
        try:
            message = error.response.text
        except Exception as text_exception:
            log.info("could not get error.response.text {}".format(text_exception))
        msg = "Graph Api URL {url} had error: {err}, message: {message}".format(
            url=url, err=error, message=message)
        log.error(msg)
        raise FacebookError(msg)
    except Exception as error:
        log.info("get_facebook_id had an Exception")
        log.exception(error)
        msg = "Graph Api URL {url} had error: {err}".format(url=url, err=error)
        log.error(msg)
        raise FacebookError(msg)
    return result.json().get('id')


def add_test_data(facebook_id, facebook_api_token):
    if facebook_id is None or facebook_api_token is None:
        return
    TEST_IDS_BY_TOKEN[facebook_api_token] = facebook_id
    TEST_DATA[FB_URL][facebook_api_token] = json.dumps({
        u'age_range': {u'min': 21},
        u'context': {u'id': u'dXNlcl9jb250ZAXh0OgGQnZCtVMck52b1wgZC8yku4Lff2Fz9Ia1HWsh5n15rZC4DGeYYUND9AS2602ehrEe5aQRWh1Ync1cd5CHirbeZBuGXoDxZAwTMqp2FJTfjnSXyZAqGwZD',
                     u'mutual_friends': {u'data': [],
                                         u'summary': {u'total_count': 0}},
                     u'mutual_likes': {u'data': [], u'summary': {u'total_count': 0}}},
         u'cover': {u'id': u'125991351082168',
                    u'offset_y': 50,
                    u'source': u'https://scontent.xx.fbcdn.net/hphotos-xpf1/t31.0-8/s720x720/11882953_125991351082168_2200205718079379228_o.jpg'},
         u'currency': {u'currency_offset': 100,
                       u'usd_exchange': 1,
                       u'usd_exchange_inverse': 1,
                       u'user_currency': u'USD'},
         u'first_name': u'Will',
        # Note: If gender is 'custom' then the field is absent.
         u'gender': u'female',
         u'id': facebook_id,
         u'install_type': u'UNKNOWN',
         u'installed': True,
         u'is_shared_login': False,
         u'is_verified': False,
         u'last_name': u'Romanescu',
         u'link': u'https://www.facebook.com/app_scoped_user_id/125980107749959/',
         u'locale': u'en_US',
         u'middle_name': u'Alajaciaajacb',
         u'name': u'Will Alajaciaajacb Romanescu',
         u'name_format': u'{first} {last}',
         u'security_settings': {u'secure_browsing': {u'enabled': True}},
         u'test_group': 8,
         u'third_party_id': u'd8vePvAFIQtj8cLoiitT84E8jv8',
         u'timezone': 0,
         u'updated_time': u'2015-08-28T17:47:21+0000',
         u'verified': False,
         u'video_upload_limits': {u'length': 2700, u'size': 1879048192},
         u'viewer_can_send_gift': False
    })

    TEST_DATA[FB_PIC_INFO_URL][facebook_api_token] = json.dumps({
        u'data': {
            u'url': u'https://fbcdn-profile-a.akamaihd.net/hprofile-ak-xfp1/v/t1.0-1/p50x50/11933441_125991241082179_6796375334028633243_n.jpg?oh=43a0a39553ecf1f267319d25506bd864&oe=56804929&__gda__=1449914191_30f0474003473430c254dc0449b24a58',
            u'is_silhouette': False}
    })

TEST_DATA = {
    FB_URL: {},
    FB_PIC_INFO_URL: {}
}

TEST_IDS_BY_TOKEN = {}

def _get(url, user):
    """Get a URL requiring .format(token=user.token). Test safe."""
    if user.facebook_api_token is None:
        raise ValueError("cannot _get '{url}' because User({user_uuid}).facebook_api_token is None".format(
                url=url, user_uuid=user.uuid.hex))
    if not settings.FACEBOOK_ENABLED:
        log.info("settings.FACEBOOK_ENABLED=False, get_facebook_data is mock")
        data = json.loads(TEST_DATA[url][user.facebook_api_token])
    else:
        # Get data from facebook api.
        url = url.format(token=user.facebook_api_token)
        try:
            result = requests.get(url)
            result.raise_for_status()
        except requests.exceptions.RequestException as error:
            log.exception(error)
            message = ''
            try:
                message = error.response.text
            except Exception as text_exception:
                log.info("could not get error.response.text {}".format(text_exception))
            msg = "Graph Api URL {url} had error: {err}, setting user.facebook_api_token to None, message: {message}".format(
                url=url, err=error, message=message)
            log.error(msg)
            user.facebook_api_token = None
            update_registration_status(user)
            user.last_facebook_event_date = now()
            user.last_facebook_event = msg
            user.save()
            raise
            # the results look like this:
            # {"error":{"message":"Error validating access token: Session has expired on Thursday, 25-Feb-16 02:00:00 PST. The current time is Tuesday, 08-Mar-16 08:05:29 PST.","type":"OAuthException","code":190,"error_subcode":463,"fbtrace_id":"FJ7Pp3q7D0R"}}
        except Exception as error:
            log.exception(error)
            msg = "Graph Api URL {url} had error: {err}, setting user.facebook_api_token to None".format(
                url=url, err=error)
            log.error(msg)
            user.last_facebook_event_date = now()
            user.last_facebook_event = msg
            user.facebook_api_token = None
            update_registration_status(user)
            user.save()
            raise
        else:
            user.last_facebook_event_date = now()
            user.last_facebook_event = 'Facebook access OK'
            user.save()
        data = result.json()
    return data
