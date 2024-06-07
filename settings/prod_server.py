from __future__ import division, absolute_import, unicode_literals



MAJOR_VERSION = 0
MINOR_VERSION = 1
MICRO_VERSION = 1
VERSION = '{}.{}.{}'.format(MAJOR_VERSION, MINOR_VERSION, MICRO_VERSION)
URL_PREFIX = '/v{}.{}'.format(MAJOR_VERSION, MINOR_VERSION)
NAME = 'LocalPicTourney'
MODE = 'prod'  # dev, prod, unit-test, load-test.
NAME_PREFIX = '{}-{}-'.format(NAME.lower(), MODE.lower())

URL = 'localpictourney.elasticbeanstalk.com'

IS_WORKER = False

URL = 'xxx fix settings'
SHARE_URL = 'xxx fix settings'

DYNAMO_DB_REGION = 'us-west-2'
DYNAMO_DB_HOST = None

S3_ENABLED = True
S3_INCOMING_BUCKET_NAME = 'localpictourney-inbox'
S3_SERVE_BUCKET_NAME = 'localpictourney-serve'
SERVE_BUCKET_URL = 'https://s3-us-west-2.amazonaws.com/localpictourney-serve'

# Listed in elastic beanstalk console. go to worker, then click 'view queue'
SQS_ENABLED = True
# SQS_QUEUE_NAME -- set in env by terraform

SNS_ENABLED = False
SNS_APPLICATION_ARN = 'arn:aws:sns:us-west-2:123456788:app/APNS_SANDBOX/LocalPicTourney-APNS-dev'

KINESIS_ENABLED = True
# SCORE_STREAM -- set in env by terraform
# TAG_TREND_STREAM -- set in env by terraform

LOCATION_DB_ENABLED = True
LOCATION_DB_NAME ='loc'
LOCATION_DB_USER ='awsuser'
LOCATION_DB_PASSWORD ='secretpassword'
LOCATION_DB_HOST ='localpictourneylocationdev.xxxxxxx.us-west-2.rds.amazonaws.com'
LOCATION_DB_PORT ='5432'

# Directory for photo processing
PHOTO_DIR = '/var/ocean/photos/'
PHOTO_CROP_ENABLED = True

FACEBOOK_ENABLED = True

STATSD_ENABLED = False
STATSD_HOST = 'localhost'
STATSD_PORT = 8125
STATS_LOG = False

SENTRY_ENABLED = True
# set in env by terraform
#SENTRY_DSN = 'https://somecode:somecode@app.getsentry.com/59918'

DATADOG_API_KEY = '12345'
DATADOG_APP_KEY = '123456789'