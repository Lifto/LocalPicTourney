from __future__ import division, absolute_import, unicode_literals



import json

from log import log
from settings import settings

import boto.sqs
from boto.sqs.message import Message

SQS_CONNECTION = None
SQS_QUEUE = None


class MockSQSQueue(object):
    def __init__(self):
        self.messages = []

    def write(self, message):
        self.messages.append(message)

# Note, we want to connect to the SQS queue that our worker created for itself.
def setup_sqs_connection():
    global SQS_CONNECTION, SQS_QUEUE
    if settings.SQS_ENABLED:
        log.info("Connecting to SQS")
        SQS_CONNECTION = boto.sqs.connect_to_region('us-west-2')
        log.info("Connecting to queue %s" % settings.SQS_QUEUE_NAME)
        SQS_QUEUE = SQS_CONNECTION.get_queue(settings.SQS_QUEUE_NAME)
        log.info("SQS queue '{}' ready".format(SQS_QUEUE))
    else:
        log.info("Using mock SQS")
        SQS_CONNECTION = "mock_sqs_connection"
        SQS_QUEUE = MockSQSQueue()
        log.info("mock SQS queue ready")

def get_sqs_connection():
    if SQS_CONNECTION is None:
        setup_sqs_connection()
    return SQS_CONNECTION

def get_queue():
    if SQS_QUEUE is None:
        setup_sqs_connection()
    return SQS_QUEUE

# Helper functions to call known worker functions asynchronously.
def get_facebook_photo(user_uuid, force_set_as_profile_photo):
    body = json.dumps({'user_uuid': user_uuid.hex,
                       'force_set_as_profile_photo': force_set_as_profile_photo,
                       't': 'facebook_registration'})
    message = Message()
    message.set_body(body)
    # This will get read by the '/worker_callback' method in worker.py
    get_queue().write(message)

def assert_connection():
    if SQS_QUEUE is None:
        setup_sqs_connection()
    SQS_CONNECTION.get_queue(settings.SQS_QUEUE_NAME)
    return True
