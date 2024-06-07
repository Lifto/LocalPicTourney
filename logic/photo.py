from __future__ import division, absolute_import, unicode_literals



from uuid import uuid1

from boto.s3.key import Key
from PIL import Image
from pynamodb.models import DoesNotExist

from log import log
from model import get_one, HourLeaderboard, MonthLeaderboard, Photo, \
    ProfileOnlyPhoto, TodayLeaderboard, WeekLeaderboard, YearLeaderboard
from logic.s3 import get_serve_bucket
from settings import settings
from util import now


def create_photo(user, location, geo, set_as_profile_photo,
                 enter_in_tournament, tags=None, is_test=False,
                 media_type='photo'):
    """Create a Photo record for user and set user's photo_file_name."""
    post_date = now()
    photo_uuid = uuid1()

    if enter_in_tournament:
        # gender_location = Photo.make_gender_location(user.show_gender_male,
        #                                              location.uuid.hex)
        # This is a hack by request of @mcgraw, all users post pix as female
        gender_location = Photo.make_gender_location(False,
                                                     location.uuid.hex)

        kwargs = {
            'uuid': photo_uuid,
            'location': location.uuid,
            'file_name': '%s_%s' % (gender_location, photo_uuid.hex),
            'post_date': post_date,
            'user_uuid': user.uuid,
            'lat': geo.lat,
            'lon': geo.lon,
            'geodata': geo.meta,
            'set_as_profile_photo': set_as_profile_photo,
            'tags': tags,
            'media_type': media_type
        }
        if is_test == True:
            kwargs['is_test'] = True
        photo = Photo(gender_location, **kwargs)
    else:
        kwargs = {
            'file_name': 'pop_%s' % photo_uuid.hex,
            'post_date': now(),
            'user_uuid': user.uuid,
            'media_type': media_type
        }
        if is_test == True:
            kwargs['is_test'] = True
        photo = ProfileOnlyPhoto(photo_uuid, **kwargs)
    photo.save()
    return photo

def get_photo(photo_uuid, check_copy_complete=True):
    if not photo_uuid:
        return None
    try:
        result = get_one(Photo, 'uuid_index', photo_uuid, raises=True,
                         consistent_read=False)
        if check_copy_complete and not result.copy_complete:
            return None
        return result
    except (DoesNotExist, IndexError):
        pass
    try:
        result = ProfileOnlyPhoto.get(photo_uuid, consistent_read=False)
        if check_copy_complete and not result.copy_complete:
            return None
        return result
    except DoesNotExist:
        return None

def get_photo_hour(photo_uuid):
    return get_one(HourLeaderboard, 'uuid_index', photo_uuid,
                   consistent_read=False)

def get_photo_today(photo_uuid):
    return get_one(TodayLeaderboard, 'uuid_index', photo_uuid,
                   consistent_read=False)

def get_photo_week(photo_uuid):
    return get_one(WeekLeaderboard, 'uuid_index', photo_uuid,
                   consistent_read=False)

def get_photo_month(photo_uuid):
    return get_one(MonthLeaderboard, 'uuid_index', photo_uuid,
                   consistent_read=False)

def get_photo_year(photo_uuid):
    return get_one(YearLeaderboard, 'uuid_index', photo_uuid,
                   consistent_read=False)

class CropError(Exception):
    pass

def crop(photo_file_name, key_name, size):
    """Crop a file in the working directory and put it in s3."""
    if not settings.PHOTO_CROP_ENABLED:
        # TODO: local test of photo.crop
        log.info("skipping photo.crop because PHOTO_CROP_ENABLED=False")
        return
    s = (size, size)
    outfile = '%s_%sx%s' % (photo_file_name, size, size)
    try:
        log.info("opening photo file for resizing {}, saving to {}".format(photo_file_name, outfile))
        im = Image.open(photo_file_name)
        im.thumbnail(s, Image.ANTIALIAS)
        # Note default quality is 75. Above 95 should be avoided.
        im.save(outfile, "JPEG", quality=80, optimize=True, progressive=True)
    except IOError as e:
        log.exception(e)
        log.error('Cannot create thumbnail for %s', outfile)
        raise CropError
    log.info("Worker cropped key: {}, file: {}".format(key_name, outfile))
    # Worker cropped key: f67f22847ecf311e4a264c8e0eb16059b_82fbacf86ef811e69f5d060a3b9f8ce7, file: / var / ocean / photos / f67f22847ecf311e4a264c8e0eb16059b_82fbacf86ef811e69f5d060a3b9f8ce7_cover.jpg_240x240
    # crop makes a file with the same name + _sizexsize
    # Copy new file to s3
    postfix = '_%sx%s' % (size, size)
    thumb_key_name = '%s%s' % (key_name, postfix)
    thumb_key = Key(get_serve_bucket(), thumb_key_name)
    thumb_key.set_contents_from_filename(outfile)
    log.info("Worker sent %s to serve bucket", outfile)

def preserve(file_name, key_name, suffix=None):
    """Preserve an original photo in the working directory and serve in s3."""
    log.info("preserve {} {}".format(file_name, key_name))
    if not settings.PHOTO_CROP_ENABLED:
        # TODO: local test of photo.crop
        log.info("skipping photo.crop because PHOTO_CROP_ENABLED=False")
        return
    if suffix:
        preserved_key_name = '{}_{}'.format(key_name, suffix)
    else:
        preserved_key_name = key_name
    key = Key(get_serve_bucket(), preserved_key_name)
    key.set_contents_from_filename(file_name)
    log.info("Worker sent {} to serve bucket at key {}".format(
            file_name, preserved_key_name))

def file_hash(path):
    """Computes a dupe-checking hash for a photo."""
    import hashlib
    def hashfile(afile, hasher, blocksize=65536):
        buf = afile.read(blocksize)
        while len(buf) > 0:
            hasher.update(buf)
            buf = afile.read(blocksize)
        return hasher.digest()

    with open(path, 'rb') as f:
        return hashfile(f, hashlib.sha256())
