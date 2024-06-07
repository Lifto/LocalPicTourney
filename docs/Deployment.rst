LocalPicTourney Deployment
==================

LocalPicTourney runs in AWS. The infrastructure is configured almost entirely by
Terraform.

For a new deployment you run terraform apply, then some command-line to attach
to the Elastic Beanstalk application, put the URL from EB Console into the
settings, init the search and location dbs.

git@github.com:LocalPicTourneyInc/LocalPicTourney.git

Quick Reference
---------------

To upgrade (an existing) Api server or Worker, use the Elastic Beanstalk cli

.. code-block:: bash

    $ eb deploy LocalPicTourney-Api-dev
    $ eb deploy LocalPicTourney-Worker-dev

To examine, change, or destroy AWS Infrastructure, use Terraform

.. code-block:: bash

    $ cd ./terraform
    $ terraform plan -var-file /Users/lifto/.local_pic_tourney/ocean.tfvars -var-file ./ocean.tfvars
    $ terraform apply -var-file /Users/lifto/.local_pic_tourney/ocean.tfvars -var-file ./ocean.tfvars
    $ terraform destroy -var-file /Users/lifto/.local_pic_tourney/ocean.tfvars -var-file ./ocean.tfvars

Overview
--------

LocalPicTourney runs a scalable Api server and Worker in Elastic Beanstalk. It has
Kinesis streams, SQS queues, S3 buckets, DynamoDB tables and accesses an RDS
instance. We expect to deploy multiple instances of the system
simultaneously (a production deployment, 'prod', for real clients to use, a
'dev' for application developers to use, and 'load-test'. The separation of
these systems is maintained by keeping each deployment on its own branch,
which is based on and kept up-to-date with master.

Terraform
---------

The AWS infrastructure the system runs on is configured by Terraform.
Run these Terraform commands inside /terraform to manage the infrastructure.

.. code-block:: bash

    $ cd ./terraform
    $ terraform plan -var-file /Users/lifto/.local_pic_tourney/ocean.tfvars -var-file ./ocean.tfvars
    $ terraform apply -var-file /Users/lifto/.local_pic_tourney/ocean.tfvars -var-file ./ocean.tfvars
    $ terraform destroy -var-file /Users/lifto/.local_pic_tourney/ocean.tfvars -var-file ./ocean.tfvars

Terraform uses the file :code:`terraform.tfstate` to maintain its connection
to the AWS infrastructure it manages so make sure it is up-to-date, and
be sure to commit and push any changes.
(same with :code:`terraform.tfstate.backup`)

Newly created Terraform infrastructure will not run until the Elastic Beanstalk
Environments for Api and Worker have been deployed.

Elastic Beanstalk
-----------------

Each Terraform deployment (dev, load-test, prod) has its own Elastic Beanstalk
Application (which holds two scalable Environments: Api and Worker.) The
Elastic Beanstalk CLI can only be attached to one Application at a time. We
manage multiple deployments by keeping each deployment on its own branch and
committing the .elasticbeanstalk folder to git. That way when you check out a
different branch your Elastic Beanstalk CLI is configured to talk to the
Application instance that goes with the branch.

When configuring a branch for the first time delete the
:code:`.elasticbeanstalk` directory and run :code:`eb init`.

.. code-block:: bash

    $ rm -rf .elasticbeanstalk/
    $ eb init

Select the Application you want to control from the list given. (If you don't
see the Application you want, did you press option 3 for US-West-2?)

Note that :code:`eb init` alters your .gitignore. We want to keep the
Elastic Beanstalk configuration in git, so, to undo those changes.

.. code-block:: bash

    $ git checkout .gitignore

Deploy applications

.. code-block:: bash

    $ eb deploy LocalPicTourney-Api-dev
    $ eb deploy LocalPicTourney-Worker-dev

The location and search databases need to be initialized. To do this first
you must get the url for the Api environment from the Elastic Beanstalk
console and set it to be the URL (global variable) in apps/cli.py

Init the databases

.. code-block:: bash

    $ python -c "from apps import cli;print(cli.open_url('v0.2/init_location_db'))"
    $ python -c "from apps import cli;print(cli.open_url('v0.2/init_search_db'))"

You can test if the databases are working

.. code-block:: bash

    $ python -c "from apps import cli;print(cli.open_url('v0.2/test_location_db'))"
    $ python -c "from apps import cli;print(cli.open_url('v0.2/test_search_db'))"

Latest Elastic Beanstalk logs

.. code-block:: bash

    $ eb logs LocalPicTourney-Api-dev

SSH

.. code-block:: bash

    $ eb ssh LocalPicTourney-Api-dev

After you ssh, our Flask application logs can be found in /var/logs/ocean.log

.. code-block:: bash

    [ec2-user@ip-172-31-35-117 ~]$ tail -f /var/logs/ocean.log

Test the API

.. code-block:: bash

    $ python -c "from apps import cli;cli.test_api()"


Seed Users
----------

We deploy seed users and photos. (see ./data/seed_data)

There are many ad-hoc tools in ./apps/cli.py, see cli.make_test_users, it will
register users for each location and upload some photos for them. see
cli.add_tags too.