from __future__ import division, absolute_import, unicode_literals


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

from base64 import b64decode
import json
import uuid

from flask import abort, Flask, request
from pynamodb.exceptions import DoesNotExist

from log import log
from logic import s3
from logic import search
from logic import tags
from logic.awards import leaderboard_awards
from logic.facebook import get_facebook_data
from logic.feed import feed_new_photo
from logic.photo import crop, file_hash, get_photo, preserve
from logic.score import trim_hour_leaderboards, trim_today_leaderboards, \
    trim_week_leaderboards, trim_month_leaderboards, trim_year_leaderboards
from logic.sentry import sentryDecorator
from logic.sns import push_new_photo
from logic.stats import incr, timingIncrDecorator
from logic.user import get_user, update_registration_status
import model
from settings import settings
from util import generate_random_string, now, took

# -- Logging ------------------------------------------------------------------

log.info("Starting LocalPicTourney SQS Worker")
if not settings.IS_WORKER:
    log.warn("Running Worker with settings.IS_WORKER=False")

class PhotoNotFound(Exception):
    pass


# -- Application Init ---------------------------------------------------------

application = Flask(__name__)

@application.route('/health_check', methods=['GET'])
@sentryDecorator()
@timingIncrDecorator('worker_health_check', track_status=False)
def health_check():
    # TODO: Check the connections to the services.
    return ''

@application.route('/worker_callback', methods=['POST'])
@sentryDecorator()
def worker_callback():
    try:
        request_hash = request.data.__hash__()
    except:
        request_hash = '--hash_unknown-'
    data = {}
    try:
        # Called when S3 has finished the upload of the Photo.
        # This is a local-only EB Worker tier function.
        log.debug("Worker processing message for worker_callback {hash}".format(
                     hash=request_hash))
        # request.data looks like this:
        # {'Records': [{'awsRegion': 'us-west-2',
        #           'eventName': 'ObjectCreated:Post',
        #           'eventSource': 'aws:s3',
        #           'eventTime': '2015-02-09T19:27:54.912Z',
        #           'eventVersion': '2.0',
        #           'requestParameters': {'sourceIPAddress': '71.254.65.74'},
        #           'responseElements': {'x-amz-id-2': 'K2vI/PfJWPnLnrxp2fYzVpk2ZlOmU5LBcg+2WbvXHKlnhKJZsiJxKRlz4jf85D3E',
        #                                'x-amz-request-id': 'F0C539AAE5B9A49D'},
        #           's3': {'bucket': {'arn': 'arn:aws:s3:::localpictourney-inbox',
        #                             'name': 'localpictourney-inbox',
        #                             'ownerIdentity': {'principalId': 'A3UCIRG5KSHMKI'}},
        #                  'configurationId': 'Upload',
        #                  'object': {'eTag': '1a7f86b3e0650c3c09d7990fefdda44f',
        #                             'key': 'bd4c6220b09111e4961302fc1f809594',
        #                             'size': 8634},
        #                  's3SchemaVersion': '1.0'},
        #           'userIdentity': {'principalId': 'AWS:AROAIDVTFQBCHQ5PUVFNQ:i-e9d3e4e3'}}]}
        try:
            data = json.loads(b64decode(request.data))
            log.debug("--worker decoded b64 message")
        except:
            try:
                data = json.loads(request.data)
                log.debug("--worker decoded non b64 message")
            except:
                log.debug("--worker could not decode message")
                log.debug(request)
                try:
                    log.debug(request.data)
                except:
                    log.debug("--worker could not access request.data")
                pass
        if 'Records' in data:
            log.debug('Worker calling handle_photo_upload')
            return handle_photo_upload(data)
        elif data.get('t') == 'facebook_registration':
            log.debug('Worker calling handle_facebook_registration')
            return handle_facebook_registration(data)
        # s3 sent this when I subscribed an event (?)
        # { u'HostId': u'2kCAZHZNV+eo0TKycXNXtyx96ucGY/3q3iUozK0l1uOerowugZ2iiSYJkFp6bPjN',
        #   u'Service': u'Amazon S3',
        #   u'Bucket': u'local_pic_tourney-inbox',
        #   u'RequestId': u'F58C7007914E4B92',
        #   u'Time': u'2016-04-07T19:24:14.468Z',
        #   u'Event': u's3:TestEvent'
        # }
        elif data.get(u'Event') == u's3:TestEvent':
            log.info('s3:TestEvent')
            return ''
        else:
            log.error('unknown SQS payload')
            log.error(data)
            return abort(404)
    except Exception as e:
        log.error("worker had exception {e} for request {hash}".format(
                     e=e, hash=request_hash))
        log.error(data)
        log.exception(e)
        # Statsd
        incr('WorkerException.{name}-incr'.format(name=type(e).__name__))
        raise

@timingIncrDecorator('worker_handle_photo_upload', track_status=False)
def handle_photo_upload(payload):
    log.info("worker handle_photo_upload")
    # In working on getting this to be retry-safe, we want to use batch
    # writes. PynamoDB doesn't do batch writes for multiple tables as far
    # as I can tell.
    # "The BatchWriteItem operation puts or deletes multiple items in one or
    # more tables." -- DynamoDB
    # The result will included notification of failures (likely provision
    # errors)

    # ------- Note how many of these could fail with a provision error, and
    # consider the state of the system at that time, or at the time of
    # any of these partial writes even without an error. So, even if we had
    # perfect provision recovery (which we could have, more-or-less), we
    # would still have potential inconsistencies.

    key_name = payload['Records'][0]['s3']['object']['key']
    photo_uuid = uuid.UUID(key_name[-32:])
    # ------- This could fail with a provision error.
    # we would just retry from the top. OK
    photo = get_photo(photo_uuid, check_copy_complete=False)
    if not photo:
        log.error("handle_photo_upload could not find %s" % photo_uuid)
        raise PhotoNotFound

    if photo.uploaded:
        log.debug("this is a retry, photo {photo} was already uploaded".format(
                     photo=photo.uuid.hex))

    if not photo.copy_complete:  # If a retry, did not make it past this step.
        # Copy to local.
        photo_path = '%s%s' % (settings.PHOTO_DIR, key_name)
        if os.path.exists(photo_path):
            log.debug("photo {photo} was already in local storage {path}".format(
                         photo=photo.uuid.hex, path=photo_path))
        else:
            key = s3.get_incoming_bucket().get_key(key_name)
            try:
                key.get_contents_to_filename(photo_path)
            except Exception as e:
                # Attempt to clean up local file if there is an error.
                log.error("get_contents_to_filename({path}) failed, deleting".format(
                             path=photo_path))
                log.exception(e)
                try:
                    os.remove(photo_path)
                except Exception:
                    pass
                raise
        if not photo.uploaded:
            photo.uploaded = True
            # ------- This could fail with a provision error.
            # If so the checks above would not re-copy, we'd end up here again.
            photo.save()
        log.debug("Worker has photo %s in %s", key_name, photo_path)

        # This could be a re-try. is_duplicate is None if this has not been
        # checked.
        log.info("photo.is_duplicate == %s" % photo.is_duplicate)
        # Profile Photo is False, but still needs a copy.
        if photo.is_duplicate is None or photo.file_name.startswith('pop'):
            log.info('begin photo crop')
            if settings.PHOTO_CROP_ENABLED:
                hash = file_hash(photo_path)
            else:
                hash = generate_random_string(32)
            if photo.is_test == False and \
               model.get_one(model.Photo, 'dupe_hash_index', hash,
                             consistent_read=False):
                # There is a photo with this hash in the system already.
                photo.is_duplicate = True
                log.info("photo is duplicate, not cropping")
            else:
                photo.dupe_hash = hash
                photo.is_duplicate = False

                if photo.media_type is None:
                    log.warn('photo media type was None, setting to photo')
                    photo.media_type = 'photo'
                    photo.save()

                if photo.media_type == 'movie':
                    # Get the first frame from the movie to use as a photo.
                    log.info('processing movie {}'.format(photo_path))
                    from moviepy.video.io.VideoFileClip import VideoFileClip
                    clip = VideoFileClip(photo_path)
                    cover_path = '{}_cover.jpg'.format(photo_path)
                    # Note: Needs the .jpg or save_frame won't know what to do.
                    clip.save_frame(cover_path)
                    log.info('saved video cover to {}'.format(cover_path))
                    crop(cover_path, key_name, 240)
                    crop(cover_path, key_name, 480)
                    crop(cover_path, key_name, 960)
                    preserve(photo_path, key_name, 'original')
                    log.info('preserved {} {}'.format(photo_path, key_name))
                    log.info("removing working photo")
                    if settings.PHOTO_CROP_ENABLED:
                        os.remove(cover_path)
                        os.remove(photo_path)
                    else:
                        # TODO: write locally runnable testable photo operations
                        log.info(
                            "skipping source photo remove because PHOTO_CROP_ENABLED=False")

                else: # media_type == 'photo'
                    log.info('cropping photo 240, 480 and 960 {}'.format(photo_path))
                    crop(photo_path, key_name, 240)
                    crop(photo_path, key_name, 480)
                    crop(photo_path, key_name, 960)
                    preserve(photo_path, key_name, 'original')
                    log.info("removing working photo")
                    if settings.PHOTO_CROP_ENABLED:
                        os.remove(photo_path)
                    else:
                        # TODO: write locally runnable testable photo operations
                        log.info(
                            "skipping source photo remove because PHOTO_CROP_ENABLED=False")

            photo.save()

        # Update the Photo to indicate the thumbnail is copied and available.
        if photo.is_duplicate == False:
            # TODO: We are using copy_complete = False to imply the photo should
            # not be included in judging. However it also implies it might be
            # an incomplete operation. Perhaps we need an is_ready=True flag?
            photo.copy_complete = True
            # ------- This could fail with a provision error.
            # If this failed it will re-copy the photo from s3, re-crop it,
            # re-remove it. I guess that's OK and not really avoidable.
            # We could put a check in for photo.uploaded... but we might
            # be better off checking the photo in storage?
            # This is OK. Provision errors are rare, and this is not bad.
            photo.save()

    # ------- This could fail with a provision error.
    user = get_user(photo.user_uuid)
    if photo.file_name.startswith('pop') or photo.set_as_profile_photo:
        user.photo = photo.uuid
        # ------- This could fail with a provision error.
        # Retries here could cause a profile-pic setting race if they post
        # another profile pic right after.
        # Not the end of the world.
        user.save()

    # no problem, retry would fix. Idempotent.
    update_registration_status(user)

    # Add the photo to the feed.
    # ------- This could fail with a provision error.
    # This could make for lots of issues. If this retries, people will get
    # multiple feeds of the item. Can we make the feed's hash be based on
    # the info?
    # Hmmm... feed items are keyed on their owner for hash and range is
    # the created-on date, which would not get re-produced. so, if this
    # re-tried we'd get a set of duplicates in the feed.
    # - I think the answer is that a dupe-check needs to happen on the read-
    # back and then the read-backer can do deletes.
    feed_new_photo(photo.user_uuid, photo.uuid)
    push_new_photo(photo.user_uuid, photo.user_uuid, photo.uuid)
    for tag in photo.get_tags():  # case-preserving tag.
        # see tags.add_tag
        gender = 'f'  # TODO Gender hack - TODO use posting user's gender.
        gender_tag = '{}_{}'.format(gender, tag.lower())
        photo_gender_tag = model.PhotoGenderTag(gender_tag,
                                                uuid=photo_uuid,
                                                score=photo.score,
                                                tag_with_case=tag)
        photo_gender_tag.save()
        tags.log_tag_for_trending(gender, tag)
        search.add_tag(gender, tag)

    log.info("photo process complete")
    return ''

@timingIncrDecorator('worker_handle_facebook_registration', track_status=False)
def handle_facebook_registration(payload):
    log.info("worker handle_facebook_registration")
    log.info(payload)
    log.info(payload['user_uuid'])
    user_uuid = uuid.UUID(payload['user_uuid'])
    force_set_as_profile_photo = payload['force_set_as_profile_photo']
    try:
        user = model.User.get(user_uuid, consistent_read=False)
    except DoesNotExist:
        user = model.User.get(user_uuid)

    set_as_profile_photo = not user.photo or force_set_as_profile_photo
    get_facebook_data(user, set_as_profile_photo)

    update_registration_status(user)
    log.info("user registration updated to '%s'" % user.registration)

    return ''

@application.route('/worker_score_callback', methods=['POST'])
@sentryDecorator()
@timingIncrDecorator('worker_score_callback', track_status=False)
def score_callback():
    log.info("Worker processing message for score_callback")
    from logic.score import do_scores
    do_scores()

    return ''

@application.route('/worker_tag_trends_callback', methods=['POST'])
@sentryDecorator()
@timingIncrDecorator('worker_tag_trends_callback', track_status=False)
def tag_trends_callback():
    log.info("Worker processing message for tag_trends_callback")
    from logic.tags import do_tag_trends
    do_tag_trends()

    return ''

@application.route('/worker_filtered_leaderboard_callback', methods=['POST'])
@sentryDecorator()
@timingIncrDecorator('worker_filtered_leaderboard_callback', track_status=False)
def filtered_leaderboard_callback():
    # See cron.yaml, this is called once per hour on the hour.
    log.info("Worker processing message for filtered_leaderboard_callback")
    start_time = now()

    trim_hour_leaderboards()
    trim_today_leaderboards()
    trim_week_leaderboards()
    trim_month_leaderboards()
    trim_year_leaderboards()

    log.info("leaderboard trim done, %s" % took(start_time))
    return ''

@application.route('/worker_awards_callback', methods=['POST'])
@sentryDecorator()
@timingIncrDecorator('worker_awards_callback', track_status=False)
def filtered_awards_callback():
    log.info("Worker processing message for awards_callback")
    start_time = now()

    leaderboard_awards()

    log.info("awards done, %s" % took(start_time))
    return ''

