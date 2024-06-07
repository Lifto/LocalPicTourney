# LocalPicTourney

provider "aws" {
  access_key = "${var.access_key}"
  secret_key = "${var.secret_key}"
  region = "${var.region}"
}

# -- Security -----------------------------------------------------------------

// Note: I can't seem to set the 'platform' security role, if we set it in
// terraform it is as if it is unset (and does not appear in the console) and
// health monitoring is blocked. In the console you can select the role, and
// when a drop-down is shown the only choice you have is the role created when
// we (I assume) started using Elastic Beanstalk.
// A "by-hand" issue is that the location and search dbs must be initialized
// see docs/Deployment.rst

// general_access_policy gives all access to dynamodb, s3, datapipeline (for
// s3 notification events, I think), SQS and Kinesis on all resources.
// TODO: Implement "Least Privilege" security practice.
resource "aws_iam_role_policy" "general_access_policy" {
    name = "${var.ocean["name"]}-${var.ocean["settings"]}-general-access-policy"
    role = "${aws_iam_role.ec2_role.id}"
    policy = <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Action": [
                "dynamodb:*",
                "s3:*",
                "datapipeline:*",
                "sqs:*",
                "kinesis:*"
            ],
            "Effect": "Allow",
            "Resource": [
                "*"
            ]
        },
        {
            "Sid": "MetricsAccess",
            "Action": [
                "cloudwatch:PutMetricData"
            ],
            "Effect": "Allow",
            "Resource": "*"
        }
    ]
}
EOF
}

// SNS Access Policy - It's not clear why this is separate from the general
// access policy.
resource "aws_iam_role_policy" "sns_access_policy" {
    name = "${var.ocean["name"]}-${var.ocean["settings"]}-sns-access-policy"
    role = "${aws_iam_role.ec2_role.id}"
    policy = <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "Stmt14512345678",
            "Effect": "Allow",
            "Action": [
                "sns:*"
            ],
            "Resource": [
                "arn:aws:sns:us-west-2:123456:app/APNS_SANDBOX/LocalPicTourney-APNS-dev"
            ]
        }
    ]
}
EOF
}

resource "aws_iam_role" "ec2_role" {
    name = "${var.ocean["name"]}-${var.ocean["settings"]}-ec2-tf-role"
    assume_role_policy = <<EOF
{
  "Version": "2008-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
}

resource "aws_iam_instance_profile" "api_server_profile" {
    name = "${var.ocean["name"]}-${var.ocean["settings"]}-api-profile"
    roles = ["${aws_iam_role.ec2_role.name}"]
}

resource "aws_iam_instance_profile" "worker_profile" {
    name = "${var.ocean["name"]}-${var.ocean["settings"]}-worker-profile"
    roles = ["${aws_iam_role.ec2_role.name}"]
}

# -- RDS ----------------------------------------------------------------------

# Notes from the console:
# The 'security group' section says Your account does not support the
# EC2-Classic Platform in this region. DB Security Groups are only needed when
# the EC2-Classic Platform is supported. Instead, use VPC Security Groups to
# control access to your DB Instances. Go to the EC2 Console to view and
# manage your VPC Security Groups. For more information, see AWS Documentation
# on Supported Platforms and Using RDS in VPC.

resource "aws_security_group" "db_authorized_sg" {
  name = "${replace(var.ocean["name"], "-", "_")}_${replace(var.ocean["settings"], "-", "_")}_db_authorized_security_group"
  description = "Attach this to your EB Environment to get DB access."
}

resource "aws_security_group" "db_security_group" {
  name = "${replace(var.ocean["name"], "-", "_")}_${replace(var.ocean["settings"], "-", "_")}_db_security_group"
  description = "Allow Elastic Beanstalk Environments to access location and search databases."

  ingress {
      from_port = 5432
      to_port = 5432
      protocol = "tcp"
      security_groups = ["${aws_security_group.db_authorized_sg.id}"]
  }

  egress {
      from_port = 0
      to_port = 0
      protocol = "-1"
      cidr_blocks = ["0.0.0.0/0"]
  }
}

// Note: search DB and location DB are same DB at the moment.
resource "aws_db_instance" "search_db" {
  identifier = "${lower(var.ocean["name"])}-${lower(var.ocean["settings"])}-search-db-rds"
  allocated_storage = "10"
  engine = "postgres"
  engine_version = "9.4.1"
  instance_class = "db.t2.micro"
  username = "awsuser"
  password = "secretpassword"
  vpc_security_group_ids = ["${aws_security_group.db_security_group.id}"]
}

# -- Kinesis ------------------------------------------------------------------

resource "aws_kinesis_stream" "score_stream" {
  name = "${var.ocean["name"]}-${var.ocean["settings"]}-score-stream"
  shard_count = 1
  retention_period = 24
}

resource "aws_kinesis_stream" "tag_trend_stream" {
  name = "${var.ocean["name"]}-${var.ocean["settings"]}-tag-trend-stream"
  shard_count = 1
  retention_period = 24
}

# -- SQS ----------------------------------------------------------------------

resource "aws_sqs_queue" "dead_letter_queue" {
  name = "${var.ocean["name"]}-${var.ocean["settings"]}-worker-sqs-dead-letter"
  delay_seconds = 0
  max_message_size = 262144
  message_retention_seconds = 86400
  receive_wait_time_seconds = 0
}

# https://www.terraform.io/docs/providers/aws/r/sqs_queue.html
resource "aws_sqs_queue" "sqs_queue" {
  name = "${var.ocean["name"]}-${var.ocean["settings"]}-worker-sqs"
  # How long until another worker gets a chance.
  visibility_timeout_seconds = 300
  # How long is the message allowed it live overall?.
  message_retention_seconds = 86400
  # Limit on how many bytes a message can contain.
  max_message_size = 262144
  # If you want a delay, put it here.
  delay_seconds = 0
  # The time for which a ReceiveMessage call will wait for a message to arrive
  # (long polling) before returning. An integer from 0 to 20 (seconds). The
  # default for this attribute is 0, meaning that the call will return
  # immediately.
  receive_wait_time_seconds = 0
  redrive_policy = "{\"deadLetterTargetArn\":\"${aws_sqs_queue.dead_letter_queue.arn}\",\"maxReceiveCount\":4}"
  policy = <<EOF
{
    "Version": "2012-10-17",
    "Id": "arn:aws:sqs:${var.region}:${var.default_secret_key}:${var.ocean["name"]}-${var.ocean["settings"]}-worker-sqs/SQSDefaultPolicy",
    "Statement": [
        {
            "Sid": "1234567890",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "SQS:*",
            "Resource": "arn:aws:sqs:${var.region}:${var.default_secret_key}:${var.ocean["name"]}-${var.ocean["settings"]}-worker-sqs"
        }
    ]
}
EOF
}

# -- S3 -----------------------------------------------------------------------

resource "aws_s3_bucket" "s3_inbox"
{
  # A bucket name in Oregon region must contain only lower case characters.
  bucket = "${lower(var.ocean["name"])}-${var.ocean["settings"]}-inbox"
  policy = <<EOF
{
	"Version": "2012-10-17",
	"Id": "Policy1452197183560",
	"Statement": [
		{
			"Sid": "Stmt1452197177705",
			"Effect": "Allow",
			"Principal": "*",
			"Action": "s3:GetObject",
			"Resource": "arn:aws:s3:::${lower(var.ocean["name"])}-${var.ocean["settings"]}-inbox/*"
		}
	]
}
EOF
}

resource "aws_s3_bucket" "s3_serve"
{
  bucket = "${lower(var.ocean["name"])}-${var.ocean["settings"]}-serve"
  policy = <<EOF
{
	"Version": "2012-10-17",
	"Id": "Policy1452197183560",
	"Statement": [
		{
			"Sid": "Stmt1452197177705",
			"Effect": "Allow",
			"Principal": "*",
			"Action": "s3:GetObject",
			"Resource": "arn:aws:s3:::${lower(var.ocean["name"])}-${var.ocean["settings"]}-serve/*"
		}
	]
}
EOF
}

resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket = "${aws_s3_bucket.s3_inbox.id}"
  queue {
    queue_arn     = "${aws_sqs_queue.sqs_queue.arn}"
    events        = ["s3:ObjectCreated:*"]
  }
}

# -- Elastic Beanstalk --------------------------------------------------------

resource "aws_elastic_beanstalk_application" "application" {
  name = "${var.ocean["name"]}-${var.ocean["settings"]}"
}

resource "aws_elastic_beanstalk_environment" "api_server" {
  name = "${var.ocean["name"]}-Api-${var.ocean["settings"]}"
  tier = "WebServer"
  application = "${var.ocean["name"]}-${var.ocean["settings"]}"
  # depends_on shouldn't be needed
  #depends_on = ["aws_elastic_beanstalk_application.application"]
  solution_stack_name = "64bit Amazon Linux 2016.03 v2.1.6 running Python 2.7"
  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "OCEAN_SETTINGS"
    value     = "${var.ocean["settings"]}_server"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__SCORE_STREAM"
      value     = "${aws_kinesis_stream.score_stream.name}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__TAG_TREND_STREAM"
      value     = "${aws_kinesis_stream.tag_trend_stream.name}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__LOCATION_DB_HOST"
      value     = "${aws_db_instance.search_db.address}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__SQS_QUEUE_NAME"
      value     = "${aws_sqs_queue.sqs_queue.name}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__S3_INCOMING_BUCKET_NAME"
      value     = "${aws_s3_bucket.s3_inbox.bucket}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__S3_SERVE_BUCKET_NAME"
      value     = "${aws_s3_bucket.s3_serve.bucket}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__SERVE_BUCKET_URL"
      value     = "https://s3-us-west-2.amazonaws.com/${aws_s3_bucket.s3_serve.bucket}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__SENTRY_DSN"
      value     = "https://74616609aa70401c919581c09415e6aa:28b7b06b94be4a348f681a2b6e8e5392@app.getsentry.com/80971"
  }

  # Got a bunch of settings in a temporary yml file by doing 'eb config',
  # but I'm not sure if these are application or environment settings, though
  # was thinking environment (some of them are, like the python stuff.)
  # - -
  # I have been setting null values as "null" but I'm thinking for them
  # to be null they need to be not set?
  # ah yes, here is the error in the EB console:
  # Service:AmazonCloudFormation, Message:'null' values are not allowed in templates
  #settings:  # How do I translate these things into terraform settings?
  # Note: Terraform does no checking at plan time (not sure about apply),
  # so, if you get this typed in wrong, you won't get a warning.
  # -- ah, but there can be errors at apply time.
  # -- I don't understand these names, AWSEB...
  #  AWSEBAutoScalingScaleDownPolicy.aws:autoscaling:trigger:
  #    LowerBreachScaleIncrement: '-1'
  #  AWSEBAutoScalingScaleUpPolicy.aws:autoscaling:trigger:
  #    UpperBreachScaleIncrement: '1'
  #  AWSEBCloudwatchAlarmHigh.aws:autoscaling:trigger:
  #    UpperThreshold: '6000000'
  #  AWSEBCloudwatchAlarmLow.aws:autoscaling:trigger:
  #    BreachDuration: '5'
  #    EvaluationPeriods: '1'
  #    LowerThreshold: '2000000'
  #    MeasureName: NetworkOut
  #    Period: '5'
  #    Statistic: Average
  #    Unit: Bytes

  # -- no wait...  not sure if this goes here in elastic beanstalk or if it
  # goes in a separate configuration.
  #https://aws.amazon.com/blogs/apn/terraform-beyond-the-basics-with-aws/
  setting {
    namespace = "aws:autoscaling:asg"
    name = "Availability Zones"
    value = "Any"
  }
  setting {
      namespace = "aws:autoscaling:asg"
      name = "Cooldown"
      value = "360"
  }
  setting {
      namespace = "aws:autoscaling:asg"
      name = "Custom Availability Zones"
      value = ""
  }
  setting {
      namespace = "aws:autoscaling:asg"
      name = "MaxSize"
      value = "4"
  }
  setting {
      namespace = "aws:autoscaling:asg"
      name = "MinSize"
      value = "1"
  }
#  setting {
#      namespace = "aws:autoscaling:launchconfiguration"
#      name = "BlockDeviceMappings"
#      value = "null"
#  }
  setting {
      namespace = "aws:autoscaling:launchconfiguration"
      name = "EC2KeyName"
      value = "michaelssl"  # How is this connected/addressed via terraform?
  }
  setting {
      namespace = "aws:autoscaling:launchconfiguration"
      name = "IamInstanceProfile"
      value = "${aws_iam_instance_profile.api_server_profile.name}"
  }
  setting {
      namespace = "aws:autoscaling:launchconfiguration"
      name = "ImageId"
      value = "ami-123456"  # What is this? Where specified?
  }
  setting {
      namespace = "aws:autoscaling:launchconfiguration"
      name = "InstanceType"
      value = "t1.micro"
  }
  setting {
      namespace = "aws:autoscaling:launchconfiguration"
      name = "MonitoringInterval"
      value = "5 minute"
  }
# Got this error on apply, so taking these out.
# * aws_elastic_beanstalk_environment.api_server: ConfigurationValidationException: Configuration validation exception: Invalid option value: 'null' (Namespace: 'aws:autoscaling:launchconfiguration', OptionName: 'RootVolumeType'): 'null' is not a valid volume type. Must be 'standard', 'gp2' or 'io1'.
#	status code: 400, request id: aa624492-0337-11e6-aa33-2d065e631d1f
#  setting {
#      namespace = "aws:autoscaling:launchconfiguration"
#      name = "RootVolumeIOPS"
#      value = "null"
#  }
#  setting {
#      namespace = "aws:autoscaling:launchconfiguration"
#      name = "RootVolumeSize"
#      value = "null"
#  }
#  setting {
#      namespace = "aws:autoscaling:launchconfiguration"
#      name = "RootVolumeType"
#      value = "null"
#  }
  setting {
      namespace = "aws:autoscaling:launchconfiguration"
      name = "SSHSourceRestriction"
      value = "tcp,22,22,0.0.0.0/0"
  }
  // SecurityGroup is automatically generated. If you set this here then it
  // is an additional security group added to the EC2 instance.
  setting {
      namespace = "aws:autoscaling:launchconfiguration"
      name = "SecurityGroups"
      value = "${aws_security_group.db_authorized_sg.name}"
  }
  setting {
      namespace = "aws:autoscaling:updatepolicy:rollingupdate"
      name = "MaxBatchSize"
      value = "1"
  }
  setting {
      namespace = "aws:autoscaling:updatepolicy:rollingupdate"
      name = "MinInstancesInService"
      value = "1"
  }
#  setting {
#      namespace = "aws:autoscaling:updatepolicy:rollingupdate"
#      name = "PauseTime"
#      value = "null"
#  }
  setting {
      namespace = "aws:autoscaling:updatepolicy:rollingupdate"
      name = "RollingUpdateEnabled"
      value = "true"
  }
  setting {
      namespace = "aws:autoscaling:updatepolicy:rollingupdate"
      name = "RollingUpdateType"
      value = "Health"
  }
  setting {
      namespace = "aws:autoscaling:updatepolicy:rollingupdate"
      name = "Timeout"
      value = "PT30M"
  }
#  setting {
#      namespace = "aws:ec2:vpc"
#      name = "AssociatePublicIpAddress"
#      value = "null"
#  }
  setting {
      namespace = "aws:ec2:vpc"
      name = "ELBScheme"
      value = "public"
  }
#  setting {
#      namespace = "aws:ec2:vpc"
#      name = "ELBSubnets"
#      value = "null"
#  }
#  setting {
#      namespace = "aws:ec2:vpc"
#      name = "Subnets"
#      value = "null"
#  }
#  setting {
#      namespace = "aws:ec2:vpc"
#      name = "VPCId"
#      value = "null"
#  }
  setting {
      namespace = "aws:elasticbeanstalk:application"
      name = "Application Healthcheck URL"
      value = "/v0.2/test"
  }
  setting {
      namespace = "aws:elasticbeanstalk:command"
      name = "BatchSize"
      value = "30"
  }
  setting {
      namespace = "aws:elasticbeanstalk:command"
      name = "BatchSizeType"
      value = "Percentage"
  }
  setting {
      namespace = "aws:elasticbeanstalk:command"
      name = "IgnoreHealthCheck"
      value = "false"
  }
  setting {
      namespace = "aws:elasticbeanstalk:command"
      name = "Timeout"
      value = "600"
  }
  setting {
      namespace = "aws:elasticbeanstalk:container:python"
      name = "NumProcesses"
      value = "1"
  }
  setting {
      namespace = "aws:elasticbeanstalk:container:python"
      name = "NumThreads"
      value = "15"
  }
  setting {
      namespace = "aws:elasticbeanstalk:container:python"
      name = "StaticFiles"
      value = "/static/=static/"
  }
  setting {
      namespace = "aws:elasticbeanstalk:container:python"
      name = "WSGIPath"
      value = "apps/wsgi_app.py"
  }
  setting {
      namespace = "aws:elasticbeanstalk:container:python:staticfiles"
      name = "/static/"
      value = "static/"
  }
  setting {
      namespace = "aws:elasticbeanstalk:control"
      name = "DefaultSSHPort"
      value = "22"
  }
  setting {
      namespace = "aws:elasticbeanstalk:control"
      name = "LaunchTimeout"
      value = "0"
  }
  setting {
      namespace = "aws:elasticbeanstalk:control"
      name = "LaunchType"
      value = "Migration"
  }
  setting {
      namespace = "aws:elasticbeanstalk:control"
      name = "RollbackLaunchOnFailure"
      value = "false"
  }
  setting {
      namespace = "aws:elasticbeanstalk:environment"
      name = "EnvironmentType"
      value = "LoadBalanced"
  }
#  setting {
#      namespace = "aws:elasticbeanstalk:environment"
#      name = "ExternalExtensionsS3Bucket"
#      value = "null"
#  }
#  setting {
#      namespace = "aws:elasticbeanstalk:environment"
#      name = "ExternalExtensionsS3Key"
#      value = "null"
#  }
  setting {
      namespace = "aws:elasticbeanstalk:environment"
      name = "ServiceRole"
    # This is broken, if we set it here, then nothing gets set. In the console
    # there is only one eligible choice, this default value here.
    # value = "${aws_iam_role.service_role.name}"
    value = "aws-elasticbeanstalk-service-role"
  }
  setting {
      namespace = "aws:elasticbeanstalk:healthreporting:system"
      name = "ConfigDocument"
      value = "{\"Version\":1,\"CloudWatchMetrics\":{\"Instance\":{\"RootFilesystemUtil\":null,\"CPUIrq\":null,\"LoadAverage5min\":null,\"ApplicationRequests5xx\":null,\"ApplicationRequests4xx\":null,\"CPUUser\":null,\"LoadAverage1min\":null,\"ApplicationLatencyP50\":null,\"CPUIdle\":null,\"InstanceHealth\":null,\"ApplicationLatencyP95\":null,\"ApplicationLatencyP85\":null,\"ApplicationLatencyP90\":null,\"CPUSystem\":null,\"ApplicationLatencyP75\":null,\"CPUSoftirq\":null,\"ApplicationLatencyP10\":null,\"ApplicationLatencyP99\":null,\"ApplicationRequestsTotal\":null,\"ApplicationLatencyP99.9\":null,\"ApplicationRequests3xx\":null,\"ApplicationRequests2xx\":null,\"CPUIowait\":null,\"CPUNice\":null},\"Environment\":{\"InstancesSevere\":null,\"InstancesDegraded\":null,\"ApplicationRequests5xx\":null,\"ApplicationRequests4xx\":null,\"ApplicationLatencyP50\":null,\"ApplicationLatencyP95\":null,\"ApplicationLatencyP85\":null,\"InstancesUnknown\":null,\"ApplicationLatencyP90\":null,\"InstancesInfo\":null,\"InstancesPending\":null,\"ApplicationLatencyP75\":null,\"ApplicationLatencyP10\":null,\"ApplicationLatencyP99\":null,\"ApplicationRequestsTotal\":null,\"InstancesNoData\":null,\"ApplicationLatencyP99.9\":null,\"ApplicationRequests3xx\":null,\"ApplicationRequests2xx\":null,\"InstancesOk\":null,\"InstancesWarning\":null}}}"
  }
/*  * aws_elastic_beanstalk_environment.worker: ConfigurationValidationException: Configuration validation exception: Invalid option specification (Namespace: 'aws:elasticbeanstalk:environment', OptionName: 'HealthCheckSuccessThreshold'): Unknown configuration setting.
	status code: 400, request id: a233de47-0bd0-11e6-bb6f-e18e70ceaeaa

  setting {
      namespace = "aws:elasticbeanstalk:healthreporting:system"
      name = "HealthCheckSuccessThreshold"
      value = "Ok"
  }*/
  setting {
      namespace = "aws:elasticbeanstalk:healthreporting:system"
      name = "SystemType"
      value = "basic"
  }
  setting {
      namespace = "aws:elasticbeanstalk:hostmanager"
      name = "LogPublicationControl"
      value = "false"
  }
  setting {
      namespace = "aws:elasticbeanstalk:monitoring"
      name = "Automatically Terminate Unhealthy Instances"
      value = "true"
  }
#  setting {
#      namespace = "aws:elasticbeanstalk:sns:topics"
#      name = "Notification Endpoint"
#      value = "null"
#  }
  setting {
      namespace = "aws:elasticbeanstalk:sns:topics"
      name = "Notification Protocol"
      value = "email"
  }
#  setting {
#      namespace = "aws:elasticbeanstalk:sns:topics"
#      name = "Notification Topic ARN"
#      value = "null"
#  }
#  setting {
#      namespace = "aws:elasticbeanstalk:sns:topics"
#      name = "Notification Topic Name"
#      value = "null"
#  }
  setting {
      namespace = "aws:elb:healthcheck"
      name = "HealthyThreshold"
      value = "3"
  }
  setting {
      namespace = "aws:elb:healthcheck"
      name = "Interval"
      value = "30"
  }
  setting {
      namespace = "aws:elb:healthcheck"
      name = "Target"
      value = "HTTP:80/v0.2/test"
  }
  setting {
      namespace = "aws:elb:healthcheck"
      name = "Timeout"
      value = "5"
  }
  setting {
      namespace = "aws:elb:healthcheck"
      name = "UnhealthyThreshold"
      value = "5"
  }
  setting {
      namespace = "aws:elb:listener:80"
      name = "InstancePort"
      value = "80"
  }
  setting {
      namespace = "aws:elb:listener:80"
      name = "InstanceProtocol"
      value = "HTTP"
  }
  setting {
      namespace = "aws:elb:listener:80"
      name = "ListenerEnabled"
      value = "true"
  }
  setting {
      namespace = "aws:elb:listener:80"
      name = "ListenerProtocol"
      value = "HTTP"
  }
#  setting {
#      namespace = "aws:elb:listener:80"
#      name = "PolicyNames"
#      value = "null"
#  }
#  setting {
#      namespace = "aws:elb:listener:80"
#      name = "SSLCertificateId"
#      value = "null"
#  }
  setting {
      namespace = "aws:elb:loadbalancer"
      name = "CrossZone"
      value = "true"
  }
  setting {
      namespace = "aws:elb:loadbalancer"
      name = "LoadBalancerHTTPPort"
      value = "OFF"
  }
  setting {
      namespace = "aws:elb:loadbalancer"
      name = "LoadBalancerHTTPSPort"
      value = "OFF"
  }
  setting {
      namespace = "aws:elb:loadbalancer"
      name = "LoadBalancerPortProtocol"
      value = "HTTP"
  }
  setting {
      namespace = "aws:elb:loadbalancer"
      name = "LoadBalancerSSLPortProtocol"
      value = "HTTPS"
  }

  // This is the pre-existing default elasticbeanstalk SG, for allowing
  // the api server to talk to the load balancer. I'm not sure if setting
  // this has any effect, or if it is necessary. TODO - is this necessary?
  // It seems to be shared across all EB Applications.
  setting {
      namespace = "aws:elb:loadbalancer"
      name = "SecurityGroups"
      value = "sg-61386c04"
  }
  setting {
      namespace = "aws:elb:policies"
      name = "ConnectionDrainingEnabled"
      value = "true"
  }
  setting {
      namespace = "aws:elb:policies"
      name = "ConnectionDrainingTimeout"
      value = "20"
  }
  setting {
      namespace = "aws:elb:policies"
      name = "ConnectionSettingIdleTimeout"
      value = "60"
  }
}

# when this gets merged in we can manage "application versions"
# see https://github.com/hashicorp/terraform/pull/3871
# resource "aws_elastic_beanstalk_application_version" "default" {
#  application = "tf-test-name"
#  name = "tf-test-version-label"
#  bucket = "${aws_s3_bucket.default.id}"
#  key = "${aws_s3_bucket_object.default.id}"
# }

resource "aws_elastic_beanstalk_environment" "worker" {
  name = "${var.ocean["name"]}-Worker-${var.ocean["settings"]}"
  tier = "Worker"
  application = "${var.ocean["name"]}-${var.ocean["settings"]}"
  # depends_on shouldn't be needed.
  #depends_on = ["aws_elastic_beanstalk_application.application"]
  solution_stack_name = "64bit Amazon Linux 2016.03 v2.1.6 running Python 2.7"
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN_SETTINGS"
      value     = "${var.ocean["settings"]}_worker"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__SCORE_STREAM"
      value     = "${aws_kinesis_stream.score_stream.name}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__TAG_TREND_STREAM"
      value     = "${aws_kinesis_stream.tag_trend_stream.name}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__LOCATION_DB_HOST"
      value     = "${aws_db_instance.search_db.address}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__SQS_QUEUE_NAME"
      value     = "${aws_sqs_queue.sqs_queue.name}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__S3_INCOMING_BUCKET_NAME"
      value     = "${aws_s3_bucket.s3_inbox.bucket}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__S3_SERVE_BUCKET_NAME"
      value     = "${aws_s3_bucket.s3_serve.bucket}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__SERVE_BUCKET_URL"
      value     = "https://s3-us-west-2.amazonaws.com/${aws_s3_bucket.s3_serve.bucket}"
  }
  setting {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = "OCEAN__SENTRY_DSN"
      value     = "https://74616609aa70401c919581c09415e6aa:28b7b06b94be4a348f681a2b6e8e5392@app.getsentry.com/80971"
  }

  // These are from a dump of the prototype deployment, but I don't know
  // their terraform names.
//    AWSEBAutoScalingScaleDownPolicy.aws:autoscaling:trigger:
//    LowerBreachScaleIncrement: '-1'
//  AWSEBAutoScalingScaleUpPolicy.aws:autoscaling:trigger:
//    UpperBreachScaleIncrement: '1'
//  AWSEBCloudwatchAlarmHigh.aws:autoscaling:trigger:
//    UpperThreshold: '6000000'
//  AWSEBCloudwatchAlarmLow.aws:autoscaling:trigger:
//    BreachDuration: '5'
//    EvaluationPeriods: '1'
//    LowerThreshold: '2000000'
//    MeasureName: NetworkOut
//    Period: '5'
//    Statistic: Average
//    Unit: Bytes

  /** aws_elastic_beanstalk_environment.worker: ConfigurationValidationException: Configuration validation exception: Invalid option specification (Namespace: 'aws:autoscaling:asg', OptionName: 'Custom Availability Zones'): The Availability Zone(s) that you specified are invalid: Any.
	status code: 400, request id: 2557e874-0bd0-11e6-1111-b1456126da02

  setting {
    namespace = "aws:autoscaling:asg"
    name = "Availability Zones"
    value = "Any"
  }
  setting {
    namespace = "aws:autoscaling:asg"
    name = "Cooldown"
    value = "Any"
  }
  setting {
    namespace = "aws:autoscaling:asg"
    name = "Custom Availability Zones"
    value = "Any"
  }
  setting {
    namespace = "aws:autoscaling:asg"
    name = "MaxSize"
    value = "Any"
  }
  setting {
    namespace = "aws:autoscaling:asg"
    name = "MinSize"
    value = "Any"
  }
  */
  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name = "EC2KeyName"
    value = "michaelssl"
  }
  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name = "IamInstanceProfile"
    value = "${aws_iam_instance_profile.worker_profile.name}"
  }
  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name = "ImageId"
    value = "ami-f93cde99"
  }
  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name = "InstanceType"
    value = "t1.micro"
  }
  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name = "MonitoringInterval"
    value = "5 minute"
  }
  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name = "SSHSourceRestriction"
    value = "tcp,22,22,0.0.0.0/0"
  }
  // SecurityGroup is automatically generated. If you set this here then it
  // is an additional security group added to the EC2 instance.
  // Note this is the one for LocalPicTourney-Worker-dev and having it here is causing
  // issues.
  setting {
      namespace = "aws:autoscaling:launchconfiguration"
      name = "SecurityGroups"
      value = "${aws_security_group.db_authorized_sg.name}"
  }
  setting {
    namespace = "aws:autoscaling:updatepolicy:rollingupdate"
    name = "RollingUpdateEnabled"
    value = "false"
  }
  setting {
    namespace = "aws:autoscaling:updatepolicy:rollingupdate"
    name = "RollingUpdateType"
    value = "Time"
  }
  setting {
    namespace = "aws:autoscaling:updatepolicy:rollingupdate"
    name = "Timeout"
    value = "PT30M"
  }
  setting {
    namespace = "aws:elasticbeanstalk:application"
    name = "Application Healthcheck URL"
    value = "/health_check"
  }
  setting {
    namespace = "aws:elasticbeanstalk:command"
    name = "BatchSize"
    value = "30"
  }
  setting {
    namespace = "aws:elasticbeanstalk:command"
    name = "BatchSizeType"
    value = "Percentage"
  }
  setting {
    namespace = "aws:elasticbeanstalk:command"
    name = "IgnoreHealthCheck"
    value = "false"
  }
  setting {
    namespace = "aws:elasticbeanstalk:command"
    name = "Timeout"
    value = "600"
  }
  setting {
    namespace = "aws:elasticbeanstalk:container:python"
    name = "NumProcesses"
    value = "1"
  }
  setting {
    namespace = "aws:elasticbeanstalk:container:python"
    name = "NumThreads"
    value = "15"
  }
  setting {
    namespace = "aws:elasticbeanstalk:container:python"
    name = "StaticFiles"
    value = "/static/=static/"
  }
  setting {
    namespace = "aws:elasticbeanstalk:container:python"
    name = "WSGIPath"
    value = "apps/worker.py"
  }
  setting {
    namespace = "aws:elasticbeanstalk:container:python:staticfiles"
    name = "/static/"
    value = "static/"
  }
  setting {
    namespace = "aws:elasticbeanstalk:control"
    name = "DefaultSSHPort"
    value = "22"
  }
  setting {
    namespace = "aws:elasticbeanstalk:control"
    name = "LaunchTimeout"
    value = "0"
  }
  setting {
    namespace = "aws:elasticbeanstalk:control"
    name = "LaunchType"
    value = "Migration"
  }
  setting {
    namespace = "aws:elasticbeanstalk:control"
    name = "RollbackLaunchOnFailure"
    value = "false"
  }
  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name = "EnvironmentType"
    value = "LoadBalanced"
  }
  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name = "ServiceRole"
    # This is broken, if we set it here, then nothing gets set. In the console
    # there is only one eligible choice, this default value here.
    # value = "${aws_iam_role.service_role.name}"
    value = "aws-elasticbeanstalk-service-role"
  }
  setting {
    namespace = "aws:elasticbeanstalk:healthreporting:system"
    name = "ConfigDocument"
    value = "{\"Version\":1,\"CloudWatchMetrics\":{\"Instance\":{\"RootFilesystemUtil\":null,\"CPUIrq\":null,\"LoadAverage5min\":null,\"ApplicationRequests5xx\":null,\"ApplicationRequests4xx\":null,\"CPUUser\":null,\"LoadAverage1min\":null,\"ApplicationLatencyP50\":null,\"CPUIdle\":null,\"InstanceHealth\":null,\"ApplicationLatencyP95\":null,\"ApplicationLatencyP85\":null,\"ApplicationLatencyP90\":null,\"CPUSystem\":null,\"ApplicationLatencyP75\":null,\"CPUSoftirq\":null,\"ApplicationLatencyP10\":null,\"ApplicationLatencyP99\":null,\"ApplicationRequestsTotal\":null,\"ApplicationLatencyP99.9\":null,\"ApplicationRequests3xx\":null,\"ApplicationRequests2xx\":null,\"CPUIowait\":null,\"CPUNice\":null},\"Environment\":{\"InstancesSevere\":null,\"InstancesDegraded\":null,\"ApplicationRequests5xx\":null,\"ApplicationRequests4xx\":null,\"ApplicationLatencyP50\":null,\"ApplicationLatencyP95\":null,\"ApplicationLatencyP85\":null,\"InstancesUnknown\":null,\"ApplicationLatencyP90\":null,\"InstancesInfo\":null,\"InstancesPending\":null,\"ApplicationLatencyP75\":null,\"ApplicationLatencyP10\":null,\"ApplicationLatencyP99\":null,\"ApplicationRequestsTotal\":null,\"InstancesNoData\":null,\"ApplicationLatencyP99.9\":null,\"ApplicationRequests3xx\":null,\"ApplicationRequests2xx\":null,\"InstancesOk\":null,\"InstancesWarning\":null}}}"
  }
  /*
  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name = "HealthCheckSuccessThreshold"
    value = "Ok"
  }

  * aws_elastic_beanstalk_environment.worker: ConfigurationValidationException: Configuration validation exception: Invalid option specification (Namespace: 'aws:elasticbeanstalk:environment', OptionName: 'SystemType'): Unknown configuration setting.
	status code: 400, request id: 64028b0c-0bd0-11e6-9d26-7be95d48f043

  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name = "SystemType"
    value = "enhanced"
  }*/
  setting {
    namespace = "aws:elasticbeanstalk:hostmanager"
    name = "LogPublicationControl"
    value = "false"
  }
  setting {
    namespace = "aws:elasticbeanstalk:managedactions"
    name = "ManagedActionsEnabled"
    value = "false"
  }
/*
* aws_elastic_beanstalk_environment.worker: ConfigurationValidationException: Configuration validation exception: Invalid option value: 'false' (Namespace: 'aws:elasticbeanstalk:managedactions', OptionName: 'PreferredStartTime'): Value does not meet the required minimum length: 9

  setting {
    namespace = "aws:elasticbeanstalk:managedactions"
    name = "PreferredStartTime"
    value = "false"
  }*/
  setting {
    namespace = "aws:elasticbeanstalk:monitoring"
    name = "Automatically Terminate Unhealthy Instances"
    value = "true"
  }
  setting {
    namespace = "aws:elasticbeanstalk:sns:topics"
    name = "Notification Endpoint"
    value = ""
  }
  setting {
    namespace = "aws:elasticbeanstalk:sns:topics"
    name = "Notification Protocol"
    value = "email"
  }
  setting {
    namespace = "aws:elasticbeanstalk:sqsd"
    name = "ConnectTimeout"
    value = "5"
  }
  setting {
    namespace = "aws:elasticbeanstalk:sqsd"
    name = "WorkerQueueURL"
    value = "${aws_sqs_queue.sqs_queue.id}"
  }

//    delay_seconds = 90
//  max_message_size = 2048
//  message_retention_seconds = 86400
//  receive_wait_time_seconds = 10



  setting {
    namespace = "aws:elasticbeanstalk:sqsd"
    name = "ErrorVisibilityTimeout"
    value = "2"
  }
  setting {
    namespace = "aws:elasticbeanstalk:sqsd"
    name = "HttpConnections"
    value = "10"
  }
  setting {
    namespace = "aws:elasticbeanstalk:sqsd"
    name = "HttpPath"
    value = "/worker_callback"
  }

  // http://docs.aws.amazon.com/elasticbeanstalk/latest/dg/using-features-managing-env-tiers.html
  // If the worker returns any response other than 200 OK, then Elastic
  // Beanstalk waits to put the message back in the queue after the configured
  // VisibilityTimeout period. If there is no response, then Elastic
  // Beanstalk waits to put the message back in the queue after the
  // InactivityTimeout period so that the message is available for another
  // attempt at processing.

  setting {
    namespace = "aws:elasticbeanstalk:sqsd"
    name = "InactivityTimeout"
    value = "${aws_sqs_queue.sqs_queue.visibility_timeout_seconds}"
  }
  setting {
    namespace = "aws:elasticbeanstalk:sqsd"
    name = "MaxRetries"
    value = "10"
  }
  setting {
    namespace = "aws:elasticbeanstalk:sqsd"
    name = "MimeType"
    value = "application/json"
  }
  setting {
    namespace = "aws:elasticbeanstalk:sqsd"
    name = "RetentionPeriod"
    value = "${aws_sqs_queue.sqs_queue.message_retention_seconds}"
  }
  setting {
    namespace = "aws:elasticbeanstalk:sqsd"
    name = "VisibilityTimeout"
    value = "${aws_sqs_queue.sqs_queue.visibility_timeout_seconds}"
  }
}

///*
////"ip" is not a value that application has, but there is something else I bet.
////output "ip" {
////    value = "${aws_elastic_beanstalk_application.application.public_ip}"
////}
////*/
