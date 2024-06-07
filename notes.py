# It is assumed that 'COUNT' is inefficient, it is like a 'scan'.

#  I can think of three options to get the total number of items in a DynamoDB table.
#
# The first option is using the scan, but the scan function is inefficient and is in general a bad practice, especially for tables with heavy reads or production tables.
#
# The second option is what was mention by Atharva:
#
# A better solution that comes to my mind is to maintain the total number of item counts for such tables in a separate table, where each item will have Table name as it's hash key and total number of items in that table as it's non-key attribute. You can then keep this Table possibly named "TotalNumberOfItemsPerTable" updated by making atomic update operations to increment/decrement the total item count for a particular table.
# The only problem this is that increment operations are not idempotent. So if a write fails or you write more than once this will be reflected in the count. If you need pin-point accuracy, use a conditional update instead.
#
# The simplest solution is the DescribeTable which returns ItemCount. The only issue is that the count isn't up to date. The count is updated every 6 hours.
#
# http://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_DescribeTable.html
#--------
# This is the annotated photo worker describing provision error issues
#
# @timingIncrDecorator('worker_handle_photo_upload', track_status=False)
# def handle_photo_upload(payload):
#     # In working on getting this to be retry-safe, we want to use batch
#     # writes. PynamoDB doesn't do batch writes for multiple tables as far
#     # as I can tell.
#     # "The BatchWriteItem operation puts or deletes multiple items in one or
#     # more tables." -- DynamoDB
#
#     # ------- Note how many of these could fail with a provision error, and
#     # consider the state of the system at that time, or at the time of
#     # any of these partial writes even without an error. So, even if we had
#     # perfect provision recovery (which we could have, more-or-less), we
#     # would still have potential inconsistencies.
#
#     key_name = payload['Records'][0]['s3']['object']['key']
#     photo_uuid = uuid.UUID(key_name[-32:])
#     # ------- This could fail with a provision error.
#     # we would just retry from the top. OK
#     photo = get_photo(photo_uuid, check_copy_complete=False)
#     if not photo:
#         log.error("handle_photo_upload could not find %s" % photo_uuid)
#         raise PhotoNotFound
#
#     if photo.uploaded:
#         log.debug("this is a retry, photo {photo} was already uploaded".format(
#                      photo=photo.uuid.hex))
#
#     if not photo.copy_complete:  # If a retry, did not make it past this step.
#         # Copy to local.
#         photo_path = '%s%s' % (settings.PHOTO_DIR, key_name)
#         if os.path.exists(photo_path):
#             log.debug("photo {photo} was already in local storage {path}".format(
#                          photo=photo.uuid.hex, path=photo_path))
#         else:
#             key = s3.get_incoming_bucket().get_key(key_name)
#             try:
#                 key.get_contents_to_filename(photo_path)
#             except Exception as e:
#                 # Attempt to clean up local file if there is an error.
#                 log.error("get_contents_to_filename({path}) failed, deleting".format(
#                              path=photo_path))
#                 log.exception(e)
#                 try:
#                     os.remove(photo_path)
#                 except Exception:
#                     pass
#                 raise
#         if not photo.uploaded:
#             photo.uploaded = True
#             # ------- This could fail with a provision error.
#             # If so the checks above would not re-copy, we'd end up here again.
#             photo.save()
#         log.debug("Worker has photo %s in %s", key_name, photo_path)
#
#         crop(photo_path, key_name, 240)
#         crop(photo_path, key_name, 480)
#         crop(photo_path, key_name, 960)
#
#         if settings.PHOTO_CROP_ENABLED:
#             os.remove(photo_path)
#         else:
#             # TODO: write locally runnable testable photo operations
#             log.info("skipping source photo remove because PHOTO_CROP_ENABLED=False")
#
#         # Update the Photo to indicate the thumbnail is copied and available.
#         # TODO: This is a problem, because the photo is now available for
#         # judging but its leaderboard counterparts may not have written.
#         # Its also a problem if we write the leaderboards, because we would
#         # have a photo in the leaderboards that is not copy-complete.
#         # -- what to do?
#         # * put flags on the photo like 'copy_complete' ?
#         # * repair the missing leaderboards in score processing if we find them?
#         # TODO: We will move the leaderboards to the score worker.
#         photo.copy_complete = True
#         # ------- This could fail with a provision error.
#         # If this failed it will re-copy the photo from s3, re-crop it,
#         # re-remove it. I guess that's OK and not really avoidable.
#         # We could put a check in for photo.uploaded... but we might
#         # be better off checking the photo in storage?
#         # This is OK. Provision errors are rare, and this is not bad.
#         photo.save()
#
#     if photo.file_name.startswith('pop') or photo.set_as_profile_photo:
#         # ------- This could fail with a provision error.
#         user = get_user(photo.user_uuid)
#         user.photo = photo.uuid
#         # ------- This could fail with a provision error.
#         # Retries here could cause a profile-pic setting race if they post
#         # another profile pic right after.
#         # Not the end of the world.
#         user.save()
#     if not photo.file_name.startswith('pop'):
#         # We keep copies of the photo in these 'filtered' leaderboards.
#         photo_hour = HourLeaderboard(photo.category,
#                                      uuid=photo_uuid,
#                                      post_date=photo.post_date)
#         # ------- This could fail with a provision error.
#         photo_hour.save()
#         photo_today = TodayLeaderboard(photo.category,
#                                        uuid=photo_uuid,
#                                        post_date=photo.post_date)
#         # ------- This could fail with a provision error.
#         photo_today.save()
#         photo_week = WeekLeaderboard(photo.category,
#                                      uuid=photo_uuid,
#                                      post_date=photo.post_date)
#         # ------- This could fail with a provision error.
#         photo_week.save()
#         photo_month = MonthLeaderboard(photo.category,
#                                        uuid=photo_uuid,
#                                        post_date=photo.post_date)
#         # ------- This could fail with a provision error.
#         photo_month.save()
#         photo_year = YearLeaderboard(photo.category,
#                                      uuid=photo_uuid,
#                                      post_date=photo.post_date)
#         # ------- This could fail with a provision error.
#         photo_year.save()
#
#     user = get_user(photo.user_uuid)
#     # ------- This could fail with a provision error.
#     # no problem, retry would fix. Idempotent.
#     update_registration_status(user)
#
#     # Add the photo to the feed.
#     # ------- This could fail with a provision error.
#     # This could make for lots of issues. If this retries, people will get
#     # multiple feeds of the item. Can we make the feed's hash be based on
#     # the info?
#     # Hmmm... feed items are keyed on their owner for hash and range is
#     # the created-on date, which would not get re-produced. so, if this
#     # re-tried we'd get a set of duplicates in the feed.
#     # - I think the answer is that a dupe-check needs to happen on the read-
#     # back and then the read-backer can do deletes.
#     feed_new_photo(photo.user_uuid, photo.uuid)
#
#     return ''

#----------------
# This snippet is for testing the binary-attribute index issue.
# temp, move or remove
# def test_photo():
#     from uuid import uuid1
#     photo_uuid = uuid1()
#     p = Photo(u'm310', photo_uuid)
#     p.is_gender_male = True
#     p.location = u'310'
#     p.post_date = datetime.now()
#     p.user_uuid = uuid1()
#     #p.sigma = 2.2
#     p.foo = b'1'
#     p.save()
#     print "saved, getting index on %s" % photo_uuid
#     for item in Photo.uuid_index.query(b'1'):#photo_uuid):
#         print("Item queried from index: {0}".format(item))
#     print "that's all the queries on photo_uuid, here's direct hash"
#     print Photo.get(u'm310', photo_uuid)
#     print "done"
# end temp

# This is broken at the moment. For some reason I can't read back from
# a binary attribute by way of the index. I'm working on getting an issue
# logged with the PynamoDB people.
# class PhotoUUIDIndex(GlobalSecondaryIndex):
#     class Meta(LocalPicTourneyMeta):
#         index_name = LocalPicTourneyMeta.base_name + 'photo_uuid_index'
#         read_capacity_units = 2
#         write_capacity_units = 1
#         projection = AllProjection()
#
#     # This attribute is the hash key for the index
#     # Note that this attribute must also exist
#     # in the model
#     uuid = UUIDAttribute(hash_key=True)

# To find next matches for this photo, read this table for the same
# gender_location and LTE this index.
# class PhotoStreamIndex(LocalSecondaryIndex):
#     class Meta(LocalPicTourneyMeta):
#         index_name = LocalPicTourneyMeta.base_name + 'photo_stream_index'
#         projection = AllProjection()
#
#     gender_location = UnicodeAttribute(hash_key=True)
#     stream_index_date = UTCDateTimeAttribute(range_key=True)
#
#
# class PhotoDateIndex(LocalSecondaryIndex):
#     class Meta(LocalPicTourneyMeta):
#         index_name = LocalPicTourneyMeta.base_name + 'photo_date_index'
#         projection = AllProjection()
#
#     gender_location = UnicodeAttribute(hash_key=True)
#     post_date = UTCDateTimeAttribute(range_key=True)


# #This snippet is for testing the binary-attribute index issue.
# #temp, move or remove
# def test_photo():
#     from uuid import uuid1
#     from datetime import timedelta
#     for x in xrange(10):
#         photo_uuid = uuid1()
#         p = Photo(u'm310', photo_uuid)
#         p.is_gender_male = True
#         p.location = u'310'
#         p.post_date = datetime.now() - timedelta(hours=x)
#         p.user_uuid = uuid1()
#         #p.sigma = 2.2
#         p.foo = b'1'
#         p.save()
#     print "saved, getting index on %s" % photo_uuid
#     for item in Photo.stream_index.query(u'm310', limit=3, post_date__lt=datetime.now() - timedelta(hours=6)):#photo_uuid):
#         print("Item queried from index: {0}".format(item))
#     print "that's all the queries on photo_uuid, here's direct hash"
#     print Photo.get(u'm310', photo_uuid)
#     print "done"
# #end temp

#------
# we still need to do input grooming validation, this is what we had.
# from __future__ import division, absolute_import, unicode_literals
#
# from flask.ext.restful import fields
# from onctuous import Schema, Optional, Required
# from onctuous.validators import Any
#
# class UUIDField(fields.Raw):
#     def __init__(self, *args, **kwargs):
#         self._format = kwargs.pop('format', 'hex')
#         super(UUIDField, self).__init__(*args, **kwargs)
#
#     def format(self, value):
#         try:
#             if self._format == 'bytes':
#                 return value.bytes
#             elif self._format == 'hex':
#                 return value.hex
#             else:
#                 return str(value)
#         except AttributeError as ae:
#             raise fields.MarshallingException(ae)
#
# # Serialization
#
# upload_complete_fields = {
#     'success': fields.String
# }
#
# user_created_fields = {
#     'success': fields.Boolean,
#     'uuid': fields.String,  # XXX make into UUIDField  (change input appropriately)
#     'token': fields.String
# }
#
# user_info_fields = {
#     'user': fields.Boolean,
#     'uuid': fields.String,  # XXX make into UUIDField (change input appropriately)
#     'name': fields.String
# }
#
# create_photo_fields = {
# 	'success': fields.Boolean,
# 	'photo_id': fields.String  # XXX make into UUIDField (change input appropriately)
# }
#
# photo_fields = {
#     'gender': fields.Integer,
#     'areacode': fields.Integer,
#     'uuid': fields.String  # XXX make into UUIDField (change input appropriately)
# }
#
# photo_data_fields = {
#     'photos': [fields.String]
# }
#
#--- some notes from tools.py
# def print_all_users():
#     user_iterator = model.User.scan()
#     for user in user_iterator:
#         print user.name
#
# def print_all_photos():
#     photo_iterator = model.Photo.scan()
#     for photo in photo_iterator:
#         # if (photo.live == True):
#         print photo.uuid
#
# def delete_photos():
#     uuids = ['0f7374c8-8dd2-11e3-bd4f-0647910162bc', 'f83bbd28-8ba2-11e3-9473-0647910162bc']
#     for u_str in uuids:
#         u = uuid.UUID(u_str)
#         photo_by_uuid = model.PhotoByUUID.get(u)
#         photo = model.Photo.get(photo_by_uuid.gender_and_areacode, range_key_value=u)
#         photo.delete()
#         photo_by_uuid.delete()
#
# def get_feed():
#     for _ in range(5):
# 	    rand = str(uuid.uuid4())
#         photo_iterator = model.Photo.query(hash_key_value=10310, limit=1, range_key_condition=GT(rand))
#         for photo in photo_iterator:
#             if (photo.live == True):
#                 print photo.uuid
# s3

# def copy(photo_uuid):
#     upload_bucket = _get_upload_bucket()
#     #image_bucket = _get_image_bucket()
#
#     k = boto.s3.key.Key(upload_bucket)
#     k.key = str(photo_uuid)
#     new_key = k.copy(IMAGE_BUCKET_NAME, str(photo_uuid))  # ? metadata ??? validate_dst_bucket=False
#     new_key.set_acl('public-read')
#     return new_key
#
# def _get_s3_connection():
#     return boto.connect_s3()
#
# def _get_upload_bucket():
#     conn = _get_s3_connection()
#     return conn.get_bucket(UPLOAD_BUCKET_NAME)
#
# def _get_image_bucket():
#     conn = _get_s3_connection()
#     return conn.get_bucket(IMAGE_BUCKET_NAME)

# def create_s3_bucket():
#     # Currently we do this on the AWS console.
#     raise NotImplemented
#     import boto
#     from boto.s3.connection import Location
#     c = boto.connect_s3()
#     bucket = c.create_bucket(S3_SERVE_BUCKET_NAME, location=Location.USWest)
#     bucket = c.create_bucket(S3_INCOMING_BUCKET_NAME, location=Location.USWest)
#
#     # # create a CORS (cross origin resource sharing) configuration and
#     # # associate it with a bucket:
#     # from boto.s3.cors import CORSConfiguration
#     # cors_cfg = CORSConfiguration()
#     # cors_cfg.add_rule(['PUT', 'POST', 'DELETE'], 'https://www.example.com', allowed_header='*', max_age_seconds=3000, expose_header='x-amz-server-side-encryption')
#     # cors_cfg.add_rule('GET', '*')
#     # # The above code creates a CORS configuration object with two rules.
#     #
#     # # The first rule allows cross-origin PUT, POST, and DELETE requests from
#     # # the https://www.example.com/ origin. The rule also allows all headers in
#     # # preflight OPTIONS request through the Access-Control-Request-Headers
#     # # header. In response to any preflight OPTIONS request, Amazon S3 will
#     # # return any requested headers.
#     # # The second rule allows cross-origin GET requests from all origins.
#     # # To associate this configuration with a bucket:
#     #
#     # c = boto.connect_s3()
#     # bucket = c.lookup(S3_BUCKET_NAME)
#     # bucket.set_cors(cors_cfg)
#
# #    bucket = c.get_bucket('my-bucket')
#  #   key = boto.s3.key.Key(bucket, 'my-big-file.gz')
#   #  signed_url = key.generate_url(60 * 60, 'POST')  # expires in an hour
#---
# def s3_copy(user, photo_uuid, check_user=True, set_live_on_completion=True):
#     photo = model.Photo.uuid_index.query(photo_uuid)
#     photo = photo.next()
#
#     photo_uuid_str = photo_uuid.hex
#
#
#     if not photo:
#         print 'Trying to load non-existant photo %s' % photo_uuid_str
#         raise Exception('Trying to load non-existant photo %s' % photo_uuid_str)
#
#     if check_user and (photo.user_uuid != user.uuid):
#         print 'User trying to move photo for another user %s %s' % photo_uuid_str, str(user.uuid)
#         raise Exception('User trying to move photo for another user %s %s' % photo_uuid_str, str(user.uuid))
#
#     key = s3.copy(photo_uuid_str)
#
#     if key:
#         photo.copy_complete = True
#         if set_live_on_completion:
#             photo.live = True
#         photo.save()


#--

# def create_photo(user, gender, areacode):
#     hash_key = get_photo_hash_key(gender, areacode)
#
#     photo_id = uuid.uuid1()
#     post_date = datetime.datetime.utcnow()
#
#     photo = model.Photo(hash_key, uuid=photo_id,
#                         gender=gender, areacode=areacode,
#                         post_date=post_date,
#                         user_uuid=user.uuid)
#     photo.save()
# #    app.log.info(photo_id)
#     return photo_id
#
# def delete_photo(photo_uuid):
#     photo = model.Photo.uuid_index.get(uuid)
#     photo.delete()
#
#
# def get_matches(user, gender, areacode):
#     photos = []
#     photo_iterator = model.Photo.scan()
#     for photo in photo_iterator:
#         if (photo.live == True):
#             photos.append({'uuid': photo.uuid.hex, 'gender': photo.gender})
#
#     return photos

# - - - - - - - - - -

# in case we ever give timeuuid another try
# #
# def pack_timeuuid_binary(input_uuid):
#     x = bitstring.BitArray(uintbe=input_uuid.int, length=128)

# def unpack_timeuuid_binary(uintbe_16):
#     return uuid.UUID(int=bitstring.BitArray(bytes=uintbe_16).int)

#
#     # -- TimeUUID -------------------------------------------------------------
#
#     def test_timeuuid_sort(self):
#         from timeuuid import pack_timeuuid_binary, unpack_timeuuid_binary, pack_timeuuid_binary_time_only_3
#         import bitstring
#
#         def encode(x):
#             return bitstring.BitArray(uint=x, length=128).bytes
#             #return bitstring.pack(b'uintbe:128', x).bytes
#
#         new_bins = [uuid1().int for b in range(10)]
#         for b in new_bins:
#             print "UUID('%s')" % b
#         bins = new_bins
#         func = encode #pack_timeuuid_binary_time_only_3
#         print '-------------------------------------------------------'
#         if bins != sorted(bins):
#             print 'uuids were not the same when sorted'
#         binaries = [func(b) for b in bins]
#         key = {}
#         for i, b in enumerate(binaries):
#             key[b] = i
#         print 'bins %s %s' % (len(binaries), len(key))
#         if binaries != sorted(binaries):
#             print 'binaries were not the same when sorted'
#             print [key[b] for b in sorted(binaries)]
#         #return
#
#
#         t_uuid = uuid1()
#         self.assertEqual(t_uuid,
#                          unpack_timeuuid_binary(pack_timeuuid_binary(t_uuid)))
#
#         timeuuids = [uuid1() for x in xrange(1000)]
#         timeuuids_original = timeuuids[:]  # copy
#         random.shuffle(timeuuids)
#         self.assertNotEqual(timeuuids, timeuuids_original)
#         timeuuids = [pack_timeuuid_binary(x) for x in timeuuids]
#         timeuuids = sorted(timeuuids)
#         timeuuids = [unpack_timeuuid_binary(x) for x in timeuuids]
#         self.assertListEqual(timeuuids_original, timeuuids)
#
#         # In case python's comparison is not what I expect? Dynamo does a
#         # byte-by-byte comparison, which I hope this does too.
#         # for len(a) == len(b)
#         def bin_cmp(a, b, index=0):
#             a1 = ord(a[index])
#             b1 = ord(b[index])
#             if index >= len(a):
#                 return 0
#             elif a1 < b1:
#                 return -1
#             elif a1 > b1:
#                 return 1
#             else:
#                 return bin_cmp(a, b, index+1)
#
#         random.shuffle(timeuuids)
#         timeuuids = [pack_timeuuid_binary(x) for x in timeuuids]
#         timeuuids = sorted(timeuuids, cmp=bin_cmp)
#         timeuuids = [unpack_timeuuid_binary(x) for x in timeuuids]
#         self.assertListEqual(timeuuids_original, timeuuids)
#
#
#
#     # http://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_Query.html
#     # For Binary, DynamoDB treats each byte of the binary data as unsigned
#     # when it compares binary values.
#
#     def test_binary_query_3(self):
#         # In case python's comparison is not what I expect? Dynamo does a
#         # byte-by-byte comparison, which I hope this does too.
#         # for len(a) == len(b)
#         from pynamodb.models import Model
#         from pynamodb.attributes import BinaryAttribute, UnicodeAttribute, NumberAttribute
#         from model import LocalPicTourneyMeta
#         from timeuuid import pack_timeuuid_binary
#
#         hash = rand_string(20)
#
#         class Binary(Model):
#             class Meta(LocalPicTourneyMeta):
#                 table_name = LocalPicTourneyMeta.base_name + 'binary'
#
#             text = UnicodeAttribute(hash_key=True)
#             bin = NumberAttribute(range_key=True)
#             #bin = BinaryAttribute(range_key=True)
#
#         Binary.create_table(wait=True,
#                             read_capacity_units=2,
#                             write_capacity_units=2)
#
#         from bitstring import BitArray
#         from uuid import uuid1
#         binaries = [
#             uuid1().int, uuid1().int, uuid1().int
#         ]
#         key = {}
#         for i, b in enumerate(binaries):
#             key[b] = i
#         sort = sorted(binaries)
# #        print [key[b] for b in sort]
#         self.assertListEqual(binaries, sort)
#         binaries_original = binaries[:]
#         random.shuffle(binaries)
#         for b in binaries:
#             n = Binary(hash, b)
#             n.save()
#         got = [b.bin for b in Binary.query(hash)]
#         got1 = list(reversed([b.bin for b in Binary.query(hash, scan_index_forward=False)]))
#         self.assertEqual(got, got1)
#         print [key[b] for b in got]
#         self.assertSetEqual(set(binaries_original), set(got))
#         self.assertEqual(binaries_original, got)
#
#         return
#
#
#     def test_binary_query_2(self):
#         # In case python's comparison is not what I expect? Dynamo does a
#         # byte-by-byte comparison, which I hope this does too.
#         # for len(a) == len(b)
#         def bin_cmp(a, b, index=0):
#             if index >= len(a):
#                 return 0
#             a1 = ord(a[index])
#             b1 = ord(b[index])
#             if a1 < b1:
#                 return -1
#             elif a1 > b1:
#                 return 1
#             else:
#                 return bin_cmp(a, b, index+1)
#
#         from pynamodb.models import Model
#         from pynamodb.attributes import BinaryAttribute, UnicodeAttribute
#         from model import LocalPicTourneyMeta
#         from timeuuid import pack_timeuuid_binary
#
#         hash = rand_string(20)
#
#         class Binary(Model):
#             class Meta(LocalPicTourneyMeta):
#                 table_name = LocalPicTourneyMeta.base_name + 'binary'
#
#             text = UnicodeAttribute(hash_key=True)
#             bin = BinaryAttribute(range_key=True)
#
#         Binary.create_table(wait=True,
#                             read_capacity_units=2,
#                             write_capacity_units=2)
#
#         from bitstring import BitArray
#         binaries = [
#             BitArray(uintbe=0b00000000, length=8).bytes,
#             BitArray(uintbe=0b11010000, length=8).bytes,
#         ]
#         key = {}
#         for i, b in enumerate(binaries):
#             key[b] = i
#         sort = sorted(binaries, cmp=bin_cmp)
# #        print [key[b] for b in sort]
#         self.assertListEqual(binaries, sort)
#         binaries_original = binaries[:]
#         random.shuffle(binaries)
#         for b in binaries:
#             n = Binary(hash, b)
#             n.save()
#         got = [b.bin for b in Binary.query(hash)]
#         got1 = list(reversed([b.bin for b in Binary.query(hash, scan_index_forward=False)]))
#         self.assertEqual(got, got1)
#         print [key[b] for b in got]
#         self.assertSetEqual(set(binaries_original), set(got))
#         self.assertEqual(binaries_original, got)
#
#         return
#
#
#     def test_binary_query(self):
#         # In case python's comparison is not what I expect? Dynamo does a
#         # byte-by-byte comparison, which I hope this does too.
#         # for len(a) == len(b)
#         def bin_cmp(a, b, index=0):
#             if index >= len(a):
#                 return 0
#             a1 = ord(a[index])
#             b1 = ord(b[index])
#             if a1 < b1:
#                 return -1
#             elif a1 > b1:
#                 return 1
#             else:
#                 return bin_cmp(a, b, index+1)
#
#         from pynamodb.models import Model
#         from pynamodb.attributes import BinaryAttribute, UnicodeAttribute, NumberAttribute
#         from model import LocalPicTourneyMeta
#         from timeuuid import pack_timeuuid_binary
#
#         hash = rand_string(20)
#
#         class Binary(Model):
#             class Meta(LocalPicTourneyMeta):
#                 table_name = LocalPicTourneyMeta.base_name + 'binary'
#
#             text = UnicodeAttribute(hash_key=True)
#             bin = NumberAttribute(range_key=True)
#
#         Binary.create_table(wait=True,
#                             read_capacity_units=2,
#                             write_capacity_units=2)
#
#         import pytz
#         from time_uuid import TimeUUID
#         from datetime import datetime
#         uuids = [
#             TimeUUID.with_utc(datetime(1970, 1, 1, tzinfo=pytz.utc)),
#             TimeUUID.with_utc(datetime(1980, 1, 1, tzinfo=pytz.utc)),
#             TimeUUID.with_utc(datetime(1990, 1, 1, tzinfo=pytz.utc)),
#             TimeUUID.with_utc(datetime(2000, 1, 1, tzinfo=pytz.utc)),
#             TimeUUID.with_utc(datetime(2010, 1, 1, tzinfo=pytz.utc)),
#             TimeUUID.with_utc(datetime(2020, 1, 1, tzinfo=pytz.utc)),
#             TimeUUID.with_utc(datetime(2030, 1, 1, tzinfo=pytz.utc)),
#             TimeUUID.with_utc(datetime(2040, 1, 1, tzinfo=pytz.utc))
#         ]
#         uuids = [uuid1() for x in range(20)]
#         self.assertListEqual(uuids, sorted(uuids))
#         # import pprint
#         # pprint.pprint([u.int for u in uuids])
#         # print '---'
#         # pprint.pprint(sorted([u.int for u in uuids]))
#         self.assertListEqual([u.int for u in uuids], sorted([u.int for u in uuids]))
#         binaries = [u.int for u in uuids]
#         key = {}
#         for i, b in enumerate(binaries):
#             key[b] = i
#         sort = sorted(binaries)
# #        print [key[b] for b in sort]
#         self.assertListEqual(binaries, sort)
#         binaries_original = binaries[:]
#         random.shuffle(binaries)
#         for b in binaries:
#             n = Binary(hash, b)
#             n.save()
#         got = [b.bin for b in Binary.query(hash)]
#         got1 = list(reversed([b.bin for b in Binary.query(hash, scan_index_forward=False)]))
#         self.assertEqual(got, got1)
#         print [key[b] for b in got]
#         self.assertSetEqual(set(binaries_original), set(got))
#         self.assertEqual(binaries_original, got)
#
#         return
#         # binaries = [
#         #     '\x11\xe4\xf2\x8c\x00\x00\x00\x00I\x8a\x14k\x00\x00\x00\x00\xa1\xbc\xc8\xe0\xeb\x16\x05\x9b',
#         #     '\x11\xe4\xf2\x8c\x00\x00\x00\x00J\r\xe7\xcf\x00\x00\x00\x00\xac\x87\xc8\xe0\xeb\x16\x05\x9b',
#         #     '\x11\xe4\xf2\x8c\x00\x00\x00\x00Je\xbb\xc0\x00\x00\x00\x00\x86\xa9\xc8\xe0\xeb\x16\x05\x9b',
#         #     '\x11\xe4\xf2\x8c\x00\x00\x00\x00J\xb8\xbf=\x00\x00\x00\x00\x89\x84\xc8\xe0\xeb\x16\x05\x9b',
#         #     '\x11\xe4\xf2\x8c\x00\x00\x00\x00L\x8f\xfd\x02\x00\x00\x00\x00\xb4\xf4\xc8\xe0\xeb\x16\x05\x9b',
#         #     '\x11\xe4\xf2\x8c\x00\x00\x00\x00L\xe5`.\x00\x00\x00\x00\xb2\x9d\xc8\xe0\xeb\x16\x05\x9b',
#         #     '\x11\xe4\xf2\x8c\x00\x00\x00\x00MNj&\x00\x00\x00\x00\x94u\xc8\xe0\xeb\x16\x05\x9b',
#         # ]
#         from timeuuid import pack_timeuuid_binary
#         hash = rand_string(20)
#         # --- pick up from here. What if we shorten the binary string until
#         # it starts working and see if there is a certain number at which it
#         # breaks?
#         print len(pack_timeuuid_binary(uuid1()))
#         binaries = [pack_timeuuid_binary(uuid1()) for x in range(10)]
#
#         self.assertListEqual(binaries, sorted(binaries))
#         key = {}
#         for i, b in enumerate(binaries):
#             key[b] = i
#         #print 'bins %s %s' % (len(binaries), len(key))
#         binaries_original = binaries[:]
#         random.shuffle(binaries)
#         for b in binaries:
#             n = Binary(hash, b)
#             n.save()
#         got = [b.bin for b in Binary.query(hash)]
#         print [key[b] for b in got]
# #        print [key[b] for b in binaries_original]
#         #print [key[b] for b in sorted(binaries_original)]
# #        for x in range(10):
# #            print binaries_original[x]
# #            print got[x]
# #            print '----'
#
#         self.assertEqual(binaries_original, got)
#         Binary.delete_table()
#
#
#     def test_timeuuid_range_key(self):
#
#         from timeuuid import pack_timeuuid_binary, LOWEST_UUID
#         pack_timeuuid_binary(LOWEST_UUID)
#
#         user_uuid = uuid1()
#         location_uuid = uuid1()
#         gender_location = Photo.make_gender_location(True, location_uuid.hex)
#         def make_photo():
#             post_date = now()
#             photo_uuid = uuid1()
#             photo = Photo(gender_location,
#                           uuid=photo_uuid,
#                           location=la_location.uuid,
#                           file_name='%s_%s' % (gender_location, photo_uuid.hex),
#                           post_date=post_date,
#                           user_uuid=user_uuid,
#                           lat=la_geo.lat,
#                           lon=la_geo.lon,
#                           geodata=la_geo.meta)
#             return photo
#         # Make photos with sequential UUIDs but don't save them.
#         photos = [make_photo() for x in xrange(5)]
#
#         # Randomize and save in this order.
#         random.shuffle(photos)
#         [p.save() for p in photos]
#
#         # Read them back.
#         from timeuuid import LOWEST_UUID
#         loaded_photos = list(Photo.query(gender_location,
#                                          scan_index_forward=False,
#                                          uuid__gt=LOWEST_UUID))
#
#         self.assertListEqual([p.uuid for p in photos],
#                              [p.uuid for p in loaded_photos])
#         self.assertListEqual([p.uuid for p in photos],
#                              [p.uuid for p in loaded_photos])
#
#     # def temp_test(self):
#     #     import pytz
#     #     from time_uuid import TimeUUID
#     #     from datetime import datetime
#     #     from time import sleep
#     #     uuids = [
#     #         TimeUUID.with_utc(datetime(1970, 1, 1, tzinfo=pytz.utc)),
#     #         TimeUUID.with_utc(datetime(1980, 1, 1, tzinfo=pytz.utc)),
#     #         TimeUUID.with_utc(datetime(1990, 1, 1, tzinfo=pytz.utc)),
#     #         TimeUUID.with_utc(datetime(2000, 1, 1, tzinfo=pytz.utc)),
#     #         TimeUUID.with_utc(datetime(2010, 1, 1, tzinfo=pytz.utc)),
#     #         TimeUUID.with_utc(datetime(2020, 1, 1, tzinfo=pytz.utc)),
#     #         TimeUUID.with_utc(datetime(2030, 1, 1, tzinfo=pytz.utc)),
#     #         TimeUUID.with_utc(datetime(2040, 1, 1, tzinfo=pytz.utc)),
#     #         TimeUUID.with_utc(datetime(2050, 1, 1, tzinfo=pytz.utc)),
#     #     ]
#     #     shuffled_uuids = uuids[:]
#     #     random.shuffle(shuffled_uuids)
#     #     for u in shuffled_uuids:

# --- used for testing kinesis
#
# #----------
# import time
# tries = 0
# while tries < 10:
#     tries += 1
#     time.sleep(15)
#     response = kinesis.describe_stream('test')
#     if response['StreamDescription']['StreamStatus'] == 'ACTIVE':
#         shard_id = response['StreamDescription']['Shards'][0]['ShardId']
#         break
# else:
#     print('Stream is still not active, aborting...')
#     return
#
# # Make a tag.
# kinesis.add_tags_to_stream(stream_name='test', tags={'foo': 'bar'})
#
# # Check that the correct tag is there.
# response = kinesis.list_tags_for_stream(stream_name='test')
# print(len(response['Tags']) == 1)
# print(response['Tags'][0] ==
#                  {'Key':'foo', 'Value': 'bar'})
#
# # Remove the tag and ensure it is removed.
# kinesis.remove_tags_from_stream(stream_name='test', tag_keys=['foo'])
# response = kinesis.list_tags_for_stream(stream_name='test')
# self.assertEqual(len(response['Tags']), 0)
#
# # Get ready to process some data from the stream
# response = kinesis.get_shard_iterator('test', shard_id, 'TRIM_HORIZON')
# shard_iterator = response['ShardIterator']
#
# # Write some data to the stream
# data = 'Some data ...'
# record = {
#     'Data': data,
#     'PartitionKey': data,
# }
# response = kinesis.put_record('test', data, data)
# response = kinesis.put_records([record, record.copy()], 'test')
#
# Wait for the data to show up
# import score
# shard_id = score.SHARD_ID
# response = kinesis.get_shard_iterator('LocalPicTourney-dev', shard_id, 'TRIM_HORIZON')
# shard_iterator = response['ShardIterator']
# tries = 0
# num_collected = 0
# num_expected_records = 30
# collected_records = []
# while tries < 100:
#     tries += 1
#     import time
#     time.sleep(1)
#     print 'checking...'
#     response = kinesis.get_records(shard_iterator)
#     shard_iterator = response['NextShardIterator']
#     print '...got %s' % len(response['Records'])
#     for record in response['Records']:
#         if 'Data' in record:
#             collected_records.append(record['Data'])
#             num_collected += 1
#     if num_collected >= num_expected_records:
#         print(num_expected_records == num_collected)
#         break
# else:
#     print('No records found, aborting...')
#
# # Read the data, which should be the same as what we wrote
# for record in collected_records:
#     self.assertEqual(data, record)

# -Notes from how a user gets a list of Matches to judge. I'm not sure if
# they are current.

        # Get the tournament state, it'll tell you how many matches until
        # the next tournament event, and what that event is.

#"regular", "tournament_card", "tournament", and "winner_card"
# (I say it's "match" not "regular")
#         Assuming we stay with regular items (which I think we should do to
# start), we'll send shorter lists during the tournament.  So, you'll get the
# a list with, say, 8 matches, then 4, then 2, then 1 (the final).
#
# In terms of how the tournament algorithm works:
#
# First, tournaments come in levels.  Matches are cyclical.  So, the sequence
#  looks like
#
# regular (~70 matches)
# tournament -- local (~8 matches)
# regular
# tournament -- regional (~8 matches)
# regular
# tournament -- global
#
# Local tournaments are just the top 16 people locally in a standard
#  tournament bracket pattern (#1 vs # 16; #2 vs # 15, etc, going in rounds
# so #1 and #2 are most likely to meet).
#
# For the other tournaments, we randomly select a list of places that have
# enough participants.  Then, we choose a level, say #3.  So, we build a
# tournament with the #3-ranked people from 16 different cities.

#---
        # how do we do this? We need a table to hold tournament stuff, we
        # need to generate tournamenty things...
        # Each user has a "number of matches 'til next tournament"
        # and a "next tournament kind" which tells you what happens when
        # the next tournament kicks in", also we'll need to know if we are
        # in a tournament I think.
        # A tournament is generated at a certain time, if not, you may see
        # the same people in different bracket positions as you move through
        # the tournament. I think it might even be better to fix the tournament
        # daily, as in, create the tournament card per location per day.
        # likewise, the regional and global. If we generate it on each request
        # there may be fluctuations that make the bracket look wonky to
        # individal users.
        # proposal:
        # User gets fields added:
        # regular_matches_remaining = NumberAttribute  # when 0, tournament
        # tournament_type = UnicodeAttribute (local, regional, global)
        # tournament_position = UnicodeAttribute (usually a number, the bracket
        #    number or equivalent to be judged next.)
        # no no no this won't work. The tournament brackets advance per user.
        # We need to keep the record for the tournament.
        # Tournament
        # user_uuid = UUIDAttribute
        # position = UnicodeAttribute # name of next bracket pattern
        # seed = UnicodeAttribute # name of seed used to create this.
        # -- the rounds
        # one_vs_two = UnicodeAttribute # Winner uuid
        # three_vs_four =
        # ...
        # fifteen_vs_sixteen = ...
        # ---
        # one_two_vs_three_four ...
        # thirteen_fourteen_vs_fifteen_sixteen
        # --
        # one_four_vs_five_eight
        # nine_twelve_vs_thirteen_sixteen
        # --
        # (one_eight_vs_nine_sixteen)
        # winner

        # Regional and global are pending new regional rules, but it's like
        # this: you build a tournament seed given a set of regions and a rank.

        # a tournament seed looks like this
        # name = UnicodeAttribute(hash_key=true) # region name, kind, date.
        # one = UUIDAttribute()  # user uuid for seed one.
        # ...
        # sixteen = UUIDAttribute()  # user uuid for seed sixteen.
        # tournament seeds are created by a cron job so that each user sees
        # the same tournaments that day if they get one.


        #  - - - - - - -

        # Need to get feedback if I'm returning a whole tournament structure
        # or a list of matches.
#------------------
# If we show all location in JSON this would help

# leaderboard_element_model = api.model('LeaderboardList', {
#     'country': fields.String(required=True, description='Country abbreviation'),
#     'city': fields.String(required=True,
#                        description='database city name'),
#     'accent_city': fields.String(required=True,
#                              description='human readable city name'),
#     'region': fields.String(
#         required=True,
#         description="numerical region for leaderboard"),
#     'population': fields.String(
#         required=True,
#         description="number of people in city"),
#     'lat': fields.String(required=True,
#                          description="central latitude of city"),
#     'lon': fields.String(
#         required=True,
#         description="central longitude of city"),
#     'uuid': fields.String(
#         required=True,
#         description="uuid of city, get leaderboards with /leaderboards/m<uuid> or /leaderboards/f<uuid>")
# })

# - - - - - - - -
# notes on "leaderboard filters"
#- - - - - - - - -
# Need daily, weekly, monthy and alltime score.
# Can't filter b/c when alltime gets large, daily might be quite buried.
# Want to do index b/c when score is updated, want daily, weekly, monthly to
#     change.
# Global Secondary Indexes - what would the hash-key be?
#     when+gender_location, score --- no, can't set a "when" on write, "when"
#     membership changes as time passes.
# Table when, hash gender_location, range score. holds uuid.
# Each day cron job scans table, deletes out-dated elements.
# Won't scale when cron job takes too long, but might scale for a long time,
# could partition the scan.
# This means any time score changes, need to update score in these other
#     tables. That each of these tables needs a by_uuid index for lookups.
# No other info is denormalized into these tables.
# alltime is still looked up in the main table.