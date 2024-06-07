from __future__ import division, absolute_import, unicode_literals



# We should be able to start a new independent deployment of the api from
# a single command.

import logging
import os
import os.path

import boto

log = logging.getLogger(__name__)
S3_CONNECTION = None

def get_s3_connection():
    global S3_CONNECTION
    if S3_CONNECTION is None:
        S3_CONNECTION = boto.connect_s3()
    return S3_CONNECTION


def clone_api():
    if os.path.exists("./localpictourney-api"):
        if not os.path.isdir("./localpictourney-api"):
            log.error("./localpictourney-api exists but is not a directory.")
            exit(1)
        # ./localpictourney-api exists, so just update it.
        os.chdir("./localpictourney-api")
    pass


def clone_tester():
    pass


def new_api(name):
    """Provision and deploy a new LocalPicTourney API installation'"""

    # git clone localpictourney-api somewhere if we haven't already.
    # eb create <name> as worker, make it create its own sqs queue.
    # Make new s3 buckets for uploading and serving photos.
    conn = get_s3_connection()
    conn.create_bucket('%s-inbox' % name)
    conn.create_bucket('%s-serve' % name)
    # --s3 bucket to put a message on worker's sqs queue on upload.
    # eb create <name> as wsgi app
    # eb deploy
    pass

def new_loadtester(name):
    """Provision and deploy a new LocalPicTourney Tester installation."""
    # git clone
    pass

def run_loadtester(api_name, tester_name):
    """Run a deployed loadtester against a deployed API and print results."""
    pass

def test(name):
    """Provision an api, loadtester, run unit and load tests, report, drop."""
    pass