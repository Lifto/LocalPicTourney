from __future__ import division, absolute_import, unicode_literals



from log import log
from settings import settings

import boto
import boto.s3.key

S3_CONNECTION = None
INCOMING_BUCKET = None
SERVE_BUCKET = None

class MockS3Connection(object):
    def __init__(self):
        self.messages = []

    def build_post_form_args(self, incoming_bucket_name, file_name,
                             http_method='https',
                             max_content_length=1000000):
        return {
            'action': 'http://%s.s3.amazonaws.com/' % incoming_bucket_name,
            'fields': [
                 {'name': 'x-amz-storage-class', 'value': 'STANDARD'},
                 {'name': 'policy',
                  'value': 'aoeutshaoesuthaosetuhasoetuhasoteuhsatoheu='},
                 {'name': 'AWSAccessKeyId', 'value': 'aoesuthasoeuthasoetuh'},
                 {'name': 'signature', 'value': u'aoesuthasoeuthasoetuh='},
                 {'name': 'key', 'value': file_name}]}


def setup_s3_connection():
    global S3_CONNECTION, INCOMING_BUCKET, SERVE_BUCKET
    if settings.S3_ENABLED:
        S3_CONNECTION = boto.connect_s3()
        INCOMING_BUCKET = S3_CONNECTION.get_bucket(
            settings.S3_INCOMING_BUCKET_NAME)
        SERVE_BUCKET = S3_CONNECTION.get_bucket(settings.S3_SERVE_BUCKET_NAME)
    else:
        S3_CONNECTION = MockS3Connection()
        INCOMING_BUCKET = MockS3IncomingBucket()
        SERVE_BUCKET = "mock_s3_serve_bucket"


class MockS3IncomingBucket(object):
    def get_key(self, key_name):
        return MockS3Key()


class MockS3Key(object):
    def get_contents_to_filename(self, path):
        pass


def get_s3_connection():
    if S3_CONNECTION is None:
        setup_s3_connection()
    return S3_CONNECTION

def get_incoming_bucket():
    if INCOMING_BUCKET is None:
        setup_s3_connection()
    return INCOMING_BUCKET

def get_serve_bucket():
    if SERVE_BUCKET is None:
        setup_s3_connection()
    return SERVE_BUCKET

def assert_connection():
    if S3_CONNECTION is None:
        setup_s3_connection()
    S3_CONNECTION.get_bucket(settings.S3_INCOMING_BUCKET_NAME)
    S3_CONNECTION.get_bucket(settings.S3_SERVE_BUCKET_NAME)
    return True