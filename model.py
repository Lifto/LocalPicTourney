from __future__ import division, absolute_import, unicode_literals

from delorean import parse
from pynamodb.models import Model
from pynamodb.attributes import (BinaryAttribute,
                                 BooleanAttribute,
                                 NumberAttribute,
                                 UnicodeAttribute,
                                 UnicodeSetAttribute)
from pynamodb.attributes import UTCDateTimeAttribute as PynamoDBUTCDateTimeAttribute
from pynamodb.exceptions import DoesNotExist
from pynamodb.indexes import (AllProjection, GlobalSecondaryIndex,
                              IncludeProjection, KeysOnlyProjection,
                              LocalSecondaryIndex)

from log import log
from logic.stats import classAwareDecorator, instanceAwareDecorator
from logic.timeuuid import pack_timeuuid_binary, unpack_timeuuid_binary
from settings import settings
from util import grouper

# TODO: Default Read and Write Units are all too low for prod.
DEFAULT_USER_READ_UNITS = 2
DEFAULT_USER_WRITE_UNITS = 2
DEFAULT_PHOTO_READ_UNITS = 2
DEFAULT_PHOTO_WRITE_UNITS = 2

# -- Table Management ---------------------------------------------------------

def create_table(obj, wait):
    log.debug("create table %s", obj)
    if not obj.exists():
        log.info('Creating table %s (table_name=%s)',
            obj.__name__, obj.Meta.table_name)
        read_capacity_units = getattr(obj,
                                      'read_capacity_units',
                                      DEFAULT_USER_READ_UNITS)
        write_capacity_units = getattr(obj,
                                      'write_capacity_units',
                                      DEFAULT_USER_WRITE_UNITS)
        obj.create_table(read_capacity_units=read_capacity_units,
                         write_capacity_units=write_capacity_units,
                         wait=wait)

def get_models():
    return [User, UserName, Photo, Match, PhotoComment, ShardIterator,
            Tournament, Win, Following, Follower, Flag,
            FlagStatus, FlagHistory, FeedActivity, TodayLeaderboard,
            WeekLeaderboard, MonthLeaderboard, ProfileOnlyPhoto,
            FacebookLog, Award, HourLeaderboard, YearLeaderboard,
            PhotoGenderTag, GenderTagTrend]

def create_model(wait_all=True):
    for models in grouper(10, get_models()):
        [create_table(o, False) for o in models]
        if wait_all:
            from pynamodb.constants import TABLE_STATUS, ACTIVE
            ready = False
            while not ready:
                for o in models:
                    status = o._get_connection().describe_table()
                    if status:
                        data = status.get(TABLE_STATUS)
                        if data != ACTIVE:
                            from time import sleep
                            sleep(.25)
                            break
                else:
                    # This else block only runs if the loop completes.
                    ready = True

def assert_model():
    from pynamodb.constants import TABLE_STATUS, ACTIVE
    for model in get_models():
        status = model._get_connection().describe_table()
        ok = False
        if status:
            data = status.get(TABLE_STATUS)
            if data == ACTIVE:
                ok = True
        if not ok:
            raise ValueError('describe {} got {}'.format(model.Meta.table_name,
                                                         status))
    return True

# This is a tool rescued from db_tools before it goes away.
# DynamoDB is a NoSQL database: Except for the required primary key, a DynamoDB
# table is schema-less.  Individual items in a DynamoDB table can have any
# number of attributes, although there is a limit of 400 KB on the item size.
# This means there is no way to introspect the attributes.
# What this means for us, if we want to do migrations:
# (this all needs confirmation.)
# * deprecate fields by not using them.  Remove them from the model, done.
# * You may want to scan the tables and delete all the old values, if you
#   want to reuse a field, you must.
# * New fields must all support null=True, or you must scan the existings to
#   write the default, and not read back until the scan completes after you
#   have started using the new field.
# * We can't confirm these with a tool, we must follow procedure.
# * It would be good to know what certain failures look like (like loading
# a record with a missing attribute that is null=False in the model.)
# * example of a scan:
# for user in User.scan():
#     user.new_attribute = u'abc'
#     user.save()

def model_diff(models=None):
    """
    Compare the expectations of the model with the realities of the db.

    Note that we can not introspect attributes that are not hash or range keys.

    """
    if models is None:
        from model import get_models
        models = get_models()
    # Get the data from the server.
    table_names = get_conn().list_tables()[u'TableNames']
    #{'ResponseMetadata':
    #    {'HTTPStatusCode': 200,
    #     'RequestId': 'PH81QWQ0OPLN4ABLTTWNXBVIDAMT1NCOWXMOOZSTJYVR7BRKBPTY'
    # },
    # u'TableNames': [u'foo', u'bar', u'baz_qux']}

    # Note that only hash and range attributes are returned.
    attributes = {}  # table_name -> attribute_name -> type
    hashes = {}  # table_name -> hash attribute_name
    ranges = {}  # table_name -> range attribute_name
    for table_name in table_names:
        info = get_conn().describe_table(table_name)
        assert info['TableName'] == table_name
        attrs = {}
        attributes[table_name] = attrs
        for a in info['AttributeDefinitions']:
            attrs[a['AttributeName']] = a['AttributeType']
        for k in info['KeySchema']:
            if k['KeyType'] == 'HASH':
                hashes[table_name] = k['AttributeName']
            elif k['KeyType'] == 'RANGE':
                ranges[table_name] = k['AttributeName']
    #pprint(hashes)
    #pprint(ranges)
    #{u'AttributeDefinitions': [
    #    {u'AttributeName': u'photo_uuids',
    #     u'AttributeType': u'B'},
    #    {u'AttributeName': u'user_uuid',
    #      u'AttributeType': u'B'}
    # ],
    # u'ProvisionedThroughput': {
    #     u'NumberOfDecreasesToday': 0,
    #     u'WriteCapacityUnits': 2,
    #     u'ReadCapacityUnits': 2
    # },
    # u'TableSizeBytes': 0,
    # u'TableName': u'dev_match',
    # u'TableStatus': u'ACTIVE',
    # u'KeySchema': [
    #    {u'KeyType': u'HASH',
    #     u'AttributeName': u'photo_uuids'},
    #    {u'KeyType': u'RANGE', u'AttributeName': u'user_uuid'}
    # ],
    # u'ItemCount': 0,
    # u'CreationDateTime': datetime.datetime(2015, 3, 16, 16, 46, 13, 76000,
    # tzinfo=tzlocal())}

    # Compare the schema we have to Cassandra.
    errors = []
    model_table_names = set()  # Table names that can be found in the model.
    for model_class in models:
        table_name = model_class.Meta.table_name
        model_table_names.add(table_name)
        if table_name not in attributes:
            errors.append('DynamoDB does not have table %s' % (table_name))
            continue
        db_attrs = attributes[table_name]
        model_attrs = model_class._get_attributes()
        for attr_name, attr in model_attrs.iteritems():
            if attr.is_hash_key:
                if table_name not in hashes:
                    errors.append('DynamoDB %s reported no hash' % table_name)
                elif hashes[table_name] != attr_name:
                    errors.append('DynamoDB %s says hash %s, model says %s' % (
                        table_name, hashes[table_name], attr_name))
                elif db_attrs[attr_name] != type_to_meta[attr.attr_type]:
                    errors.append('%s Model %s is type %s, DynamoDB is type %s' % (
                        table_name, attr_name, attr.attr_type, db_attrs[attr_name]))
            elif attr.is_range_key:
                if table_name not in ranges:
                    errors.append('DynamoDB %s reported no range' % table_name)
                elif ranges[table_name] != attr_name:
                    errors.append('DynamoDB %s says range %s, model says %s' % (
                        table_name, ranges[table_name], attr_name))
                elif db_attrs[attr_name] != type_to_meta[attr.attr_type]:
                    errors.append('%s Model %s is type %s, DynamoDB is type %s' % (
                        table_name, attr_name, attr.attr_type, db_attrs[attr_name]))

        for attr_name, attr_type in db_attrs.iteritems():
            if attr_name not in model_attrs:
                errors.append('DynamoDB has %s.%s, model does not.' % (
                    table_name, attr_name))
        if table_name in hashes and not model_attrs[hashes[table_name]].is_hash_key:
            errors.append('DynamoDB %s.%s hash is not hash in model' % (
                table_name, hashes[table_name]))
        if table_name in ranges and not model_attrs[ranges[table_name]].is_range_key:
            errors.append('DynamoDB %s.%s range is not range in model' % (
                table_name, ranges[table_name]))

    for table_name, attrs in attributes.iteritems():
        if table_name not in model_table_names:
            errors.append('DynamoDB had unexpected table %s' % table_name)
    return errors

# -- Query Help ---------------------------------------------------------------

def get_one(model_class, index_name, hash_key, raises=False, **kwargs):
    try:
        # don't know why has to be limit=2, but limit=1 gives nada.
        return getattr(model_class, index_name).query(hash_key,
                                                      limit=2,
                                                      **kwargs).next()
    except StopIteration:
        if raises:
            raise DoesNotExist
        return None

# -- Custom Attributes --------------------------------------------------------

class UUIDAttribute(BinaryAttribute):
    """Attribute holding a uuid1, time sortable."""
    def serialize(self, value):
        if value is None:
            bytes = ''
        else:
            bytes = pack_timeuuid_binary(value)
        return super(UUIDAttribute, self).serialize(bytes)

    def deserialize(self, value):
        bytes = super(UUIDAttribute, self).deserialize(value)
        if bytes == '':
            return None
        return unpack_timeuuid_binary(bytes)


class DoubleUUIDAttribute(BinaryAttribute):
    """Attribute holding two uuids. Order insensitive (uA,uB)==(uB,uA)."""
    def serialize(self, value):
        uuid_a, uuid_b = value
        bytes = pack_timeuuid_binary(uuid_a) + pack_timeuuid_binary(uuid_b)
        return super(DoubleUUIDAttribute, self).serialize(bytes)

    def deserialize(self, value):
        bytes = super(DoubleUUIDAttribute, self).deserialize(value)
        bytes_a = bytes[:24]
        bytes_b = bytes[24:]
        return unpack_timeuuid_binary(bytes_a), unpack_timeuuid_binary(bytes_b)


class GeoCoordinate(NumberAttribute):
    def serialize(self, value):
        value = int(value * 10000)
        return super(GeoCoordinate, self).serialize(value)

    def deserialize(self, value):
        value = super(GeoCoordinate, self).deserialize(value)
        return value / 10000.0


class UTCDateTimeAttribute(PynamoDBUTCDateTimeAttribute):
    # Patching an issue with Pynamo. This is fixed on their dev branch.
    def deserialize(self, value):
        """
        Takes a UTC datetime string and returns a datetime object
        """
        return parse(value, dayfirst=False).datetime


if settings.DYNAMO_DB_HOST is None:
    class OceanMeta(object):
        base_name = settings.NAME_PREFIX
        region = settings.DYNAMO_DB_REGION
else:
    class OceanMeta(object):
        base_name = settings.NAME_PREFIX
        host = settings.DYNAMO_DB_HOST


class StatsModel(Model):
    """Subclass of PynamoDB model that reports to statsd"""
    @classmethod
    @classAwareDecorator('get')
    def get(cls, *args, **kwargs):
        return super(StatsModel, cls).get(*args, **kwargs)

    @classmethod
    @classAwareDecorator('query')
    def query(cls, *args, **kwargs):
        return super(StatsModel, cls).query(*args, **kwargs)

    @instanceAwareDecorator('save')
    def save(cls, *args, **kwargs):
        return super(StatsModel, cls).save(*args, **kwargs)


class StatsGlobalSecondaryIndex(GlobalSecondaryIndex):
    @classmethod
    @classAwareDecorator('count')
    def count(cls, *args, **kwargs):
        return super(StatsGlobalSecondaryIndex, cls).count(*args, **kwargs)

    @classmethod
    @classAwareDecorator('query')
    def query(cls, *args, **kwargs):
        return super(StatsGlobalSecondaryIndex, cls).query(*args, **kwargs)


class StatsLocalSecondaryIndex(LocalSecondaryIndex):
    @classmethod
    @classAwareDecorator('count')
    def count(cls, *args, **kwargs):
        return super(StatsLocalSecondaryIndex, cls).count(*args, **kwargs)

    @classmethod
    @classAwareDecorator('query')
    def query(cls, *args, **kwargs):
        return super(StatsLocalSecondaryIndex, cls).query(*args, **kwargs)


# -- User ---------------------------------------------------------------------

class UserByFacebook(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'user_facebook_id_index'
        projection = IncludeProjection(['token'])
        read_capacity_units = 2
        write_capacity_units = 1

    facebook_id = UnicodeAttribute(hash_key=True)


class User(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'user'

    uuid = UUIDAttribute(hash_key=True)
    token = UnicodeAttribute(null=True)
    auth_enabled = BooleanAttribute(default=True)
    user_name = UnicodeAttribute(null=True)
    first_name = UnicodeAttribute(null=True)
    last_name = UnicodeAttribute(null=True)
    biography = UnicodeAttribute(null=True)
    snapchat = UnicodeAttribute(null=True)
    instagram = UnicodeAttribute(null=True)
    website = UnicodeAttribute(null=True)
    # True if views Male contestants, False if views Female contestants.
    view_gender_male = BooleanAttribute(null=True)
    # True if their pictures are shown with Males, False if Female.
    show_gender_male = BooleanAttribute(null=True)
    joined_date = UTCDateTimeAttribute(null=True)
    last_facebook_event_date = UTCDateTimeAttribute(null=True)
    last_facebook_event = UnicodeAttribute(null=True)
    facebook_id = UnicodeAttribute(null=True)
    facebook_api_token = UnicodeAttribute(null=True)
    facebook_gender = UnicodeAttribute(null=True)
    photo = UUIDAttribute(null=True)
    lat = GeoCoordinate(null=True)
    lon = GeoCoordinate(null=True)
    geodata = UnicodeAttribute(null=True)
    location = UUIDAttribute(null=True)
    user_agent = UnicodeAttribute(null=True)
    # 'need facebook', 'need facebook token confirmation', 'need name',
    # 'need pic', 'ok'
    registration = UnicodeAttribute(default='need facebook')
    win_count = NumberAttribute(null=True, default=0)
    loss_count = NumberAttribute(null=True, default=0)
    is_test_user = BooleanAttribute(null=True)  # Deprecated
    is_test = BooleanAttribute(null=True)

    # Tournament Status
    # When this is 0, we have a tournament and it resets.
    matches_until_next_tournament = NumberAttribute()
    # The next tournament is of this type.
    next_tournament = UnicodeAttribute()
    last_tournament_status_access = UTCDateTimeAttribute()

    facebook_id_index = UserByFacebook()

    # Notification Settings
    apn_device_id = UnicodeAttribute(null=True)
    notify_new_photo = BooleanAttribute(null=True, default=True)
    notify_new_comment = BooleanAttribute(null=True, default=True)
    notify_won_tournament = BooleanAttribute(null=True, default=True)
    notify_you_won_tournament = BooleanAttribute(null=True, default=True)
    notify_new_follower = BooleanAttribute(null=True, default=True)

    def get_view_gender_location(self):
        if not self.location:
            from ocean_exceptions import InvalidAPIUsage
            raise InvalidAPIUsage('User does not have location')
        gender = 'm' if self.view_gender_male else 'f'
        gender = 'f'  # TODO: TAKE this out!
        # TODO: put this back, but as of now, everybody views female LA.
        return '%s%s' % (gender, self.location.hex)

    def get_show_gender_location(self):
        gender = 'm' if self.show_gender_male else 'f'
        gender = 'f'  # TODO: TAKE this out!
        # TODO: put this back, but as of now, everybody views female LA.
        return '%s%s' % (gender, self.location.hex)

    def get_photo(self):
        from logic.photo import get_photo
        return get_photo(self.photo)

    def get_location(self):
        from logic.location import LOCATIONS
        if self.location is None:
            return None
        return LOCATIONS.get(self.location)

    def get_gender_string(self):
        if self.show_gender_male is None:
            return None
        if self.show_gender_male:
            return 'male'
        else:
            return 'female'

    def get_win_loss_ratio(self):
        win_count = self.win_count
        loss_count = self.loss_count
        match_count = win_count + loss_count
        if match_count:
            return 100.0 * win_count / (win_count + loss_count)
        else:
            return 50.0


class UserName(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'user_name'

    name = UnicodeAttribute(hash_key=True)
    user_uuid = UUIDAttribute()


class FacebookLog(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'facebook_log'

    user_uuid = UUIDAttribute(hash_key=True)
    recorded_on = UTCDateTimeAttribute(range_key=True)
    data = UnicodeAttribute()


# -- Photo --------------------------------------------------------------------

class PhotoByUUID(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'photo_uuid_index'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    # This attribute is the hash key for the index
    # Note that this attribute must also exist
    # in the model
    uuid = UUIDAttribute(hash_key=True)


class PhotoByScore(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'photo_score_index'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class AllPhotoByScore(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'photo_score_index_all'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    null_hash = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class PhotoByUser(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'photo_user_index'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    # This attribute is the hash key for the index
    # Note that this attribute must also exist
    # in the model
    user_uuid = UUIDAttribute(hash_key=True)
    post_date = UTCDateTimeAttribute(range_key=True)


class PhotoByPostDate(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'photo_post_date_index'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    post_date = UTCDateTimeAttribute(range_key=True)


class PhotoByDupeHash(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'photo_dupe_hash_index'
        projection = KeysOnlyProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    # This attribute is the hash key for the index
    # Note that this attribute must also exist
    # in the model
    dupe_hash = BinaryAttribute(hash_key=True)


class Photo(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'photo'

    gender_location = UnicodeAttribute(hash_key=True)
    uuid = UUIDAttribute(range_key=True)
    uuid_index = PhotoByUUID()
    score_index = PhotoByScore()
    all_score_index = AllPhotoByScore()
    user_index = PhotoByUser()
    post_date_index = PhotoByPostDate()
    file_name = UnicodeAttribute()
    post_date = UTCDateTimeAttribute()
    user_uuid = UUIDAttribute()
    score = NumberAttribute(default=1500.0)  # Glicko2 mu, rating.
    phi = NumberAttribute(default=350.0)  # Gilcko2 phi, rating deviation.
    sigma = NumberAttribute(default=0.006)  # Glicko2 sigma, volatility.
    live = BooleanAttribute(default=False)
    uploaded = BooleanAttribute(default=False)
    copy_complete = BooleanAttribute(default=False)
    location = UUIDAttribute()
    lat = GeoCoordinate()
    lon = GeoCoordinate()
    geodata = UnicodeAttribute()
    set_as_profile_photo = BooleanAttribute()
    match_bumped = BooleanAttribute(null=True, default=False)
    null_hash = UnicodeAttribute(default='all')  # For all-gender_location score.
    dupe_hash = BinaryAttribute(null=True)
    dupe_hash_index = PhotoByDupeHash()
    is_duplicate = BooleanAttribute(null=True)
    tags = UnicodeSetAttribute(null=True, default=set())
    media_type = UnicodeAttribute(null=True)  # 'photo' or 'movie'
    is_test = BooleanAttribute(null=True)

    def get_tags(self):
        return self.tags

    def get_is_gender_male(self):
        return self.gender_location.startswith('m')

    def get_location(self):
        return self.gender_location[1:]

    @staticmethod
    def make_gender_location(is_gender_male, location):
        gender = 'm' if is_gender_male else 'f'
        return '%s%s' % (gender, location)

    def get_awards(self):
        from logic.awards import get_awards_by_photo
        return get_awards_by_photo(self.uuid)

    def get_comment_count(self):
        return PhotoComment.count(self.uuid, consistent_read=False)

    def is_profile_only(self):
        return False

    def get_share_url(self):
        return 'http://{}/{}'.format(settings.SHARE_URL, self.uuid.hex)


class PhotoGenderTagByScore(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'photo_gender_tag_score_index'
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    gender_tag = UnicodeAttribute(hash_key=True)  # lower-case
    score = NumberAttribute(default=0.0, range_key=True)


class PhotoGenderTag(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'photo_by_gender_tag'

    gender_tag = UnicodeAttribute(hash_key=True)  # lower-case
    uuid = UUIDAttribute(range_key=True)  # uuid of the Photo this refers to.
    score = NumberAttribute(default=0.0)  # Glicko2 mu, rating.
    score_index = PhotoGenderTagByScore()
    tag_with_case = UnicodeAttribute()  # tag with casing preserved


class GenderTagTrend(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'tag_trend'

    gender = UnicodeAttribute(hash_key=True)
    rank = NumberAttribute(default=0.0, range_key=True)  # Posts per interval.
    tag_with_case = UnicodeAttribute()  # tag with casing preserved

    def get_gender_tag(self):
        return '{}_{}'.format(self.gender, self.tag_with_case.lower())


class ProfileOnlyPhoto(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'profile_only_photo'

    uuid = UUIDAttribute(hash_key=True)
    file_name = UnicodeAttribute()
    post_date = UTCDateTimeAttribute()
    user_uuid = UUIDAttribute()
    live = BooleanAttribute(default=False)
    uploaded = BooleanAttribute(default=False)
    copy_complete = BooleanAttribute(default=False)
    is_duplicate = BooleanAttribute(default=False)
    is_test = BooleanAttribute(null=True)
    media_type = UnicodeAttribute(null=True)

    def get_tags(self):
        return set()

    def get_comment_count(self):
        return 0

    def is_profile_only(self):
        return True

    def get_share_url(self):
        return 'http://{}/{}'.format(settings.SHARE_URL, self.uuid.hex)


class HourByUUID(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'hour_leaderboard_uuid_index'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    uuid = UUIDAttribute(hash_key=True)


class HourByScore(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'hour_leaderboard_score_index'
        # All attributes are projected
        # TODO: does this need to be allprojection?
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class AllHourByScore(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'hour_leaderboard_score_index_all'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    null_hash = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class HourByDate(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'hour_leaderboard_date_index'
        # All attributes are projected
        # TODO: does this need to be allprojection?
        projection = AllProjection()
        read_capacity_units = 4
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    post_date = UTCDateTimeAttribute(range_key=True)


class HourLeaderboard(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'hour_leaderboard'

    gender_location = UnicodeAttribute(hash_key=True)
    uuid = UUIDAttribute(range_key=True)
    score = NumberAttribute(default=1500)
    post_date = UTCDateTimeAttribute()
    date_index = HourByDate()
    score_index = HourByScore()
    all_score_index = AllHourByScore()
    uuid_index = HourByUUID()
    null_hash = UnicodeAttribute(default='all')  # For all-gender_location score.


class TodayByUUID(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'today_leaderboard_uuid_index'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    uuid = UUIDAttribute(hash_key=True)


class TodayByScore(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'today_leaderboard_score_index'
        # All attributes are projected
        # TODO: does this need to be allprojection?
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class AllTodayByScore(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'today_leaderboard_score_index_all'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    null_hash = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class TodayByDate(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'today_leaderboard_date_index'
        # All attributes are projected
        # TODO: does this need to be allprojection?
        projection = AllProjection()
        read_capacity_units = 4
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    post_date = UTCDateTimeAttribute(range_key=True)


class TodayLeaderboard(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'today_leaderboard'

    gender_location = UnicodeAttribute(hash_key=True)
    uuid = UUIDAttribute(range_key=True)
    score = NumberAttribute(default=1500)
    post_date = UTCDateTimeAttribute()
    date_index = TodayByDate()
    score_index = TodayByScore()
    uuid_index = TodayByUUID()
    all_score_index = AllTodayByScore()
    null_hash = UnicodeAttribute(default='all')  # For all-gender_location score.


class WeekByUUID(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'week_leaderboard_uuid_index'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    uuid = UUIDAttribute(hash_key=True)


class WeekByScore(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'week_leaderboard_score_index'
        # All attributes are projected
        # TODO: does this need to be allprojection?
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class AllWeekByScore(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'week_leaderboard_score_index_all'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    null_hash = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class WeekByDate(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'week_leaderboard_date_index'
        # All attributes are projected
        # TODO: does this need to be allprojection?
        projection = AllProjection()
        read_capacity_units = 4
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    post_date = UTCDateTimeAttribute(range_key=True)


class WeekLeaderboard(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'week_leaderboard'

    gender_location = UnicodeAttribute(hash_key=True)
    uuid = UUIDAttribute(range_key=True)  # uuid of Photo this refers to.
    score = NumberAttribute(default=1500)
    post_date = UTCDateTimeAttribute()
    date_index = WeekByDate()
    score_index = WeekByScore()
    uuid_index = WeekByUUID()
    all_score_index = AllWeekByScore()
    null_hash = UnicodeAttribute(default='all')  # For all-gender_location score.


class MonthByUUID(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'month_leaderboard_uuid_index'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    uuid = UUIDAttribute(hash_key=True)


class MonthByScore(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'month_leaderboard_score_index'
        # All attributes are projected
        # TODO: does this need to be allprojection?
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class AllMonthByScore(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'month_leaderboard_score_index_all'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    null_hash = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class MonthByDate(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'month_leaderboard_date_index'
        # All attributes are projected
        # TODO: does this need to be allprojection?
        projection = AllProjection()
        read_capacity_units = 4
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    post_date = UTCDateTimeAttribute(range_key=True)


class MonthLeaderboard(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'month_leaderboard'

    gender_location = UnicodeAttribute(hash_key=True)
    uuid = UUIDAttribute(range_key=True)
    score = NumberAttribute(default=1500)
    post_date = UTCDateTimeAttribute()
    date_index = MonthByDate()
    score_index = MonthByScore()
    uuid_index = MonthByUUID()
    all_score_index = AllMonthByScore()
    null_hash = UnicodeAttribute(default='all')  # For all-gender_location score.


class YearByUUID(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'year_leaderboard_uuid_index'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    uuid = UUIDAttribute(hash_key=True)


class YearByScore(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'year_leaderboard_score_index'
        # All attributes are projected
        # TODO: does this need to be allprojection?
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class AllYearByScore(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'year_leaderboard_score_index_all'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    null_hash = UnicodeAttribute(hash_key=True)
    score = NumberAttribute(range_key=True)


class YearByDate(StatsLocalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'year_leaderboard_date_index'
        # All attributes are projected
        # TODO: does this need to be allprojection?
        projection = AllProjection()
        read_capacity_units = 4
        write_capacity_units = 1

    gender_location = UnicodeAttribute(hash_key=True)
    post_date = UTCDateTimeAttribute(range_key=True)


class YearLeaderboard(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'year_leaderboard'

    gender_location = UnicodeAttribute(hash_key=True)
    uuid = UUIDAttribute(range_key=True)
    score = NumberAttribute(default=1500)
    post_date = UTCDateTimeAttribute()
    date_index = YearByDate()
    score_index = YearByScore()
    uuid_index = YearByUUID()
    all_score_index = AllYearByScore()
    null_hash = UnicodeAttribute(default='all')  # For all-gender_location score.


class PhotoCommentByUUID(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'photo_comment_uuid_index'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    # This attribute is the hash key for the index
    # Note that this attribute must also exist
    # in the model
    uuid = UUIDAttribute(hash_key=True)


class PhotoComment(StatsModel):
    read_capacity_units = 4  # TODO: Move this so it doesn't appear in instances?

    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'photo_comment'

    photo_uuid = UUIDAttribute(hash_key=True)
    posted_at = UTCDateTimeAttribute(range_key=True)
    uuid = UUIDAttribute(null=True)
    uuid_index = PhotoCommentByUUID()
    text = UnicodeAttribute()
    user_uuid = UUIDAttribute()
    lat = GeoCoordinate()
    lon = GeoCoordinate()
    geodata = UnicodeAttribute()
    location = UUIDAttribute()


class Award(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'award'

    photo_uuid = UUIDAttribute(hash_key=True)
    uuid = UUIDAttribute(range_key=True)
    awarded_on = UTCDateTimeAttribute(null=False)
    kind = UnicodeAttribute(null=False)

# -- Match --------------------------------------------------------------------

class Match(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'match'

    photo_uuids = DoubleUUIDAttribute(hash_key=True)
    user_uuid = UUIDAttribute(range_key=True)
    judged = BooleanAttribute(default=False)
    proposed_date = UTCDateTimeAttribute()
    judged_date = UTCDateTimeAttribute(null=True)
    scored_date = UTCDateTimeAttribute(null=True)
    a_win_delta = NumberAttribute(null=True)
    a_lose_delta = NumberAttribute(null=True)
    b_win_delta = NumberAttribute(null=True)
    b_lose_delta = NumberAttribute(null=True)
    # True if 'a' won, False if 'b' won
    a_won = BooleanAttribute(null=True)
    # If this is part of a tournament, this is the tournament
    tournament_uuid = UUIDAttribute(null=True)
    # If this is part of a tournament, this is the bracket position it was.
    tournament_position = NumberAttribute(null=True)
    lat = GeoCoordinate()
    lon = GeoCoordinate()
    geodata = UnicodeAttribute()
    location = UUIDAttribute()

    def get_photo_a(self):
        return get_one(Photo, 'uuid_index', self.photo_uuids[0],
                       consistent_read=False)

    def get_photo_b(self):
        return get_one(Photo, 'uuid_index', self.photo_uuids[1],
                       consistent_read=False)


class Tournament(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'tournament'

    user_uuid = UUIDAttribute(hash_key=True)
    uuid = UUIDAttribute(range_key=True)
    kind = UnicodeAttribute()  # local, regional or global
    judged = BooleanAttribute(default=False)
    judged_date = UTCDateTimeAttribute(null=True)
    lat = GeoCoordinate()
    lon = GeoCoordinate()
    geodata = UnicodeAttribute()
    location = UUIDAttribute()
    one_vs_two = UUIDAttribute(null=True)
    three_vs_four = UUIDAttribute(null=True)
    five_vs_six = UUIDAttribute(null=True)
    seven_vs_eight = UUIDAttribute(null=True)
    nine_vs_ten = UUIDAttribute(null=True)
    eleven_vs_twelve = UUIDAttribute(null=True)
    thirteen_vs_fourteen = UUIDAttribute(null=True)
    fifteen_vs_sixteen = UUIDAttribute(null=True)
    one_two_vs_three_four = UUIDAttribute(null=True)
    five_six_vs_seven_eight = UUIDAttribute(null=True)
    nine_ten_vs_eleven_twelve = UUIDAttribute(null=True)
    thirteen_fourteen_vs_fifteen_sixteen = UUIDAttribute(null=True)
    one_four_vs_five_eight = UUIDAttribute(null=True)
    nine_twelve_vs_thirteen_sixteen = UUIDAttribute(null=True)
    winner = UUIDAttribute(null=True)

    # Seed
    one = UUIDAttribute()
    two = UUIDAttribute()
    three = UUIDAttribute()
    four = UUIDAttribute()
    five = UUIDAttribute()
    six = UUIDAttribute()
    seven = UUIDAttribute()
    eight = UUIDAttribute()
    nine = UUIDAttribute()
    ten = UUIDAttribute()
    eleven = UUIDAttribute()
    twelve = UUIDAttribute()
    thirteen = UUIDAttribute()
    fourteen = UUIDAttribute()
    fifteen = UUIDAttribute()
    sixteen = UUIDAttribute()

    @staticmethod
    def position_to_attribute_name(position):
        return {
            0: 'one_vs_two',
            1: 'three_vs_four',
            2: 'five_vs_six',
            3: 'seven_vs_eight',
            4: 'nine_vs_ten',
            5: 'eleven_vs_twelve',
            6: 'thirteen_vs_fourteen',
            7: 'fifteen_vs_sixteen',
            8: 'one_two_vs_three_four',
            9: 'five_six_vs_seven_eight',
            10: 'nine_ten_vs_eleven_twelve',
            11: 'thirteen_fourteen_vs_fifteen_sixteen',
            12: 'one_four_vs_five_eight',
            13: 'nine_twelve_vs_thirteen_sixteen',
            14: 'winner'}[position]


class Win(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'win'

    user_uuid = UUIDAttribute(hash_key=True)
    created_on = UTCDateTimeAttribute(range_key=True)
    win_photo = UUIDAttribute()
    lose_photo = UUIDAttribute()
    uuid = UUIDAttribute()  # unique id of this Win object.


# -- Following ----------------------------------------------------------------

class Following(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'following'

    follower = UUIDAttribute(hash_key=True)
    followed = UUIDAttribute(range_key=True)
    created_on = UTCDateTimeAttribute()


class Follower(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'follower'

    followed = UUIDAttribute(hash_key=True)
    follower = UUIDAttribute(range_key=True)
    created_on = UTCDateTimeAttribute()


# -- Activity -----------------------------------------------------------------

class FeedActivity(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'feed_activity'

    feed_owner = UUIDAttribute(hash_key=True)
    created_on = UTCDateTimeAttribute(range_key=True)
    activity = UnicodeAttribute()
    read = BooleanAttribute(default=False)
    user = UUIDAttribute(null=True)
    photo = UUIDAttribute(null=True)
    comment = UUIDAttribute(null=True)
    uuid = UUIDAttribute()

    def unique_hash(self):
        # TODO: If there are other unique
        if self.activity == 'NewPhoto':
            return self.feed_owner, self.activity, self.user, self.photo, self.comment
        return None



# -- System -------------------------------------------------------------------

class ShardIterator(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'shard_iterator'

    shard_id = UnicodeAttribute(hash_key=True)
    sequence_number = UnicodeAttribute(null=True)


# -- Flagging -----------------------------------------------------------------

class FlagByCountIndex(StatsGlobalSecondaryIndex):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'flag_count_by_index'
        # All attributes are projected
        projection = AllProjection()
        read_capacity_units = 2
        write_capacity_units = 1

    kind = UnicodeAttribute(hash_key=True)
    flag_count = NumberAttribute(range_key=True)


class FlagStatus(StatsModel):
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'flag_status'

    # note -- if an object becomes disabled, we set a boolean on that object
    # so that we don't need to eager load the object. the correctness of the
    # on-object-boolean and this FlagStatus object is tested in the admin
    #  interface.
    kind_id = UnicodeAttribute(hash_key=True)  # name+hash_key of flagged obj.
    kind = UnicodeAttribute()  # used for index hash_key
    flag_count = NumberAttribute(default=0)
    history_count = NumberAttribute(default=0)
    status = UnicodeAttribute(null=True)
    history_updated_on = UTCDateTimeAttribute()
    count_index = FlagByCountIndex()


class FlagHistory(StatsModel):
    """History of flag related activity on a (FlagStatus)Object."""
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'flag_history'
    kind_id = UnicodeAttribute(hash_key=True)  # name+hash_key of flagged obj.
    admin_id = UUIDAttribute(range_key=True)
    action = UnicodeAttribute()  # action taken on this flag.
    created_on = UTCDateTimeAttribute()


class Flag(StatsModel):
    """Flag to flag object for review. One Flag per Object per User.

    We increment the object's flagstatus when a new flag is created.
    """
    class Meta(OceanMeta):
        table_name = OceanMeta.base_name + 'flag'
    kind_id = UnicodeAttribute(hash_key=True)  # name+hash_key of flagged obj.
    user_id = UUIDAttribute(range_key=True)  # User who created flag.
    created_on = UTCDateTimeAttribute()
    reason = UnicodeAttribute()
    ip = UnicodeAttribute()  # Ip-address from whence flag was submitted.

