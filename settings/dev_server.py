from __future__ import division, absolute_import, unicode_literals



MAJOR_VERSION = 0
MINOR_VERSION = 2
MICRO_VERSION = 1
VERSION = '{}.{}.{}'.format(MAJOR_VERSION, MINOR_VERSION, MICRO_VERSION)
URL_PREFIX = '/v{}.{}'.format(MAJOR_VERSION, MINOR_VERSION)
NAME = 'LocalPicTourney-0.2'
MODE = 'dev'  # dev, prod, unit-test, load-test.
NAME_PREFIX = '{}-{}-'.format(NAME.lower(), MODE.lower())

# I'd like to set this with Terraform, but they come from the object I'm
# creating. If possible I could get it from the load balancer?
URL = 'localpictourney-0-2-api-dev.23rdm7ngb3.us-west-2.elasticbeanstalk.com'
SHARE_URL = 'localpictourney-0-2-api-dev.23rdm7ngb3.us-west-2.elasticbeanstalk.com'

IS_WORKER = False

DYNAMO_DB_REGION = 'us-west-2'
DYNAMO_DB_HOST = None

S3_ENABLED = True
# S3_INCOMING_BUCKET_NAME -- set in env by terraform
# S3_SERVE_BUCKET_NAME -- set in env by terraform
# SERVE_BUCKET_URL -- set in env by terraform

# Listed in elastic beanstalk console. go to worker, then click 'view queue'
SQS_ENABLED = True
# SQS_QUEUE_NAME -- set in env by terraform

SNS_ENABLED = False
SNS_APPLICATION_ARN = 'arn:aws:sns:us-west-2:000841753196:app/APNS_SANDBOX/LocalPicTourney-APNS-dev'

KINESIS_ENABLED = True
# SCORE_STREAM -- set in env by terraform
# TAG_TREND_STREAM -- set in env by terraform

LOCATION_DB_ENABLED = True
LOCATION_DB_NAME ='loc'
LOCATION_DB_USER ='awsuser'
LOCATION_DB_PASSWORD ='secretpassword'
LOCATION_DB_HOST ='localpictourneylocationdev.cocmaif1j3d7.us-west-2.rds.amazonaws.com'
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
#SENTRY_DSN = 'https://codegoeshere@app.getsentry.com/12345'

DATADOG_API_KEY = '123456'
DATADOG_APP_KEY = '1234567890'
