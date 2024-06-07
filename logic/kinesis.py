from __future__ import division, absolute_import, unicode_literals


from datetime import timedelta
from time import sleep

import boto
import boto.kinesis
from boto.kinesis.exceptions import InvalidArgumentException, \
    ProvisionedThroughputExceededException
from botocore.vendored.requests.exceptions import ConnectionError
from pynamodb.models import DoesNotExist

from log import log
from model import ShardIterator
from logic import sentry
from settings import settings
from util import now


CONNECTION = None

def setup_connection():
    global CONNECTION
    if settings.KINESIS_ENABLED:
        CONNECTION = boto.kinesis.connect_to_region('us-west-2')
    else:
        log.info("kinesis using MOCK connection")
        CONNECTION = MockKinesis()

def get_kinesis():
    if CONNECTION is None:
        setup_connection()
    return CONNECTION


class MockKinesis(object):
    def __init__(self):
        self.records = {}
        self.shard_iterators = {}

    n = 49550782119036890694910890427288626268549652974252589058
    def put_record(self, stream, data, partition_key):
        log.info('kinesis put_record is MOCK')
        record = {u'PartitionKey': partition_key,
                  u'Data': data,
                  u'SequenceNumber': str(self.n)}
        if stream not in self.records:
            self.records[stream] = []
        self.records[stream].append(record)

    def reset(self, stream):
        try:
            del self.records[stream]
        except KeyError:
            pass

    def describe_stream(self, stream):
        return {
u'StreamDescription': {
    u'HasMoreShards': False,
    u'StreamStatus': u'ACTIVE',
    u'StreamName': stream,
    u'StreamARN': u'arn:aws:kinesis:us-west-2:000841753196:stream/{}'.format(stream),
    u'Shards': [
        {u'HashKeyRange': {
            u'EndingHashKey': u'340282366920938463463374607431768211455',
            u'StartingHashKey': u'0'
        },
        u'ShardId': u'shardId-000000000000',
        u'SequenceNumberRange': {
            u'StartingSequenceNumber': u'49550782119036890694910889893090902948876102227749502978'
}}]}}

    def get_shard_iterator(self, stream, shard_id, shard_iterator_type, starting_sequence_number=None):
        shard_iterator = 'sharditer-%s-%s-%s' % (stream, shard_id,
                                                 shard_iterator_type)
        self.shard_iterators[shard_iterator] = stream
        return {'ShardIterator': shard_iterator}

    def get_records(self, shard_iterator):
        stream = self.shard_iterators[shard_iterator]
        try:
            records = self.records[stream]
        except KeyError:
            records = []
        self.records[stream] = []
        return {u'Records': records, u'NextShardIterator': shard_iterator}

def get_all(stream):
    """Iterator that gets all the messages pending on a given stream."""
    try:
        log.info("kinesis get_all(stream=%s)", stream)
        connection = get_kinesis()

        count = 0
        response = connection.describe_stream(stream)
        if response['StreamDescription']['StreamStatus'] != 'ACTIVE':
            log.error("Stream '{}' not active, get_all abort.".format(stream))
            return
        else:
            shards = response['StreamDescription']['Shards']
            log.info("{} shard count {}".format(stream, len(shards)))
            for shard in shards:
                shard_id = shard['ShardId']
                stream_and_shard_id = '%s%s' % (stream, shard_id)
                log.info("get_all reading %s", stream_and_shard_id)
                # Do we already have a record for this?
                for i in range(10):
                    log.info("getting ShardIterator")
                    try:
                        shard_iter = ShardIterator.get(stream_and_shard_id,
                                                       consistent_read=False)
                        break
                    except ConnectionError:
                        log.warn("Got ConnectionError getting ShardIterator")
                        sleep(i)
                    except DoesNotExist:
                        log.info("Creating ShardIterator")
                        shard_iter = ShardIterator(stream_and_shard_id)
                        log.info("saving ShardIterator")
                        shard_iter.save()
                        log.info("sleeping {}".format(i))
                        sleep(i)
                else:
                    log.error("Could not find or create ShardIterator %s",
                                 stream_and_shard_id)
                    continue
                log.info("getting sequence number")
                sequence_number = shard_iter.sequence_number
                log.info("sequence number {}".format(sequence_number))
                if sequence_number:
                    # We got this error after some early Terraform work:
                    # InvalidArgumentException: InvalidArgumentException: 400
                    # Bad Request
                    # StartingSequenceNumber 49561732024706981605478962100984641178096952071497449474
                    #     used in GetShardIterator on shard shardId-000000000000
                    #     in stream LocalPicTourney-dev-kinesis-stream under account
                    #     000841753196 is invalid because it did not come from
                    #     this stream.'
                    # I'm assuming it is a terraform related issue, where
                    # the sequence number we are keeping is from another
                    # deploy. So, when this happens we will log the issue and
                    # reset.
                    try:
                        response = connection.get_shard_iterator(
                                stream, shard_id, 'AFTER_SEQUENCE_NUMBER',
                                sequence_number)
                    except InvalidArgumentException as ex:
                        log.info('--debug-- in InvalidArgumentException, kinesis.py')
                        log.exception(ex)
                        msg_fragment = 'invalid because it did not come from this stream.'
                        if msg_fragment in ex.message:
                            msg = 'kinesis.py attempting to get shard iterator, had error. Resetting shard iterator.'
                            log.warn(msg)
                            sentry.get_client().captureMessage(msg)
                        shard_iter.sequence_number = None
                        shard_iter.save()
                        response = connection.get_shard_iterator(stream,
                                                                    shard_id,
                                                                    'TRIM_HORIZON')
                else:
                    response = connection.get_shard_iterator(stream,
                                                                shard_id,
                                                                'TRIM_HORIZON')

                shard_iterator = response['ShardIterator']
                log.info("shard iterator {}".format(shard_iterator))

                start = now()
                flag = True
                while flag:
                    try:
                        response = connection.get_records(shard_iterator)
                    except ProvisionedThroughputExceededException:
                        log.warn('kinesis get_records ProvisionThroughputExceededException, skipping')
                        sleep(1.0)
                        continue
                    shard_iterator = response['NextShardIterator']
                    records = response['Records']
                    if not records:
                        flag = False
                        continue
                    for record in records:
                        data = record['Data']
                        sequence_number = record['SequenceNumber']
                        yield data
                        count += 1
                    if now() - start > timedelta(minutes=3):
                        flag = False
                shard_iter.sequence_number = sequence_number
                shard_iter.save()

        log.info("get_all complete with count %s", count)
        return
    except Exception as e:
        log.error("get_all had Exception %s", e)
        log.exception(e)
        raise

def assert_connection():
    connection = get_kinesis()
    connection.describe_stream(settings.SCORE_STREAM)
    connection.describe_stream(settings.TAG_TREND_STREAM)
    return True