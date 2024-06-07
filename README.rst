LocalPicTourney
===============

Open Source Note
----------------

This was a private application backend whose original backers have graciously
authorized me to make public so that I may demonstrate my work and perhaps
enrich the world with any of these contents. The application name has been 
changed and the seed data is not included. 

The original idea was for users to upload pictures which would be entered into 
a tournament with other pictures from the same category. While the obvious use 
of comparing photogenic humans is baked in to the system, there were categories 
for landscapes, architecture, and cars among other topics. A user could request
to judge a tournament of 16 photos, 2-at-a-time, and the app would run the
results through the Glicko2 scoring algorithm, resulting in each photo having
its score slightly increased or decreased. Thus Photo posters could compete to 
have their photo be the highest scored photos in each category, and everybody
could view the category to see the highest scored photos.

I had made many backends over the years and this may be my most recent 
example using the many tools/services I had learned until then. I made a 
platform I called 'Ocean' to create some architectural separation from this
application 'LocalPicTourney'. 

Note that it uses Terraform, and many services like SQS, Kinesis, DynamoDB, a 
PostgresQL location DB and AWS signed URLs for users to upload photos and 
videos.
(End of Open Source Note)

LocalPicTourney
---------------

This is the API and backend for the LocalPicTourney mobile app. It runs on AWS. The
web server and worker are Flask (Python) WSGI applications deployed to AWS
using Elastic Beanstalk. (see docs/Deployment.rst)

Note: The API is configured to put all users in the Los Angeles area, and have
them all post as if they were female, and view as if they viewed females.

There are helpful diagnostic endpoints to test fetaures like the location DB
and iOS push, see /test_location_db and /test_push (for example.)

Features
--------
API server: Swagger docs, 'share' endpoints, admin system, reports errors to
Sentry, statsd, SNS (iOS push), http auth, facebook integration, photo/movie
upload to s3 with signed URL.

Worker: Glicko2 Scoring. Leaderboard updates. Photo and movie post processing.

Location DB: Postgres database to map lat/lon to a named location.

Search DB: Postgres database to do simple text-matching tag search.

Kinesis: Use kinesis to batch-up operations for the worker to process in a
group.

DynamoDB: Primary record storage is DynamoDB.

s3: Photos are uploaded and served from s3.

Deployment: Uses Terraform to deploy to AWS Elastic Beanstalk (auto scaling)

Development
-----------

You will need valid AWS credentials, even if only to run locally. They are kept
in ~/.aws/config and/or ~/.aws/credentials (I have both and not sure if both
 are necessary.)

To run locally, you'll need the DynamoDB local test tool dynalite
dynalite --port 8000

LocalPicTourney uses virtualenv, it has a requirements.txt

The unit tests in localpictourney_tests.py should be run using the environment variable
OCEAN_SETTINGS = unit_test_server

Ocean
-----

LocalPicTourneyPrime was forked to make LocalPicTourney. The name 'Ocean' is 
intented as an project
neutral term to refer to the project. If the concepts ever became a library
that would be the name. For now it serves to share functions across the
projects. Project-neutral improvements are cherry-picked across the projects.

Settings
--------

When the Flask applications run they use configuration 'settings', they are
found in ./settings.

Any setting can be set using environment variables. A setting is first looked
up in the environment, with the prefix 'OCEAN__' (ex: OCEAN__LOCATION_DB_HOST),
next the settings file with the name given in OCEAN_SETTINGS environment
variable is checked.

Status
------

Active development was put down October 2016 pending further design of the
mobile app. The most recent feature is support of video files and adding
'media_type' to the Photo records.

TODO list
---------

* logic.provision - make a reserverd place for managing provision errors.
* look at how emqttd can work for localpictourney and see if emqttd is only plugins
for build or did top level build change?
* make sentry event when health check fails
* Do not include Sentry data in 'prod' errors
* need better info in sentry, should come from flask request info, need to
know URL requested, user, etc.
* (Ocean) Read the cron yml and call all the endpoints in test.
* (Ocean) move the api blueprint to a versioned folder
* LocalPicTourneyLocalPicTourney port - localpictourney needs no-dupes, then dupes, in get matches
* APNS may have changed https://developer.apple.com/library/content/documentation/NetworkingInternet/Conceptual/RemoteNotificationsPG/Chapters/ApplePushService.html
* can use unlink instead of delete for operating on photo in worker?
* Confirm the sunset of certain ec2 features wont effect us.


