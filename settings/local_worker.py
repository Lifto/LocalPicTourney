from __future__ import division, absolute_import, unicode_literals



MAJOR_VERSION = 0
MINOR_VERSION = 1
MICRO_VERSION = 1
VERSION = '{}.{}.{}'.format(MAJOR_VERSION, MINOR_VERSION, MICRO_VERSION)
URL_PREFIX = '/v{}.{}'.format(MAJOR_VERSION, MINOR_VERSION)
NAME = 'LocalPicTourney'
MODE = 'local'  # dev, prod, unit-test, load-test.
NAME_PREFIX = '{}-{}-'.format(NAME.lower(), MODE.lower())

URL = 'localhost'

IS_WORKER = True

URL = 'localhost:8000'
SHARE_URL = 'localhost:8000'

DYNAMO_DB_REGION = None
DYNAMO_DB_HOST = 'http://localhost:8000'

S3_ENABLED = False
S3_INCOMING_BUCKET_NAME = 'localpictourney-inbox'
S3_SERVE_BUCKET_NAME = 'localpictourney-serve'
SERVE_BUCKET_URL = 'https://s3-us-west-2.amazonaws.com/localpictourney-serve'

# Listed in elastic beanstalk console. go to worker, then click 'view queue'
SQS_ENABLED = False
SQS_QUEUE_NAME = 'awseb-e-xxxxxx-stack-AWSEBWorkerQueue-xxxxxxx'

SNS_ENABLED = False
SNS_APPLICATION_ARN = 'arn:aws:sns:us-west-2:123455:app/APNS_SANDBOX/LocalPicTourney-APNS-dev'

KINESIS_ENABLED = False
#SCORE_STREAM = "{}-{}-kinesis-stream".format(NAME, MODE)
# TAG_TREND_STREAM

LOCATION_DB_ENABLED = False
LOCATION_DB_NAME ='loc'
LOCATION_DB_USER ='awsuser'
LOCATION_DB_PASSWORD ='secretpassword'
LOCATION_DB_HOST ='localpictourneylocationdev.cocmaif1j3d7.us-west-2.rds.amazonaws.com'
LOCATION_DB_PORT ='5432'

# Directory for photo processing
PHOTO_DIR = '/var/ocean/photos/'
PHOTO_CROP_ENABLED = False

FACEBOOK_ENABLED = False

STATSD_ENABLED = False
STATSD_HOST = 'localhost'
STATSD_PORT = 8125
STATS_LOG = False

SENTRY_ENABLED = False
SENTRY_DSN = ''

DATADOG_API_KEY = '123456'
DATADOG_APP_KEY = '123456789'