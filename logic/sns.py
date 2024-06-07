from __future__ import division, absolute_import, unicode_literals


import json

import boto
import boto.exception
import boto.sns
from pynamodb.exceptions import DoesNotExist
import re

from log import log
from logic.feed import get_feed_count
import model
from settings import settings

result_re = re.compile(r'Endpoint(.*)already', re.IGNORECASE)

SNS_CONNECTION = None

def setup_sns_connection():
    global SNS_CONNECTION
    if settings.SNS_ENABLED:
        try:
            region = [r for r in boto.sns.regions() if r.name==u'us-west-2'][0]
            SNS_CONNECTION = boto.sns.SNSConnection(region=region)
        except Exception as e:
            log.error(e)
            log.exception(e)
    else:
        log.info("Using mock SNS")
        SNS_CONNECTION = "mock_sns_connection"

def get_sns_connection():
    if SNS_CONNECTION is None:
        setup_sns_connection()
    return SNS_CONNECTION

def _send_push_new_photo(follower, user_name, photo_owner_uuid, photo_uuid):
    if user_name:
        msg = '{} put a new photo on LocalPicTourney, vote on it!'.format(user_name)
    else:
        msg = 'A user you follow put a new photo on LocalPicTourney, vote on it!'
    supporting_data = {
        'user_uuid': photo_owner_uuid.hex,
        'photo_uuid': photo_uuid.hex
    }
    send_push(follower, msg, supporting_data=supporting_data)

def push_new_photo(followed_uuid, photo_owner_uuid, photo_uuid):
    try:
        followed = model.User.get(followed_uuid, consistent_read=False)
    except DoesNotExist:
        user_name = None
    else:
        if followed.user_name:
            user_name = followed.user_name
        else:
            user_name = None

    followers = model.Follower.query(followed_uuid, consistent_read=False)
    for f in followers:
        if f.follower != followed_uuid:
            try:
                follower = model.User.get(f.follower, consistent_read=False)
            except DoesNotExist:
                continue
            if follower.notify_new_photo:
                _send_push_new_photo(follower, user_name,
                                     photo_owner_uuid, photo_uuid)

def _send_push_new_comment(comment_owner, user_name, commenter_uuid,
                           photo_uuid, comment_uuid):
    msg = '{} commented on your photo, see it!'.format(user_name)
    supporting_data = {
        'commenter_uuid': commenter_uuid.hex,
        'photo_uuid': photo_uuid.hex,
        'comment_uuid': comment_uuid.hex
    }
    send_push(comment_owner, msg, supporting_data=supporting_data)

def push_new_comment(comment_owner_uuid, commenter_uuid, photo_uuid,
                     comment_uuid):
    try:
        comment_owner = model.User.get(comment_owner_uuid, consistent_read=False)
    except DoesNotExist:
        return

    if not comment_owner.notify_new_comment:
        return

    try:
        commenter = model.User.get(commenter_uuid, consistent_read=False)
    except DoesNotExist:
        user_name = 'A user'
    else:
        if commenter.user_name:
            user_name = commenter.user_name
        else:
            user_name = 'A user'
    _send_push_new_comment(comment_owner, user_name, commenter_uuid,
                           photo_uuid, comment_uuid)

def _send_push_new_follower(followed, user_name, follower_uuid):
    msg = '{} is following you on LocalPicTourney'.format(user_name)
    supporting_data = {
        'follower_uuid': follower_uuid.hex
    }
    send_push(followed, msg, supporting_data=supporting_data)

def push_new_follower(followed_uuid=None, follower_uuid=None):
    if not followed_uuid or follower_uuid:
        return

    try:
        followed = model.User.get(followed_uuid, consistent_read=False)
    except DoesNotExist:
        return

    if not followed.notify_new_follower:
        return

    try:
        follower = model.User.get(follower_uuid, consistent_read=False)
    except DoesNotExist:
        user_name = 'A user'
    else:
        if follower.user_name:
            user_name = follower.user_name
        else:
            user_name = 'A user'

    _send_push_new_follower(followed, user_name, follower_uuid=follower.uuid)


def send_push(user, message, supporting_data=None):
    if not settings.SNS_ENABLED:
        log.debug('send_push SNS_ENABLED=False, not sending push')
        return

    if not user.apn_device_id:
        log.debug('send_push no user.apn_device_id, not sending push')
        return

    feed_count = get_feed_count(user.uuid)

    _send_apns_push(user.apn_device_id, message, badge_number=feed_count,
                    supporting_data=supporting_data)


def _send_apns_push(apn_device_id, message, badge_number=0,
                    supporting_data=None):
    apns_dict = {
        'aps': {
            'alert': message,
            'badge': badge_number,
            'content-available': 1
        }
    }
    if supporting_data:
        apns_dict.update(supporting_data)
    apns_string = json.dumps(apns_dict, ensure_ascii=False)
    message = {'default': 'local_pic_tourney default message', 'APNS_SANDBOX': apns_string}
    message_json = json.dumps(message, ensure_ascii=False)

    try:
        endpoint_response = get_sns_connection().create_platform_endpoint(
            platform_application_arn=settings.SNS_APPLICATION_ARN,
            token=apn_device_id,
        )
        endpoint_arn = endpoint_response['CreatePlatformEndpointResponse']['CreatePlatformEndpointResult']['EndpointArn']
    except boto.exception.BotoServerError as err:
        # Yes, this is actually the official way:
        # http://stackoverflow.com/questions/22227262/aws-boto-sns-get-endpoint-arn-by-device-token
        result = result_re.search(err.message)
        if result:
            endpoint_arn = result.group(0).replace('Endpoint ','').replace(' already','')
        else:
            raise

    log.info("Sending SNS push to Endpoint ARN:%s", endpoint_arn)

    connection = get_sns_connection()
    try:
        publish_result = connection.publish(
            target_arn=endpoint_arn,
            message=message_json,
            message_structure='json'
        )
    except boto.exception.BotoServerError as err:
        log.warn("sns publish raised error, continuing")
        log.exception(err)
    else:
        log.info(publish_result)
