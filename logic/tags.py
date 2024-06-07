from __future__ import division, absolute_import, unicode_literals

# Tag Notes:
# Tags look like this internally: m_#tagname, where 'm' is the gender (m or f)
#    and the #tagname is the name of the tag with the customary '#' hash.
# They are case preserving, but case insensitive.
# So, the hash_key of the tag is m_#tagname (item.gender_tag) and it keeps
# a separate field, (item.with_case) for m_#TagName

import json

from log import log
from logic import kinesis
from logic.photo import get_photo
from logic import search
import model
from settings import settings
from util import now, pluralize, stringify

TOP_TAGS_MALE = [
    'fitness',
    'beach',
    'fashion',
    'selfie',
]

TOP_TAGS_FEMALE = [
    'fitness',
    'beach',
    'bikini',
    'fashion',
    'selfie',
]

def get_cover_photo(gender_tag):
    tags_by_score = model.PhotoGenderTag.score_index.query(
        gender_tag, scan_index_forward=False, consistent_read=False, limit=2)
    tags_by_score = list(tags_by_score)
    if not tags_by_score:
        return None
    cover_tag = tags_by_score[0]
    return get_photo(cover_tag.uuid)

def add_tag(user, photo, tag):
    # TODO: GENDER HACK put this back
    gender = 'f'  # 'm' if user.show_gender_male else 'f'
    photo.tags.add(tag)
    photo.save()
    gender_tag = '{}_{}'.format(gender, tag.lower())
    photo_gender_tag = model.PhotoGenderTag(gender_tag.lower(),
                                            uuid=photo.uuid,
                                            score=photo.score,
                                            tag_with_case=tag)
    photo_gender_tag.save()
    log_tag_for_trending(gender, tag)
    search.add_tag(gender, tag)

def log_tag_for_trending(gender, tag):
    data = json.dumps([gender, tag])
    partition_key = '0'
    log.info("log_tag_for_trending {} {} {}".format(settings.TAG_TREND_STREAM,
                                                     gender,
                                                     tag))
    kinesis.get_kinesis().put_record(settings.TAG_TREND_STREAM,
                                     data,
                                     partition_key)

def do_tag_trends():
    """Get all pending taggings from stream and update GenderTagTrend table."""
    # This is called by cron.
    # Get all the taggings and make a name -> count dict.
    log.info("do_tag_trends")
    start_time = now()
    count = 0
    tag_counts = {}  # gender_tag -> count
    log.info("tag trends reading {}".format(settings.TAG_TREND_STREAM))
    for data in kinesis.get_all(settings.TAG_TREND_STREAM):
        gender, tag = json.loads(data)
        gender_tag = '{}_{}'.format(gender, tag.lower())
        try:
            tag_counts[gender_tag] += 1
        except KeyError:
            tag_counts[gender_tag] = 1
        count += 1
    next_time = now()
    log.info("do_tag_trends has {} from {}, took {}".format(
        pluralize(tag_counts, 'tag'),
        pluralize(count, 'tagging'),
        stringify(next_time-start_time)))
    start_time = next_time

    # Scan the Trend table, and update or delete each record. Any trends that
    # were not updated are then created.
    tag_updates = set()
    update_count = 0
    delete_count = 0
    # Note: we could shard this, and then the scan becomes a query.
    # OR we could re-put everything on the stream, no dynamodb.
    # (that is, put the expirations on the stream too, in this call.)
    for tag_trend in model.GenderTagTrend.scan():
        gender_tag = tag_trend.get_gender_tag()
        try:
            tag_count = tag_counts[gender_tag]
        except KeyError:
            tag_trend.delete()
            delete_count += 1
        else:
            tag_trend.rank = tag_count
            tag_updates.add(gender_tag)
            tag_trend.save()
            update_count += 1
    next_time = now()
    log.info("do_tag_trends scan had {} and {}, took {}".format(
        pluralize(update_count, 'update'),
        pluralize(delete_count, 'delete'),
        stringify(next_time-start_time)))
    start_time = next_time
    create_count = 0
    for gender_tag, count in tag_counts.items():
        if gender_tag not in tag_updates:
            gender, lower = gender_tag.split('_', 1)
            tag_trend = model.GenderTagTrend(gender,
                                             rank=count,
                                             tag_with_case=lower)
            tag_trend.save()
            create_count += 1
    next_time = now()
    log.info("do_tag_trends create had {}, took {}".format(
        pluralize(create_count, 'create'),
        stringify(next_time-start_time)))
    log.info("do_tag_trends done")