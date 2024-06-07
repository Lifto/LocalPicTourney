from __future__ import division, absolute_import, unicode_literals

import getopt
import os
import os.path
import subprocess
import sys

# The git repo from which we deploy (most likely the same repo in which this
# script lives.)
GIT_REPO = 'git@github.com:LocalPicTourneyInc/LocalPicTourney.git'
# Name of the directory created by 'git clone'
REPO_NAME = 'LocalPicTourney'
LC_NAME = 'localpictourney'  # for venv prefix

# Top level build dir. *Must not be within a git repo.* Script will create this.
TOP_BUILD_DIR = '/Users/lifto/Documents/Development/LocalPicTourneyBuild'

# This is inside the git repo, it is where we keep each deployment's terraform
# scripts.
DEPLOYMENT_DIR = os.path.join(TOP_BUILD_DIR, REPO_NAME, 'terraform/deployments')

# Directory where this system keeps its virtualenvs.
VIRTUALENV_DIR = '/Users/lifto/.virtualenvs'

# TODO: put the .localpictourney/ocean.tfvars in a user-agnostic place.
TERRAFORM_PLAN = 'terraform plan -var-file /Users/lifto/.localpictourney/ocean.tfvars -var-file ./ocean.tfvars'
TERRAFORM_APPLY = 'terraform apply -var-file /Users/lifto/.localpictourney/ocean.tfvars -var-file ./ocean.tfvars'
TERRAFORM_DESTROY = 'terraform destroy -var-file /Users/lifto/.localpictourney/ocean.tfvars -var-file ./ocean.tfvars'

def main(argv):
    try:
        # Shortopts requiring an argument are followed by a ':'
        # longopts requiring an argument are followed by a '='
        #opts, args = getopt.getopt(argv, "hi:0", ["ifile=", "ofile="])
        opts, args = getopt.getopt(argv, "s:d:", ["status=", "deploy="])
    except getopt.GetoptError:
        print('error handling options')
        print('TODO: show the help')
        sys.exit(2)
    for opt, arg in opts:
        if opt in ("-s", "--status"):
            do_status(arg)
        elif opt in ("-d", "--deploy"):
            do_deploy(arg)
    print('done')

def do_status(name):
    settings_profiles = os.listdir(DEPLOYMENT_DIR)
    if name not in settings_profiles:
        print(
        "unknown deployment '{}', current valid arguments are {}, add a directory to ./terraform/deployments with this name".format(
            name, ', '.join(settings_profiles)
        ))
        sys.exit(1)
    # Get the status of the deployment. Do we have a working directory?
    working_dir = WORKING_DIR + name
    if not os.path.exists(working_dir):
        print('deployment does not have working dir {}'.format(working_dir))
    print('status %s' % name)

def do_deploy(name):
    settings_profiles = os.listdir('./terraform/deployments')
    if name not in settings_profiles:
        print(
        "unknown deployment '{}', current valid arguments are {}, add a directory to ./terraform/deployments with this name".format(
            name, ', '.join(settings_profiles)
        ))
        sys.exit(1)

    # Do we have the build dir?
    confirm_or_construct_directory(TOP_BUILD_DIR)

    # Do we have the working directory?
    working_dir = os.path.join(TOP_BUILD_DIR, name)
    confirm_or_construct_directory(working_dir)
    os.chdir(working_dir)

    # Do we have the git repo?
    repo_dir = os.path.join(working_dir, REPO_NAME)
    if not os.path.exists(repo_dir):
        print('No git repository installed, cloning git repo')
        returncode = 0
        try:
            output = subprocess.check_output(['git', 'clone', GIT_REPO])
        except subprocess.CalledProcessError as e:
            # note: e.returncode, e.cmd, e.output are available.
            returncode = e.returncode
            output = e.output
        if returncode != 0:
            print('git clone failed, exiting')
            print(output)
            print(returncode)
            exit(1)

    os.chdir(repo_dir)

    # Check git again
    try:
        subprocess.check_output(['git', 'status'])
    except subprocess.CalledProcessError as e:
        print('git status failed, exiting')
        print(e.output)
        print(e.returncode)
        exit(1)

    # Get latest from git.
    try:
        print(subprocess.check_output(['git', 'pull']))
    except subprocess.CalledProcessError as e:
        print('git pull failed, exiting')
        print(e.output)
        print(e.returncode)
        exit(1)

    # TODO: We have a problem here... we need the 'source' line in my
    # .profile for 'mkvirtualenv' to work, but it's not present when I
    # use this 'check_output' call.

    # Is there a virtualenv for this deployment?
    venv_name = '{}-{}'.format(LC_NAME, name)
    venv_path = os.path.join(VIRTUALENV_DIR, venv_name)
    if not os.path.exists(venv_path):
        print("virtualenv '{}' not found, creating.".format(venv_name))
        try:
            # Not sure why this only works as a single string but it does.
            # Python subprocess docs say this argument should be a list of
            # individual strings, but this monolithic line works where the
            # list does not.
            output = subprocess.check_output('source /usr/local/bin/virtualenvwrapper.sh;mkvirtualenv {}'.format(venv_name), shell=True)
        except subprocess.CalledProcessError as e:
            # note: e.returncode, e.cmd, e.output are available.
            print('mkvirtualenv failed, exiting')
            print(e.output)
            print(e.returncode)
            exit(1)

    if os.path.exists(venv_path):
        print("virtualenv '{}' confirmed.".format(venv_name))
    else:
        print("virtualenv '{}' not found, exiting.".format(venv_name))
        exit(1)

    # Is the virtualenv up to date?
    cmd = [os.path.join(venv_path, 'bin', 'pip2.7'), 'install', '-r', 'requirements.txt']
    cmd_text = ' '.join(cmd)
    print(cmd_text)
    try:
        output = subprocess.check_output(cmd)
    except subprocess.CalledProcessError as e:
        # note: e.returncode, e.cmd, e.output are available.
        print('mkvirtualenv upgrade failed, exiting')
        print(e.output)
        print(e.returncode)
        exit(1)
    print('virtualenv {} up to date.'.format(venv_name))
    #print(output)

    # Test the Elastic Beanstalk client to make sure it works.
    try:
        #output = subprocess.check_output(['eb'])
        output = subprocess.check_output('source /Users/lifto/.virtualenvs/{}/bin/activate;eb'.format(venv_name), shell=True)
    except subprocess.CalledProcessError as e:
        # note: e.returncode, e.cmd, e.output are available.
        print('Elastic Beanstalk Command Line Interface (awsebcli) not findable, exiting')
        print(e.output)
        print(e.returncode)
        exit(1)
    except OSError as e:
        print('Elastic Beanstalk Command Line Interface (awsebcli) not findable, exiting')
        print(e)
        exit(1)

    # Which terraform script do we use? The one in that repo or the one in
    # this one? This is a migration issue. Terraform says it migrates by
    # introspecting the existing resources. But what if it skips a few, and
    # there are resources that are not in the terraform files?
    # - - my answer - -
    # The idea is that we may need to deploy older versions of the code.
    # I'm thinking we use the one from the deploy-candidate.
    # And if needed, we patch the deploy candidate.
    # This binds the API code-version to the terraform plan.
    # But that's probably quite sensible. If you want to deploy separate
    # versions of the same API code you either make a new settings sub-set in
    # the repo's terraform dir, or you update same. If you really want two
    # separate versions then you fork or make a branch.
    # When there is complexity you can't pretend it's not there, so, this is
    # where that complexity can go if/when it arises. Whew :simple_smile:
    # And it's all in version control.
    # TL;DR - use the script in the deploy candidate dir.

    # Do we have terraform installed?
    try:
        output = subprocess.check_output(['terraform', '--version'])
    except subprocess.CalledProcessError as e:
        # note: e.returncode, e.cmd, e.output are available.
        print('terraform not findable, exiting')
        print(e.output)
        print(e.returncode)
        exit(1)
    except OSError as e:
        print('terraform not findable, exiting')
        print(e)
        exit(1)
    print(output)

    # Before we can init the Elastic Beanstalk cli, we need to run Terraform
    # so there is an application to connect to.
    terraform_dir = os.path.join(working_dir, REPO_NAME, 'terraform',
                                 'deployments', name)

    if not os.path.exists(terraform_dir):
        print("can not find settings directory {}, exiting".format(terraform_dir))
    os.chdir(terraform_dir)

    # Where we are: We may need to interpret the terraform output?
    # Or we could just keep trying the eb cli?

    print('terraform commented out, see ocean.py')
    # TODO: Upgrade terraform, and/or figure out which of these settings in
    # the .tf cause this to reset when nothing has changed. (I know they
    # are working on it.)
    # Otherwise we have a problem where the thing is kicking over and then
    # we try to do a deploy and it says "no!" because it is turning over.
    # Let's see a terraform plan (TODO: switch on plan or deploy input)
    cmd = TERRAFORM_APPLY.split(' ')
    try:
        output = subprocess.check_output(cmd)
    except subprocess.CalledProcessError as e:
        # note: e.returncode, e.cmd, e.output are available.
        print('terraform plan failed, exiting')
        print(e.output)
        print(e.returncode)
        exit(1)

    print(cmd)
    print(output)

    # Is the eb client initialized?
    os.chdir(repo_dir)

    output = eb('status', venv_name=venv_name)
    if output == 'ERROR: This directory has not been set up with the EB CLI\nYou must first run "eb init".\n':
        # TODO: Init eb using an interactive popen session.
        # Eb requires init, this is a user step at the moment.
        print('USER MUST DO NEXT STEP BY HAND\nSwitch to dir {}\ntype: workon {}\ntype: eb init\nIn the menu select us-west-2\nIn the menu select the Application {}'.format(
            repo_dir, venv_name, '{}-{}'.format(REPO_NAME, name)))
        return

    # TODO: Can we see if the latest is already deployed?
    # Deploy the latest version in the git repo.
    eb('deploy {}-Api-{}'.format(REPO_NAME, name), venv_name=venv_name)
    eb('deploy {}-Worker-{}'.format(REPO_NAME, name), venv_name=venv_name)

    print output


def confirm_or_construct_directory(path):
    if not os.path.exists(path):
        print("directory '{}' does not exist, creating".format(path))
        os.mkdir(path)
        if not os.path.exists(path):
            print("Could not create directory '{}', exiting".format(path))
            exit(1)
    if os.path.exists(path):
        print("Using directory '{}'".format(path))
    else:
        print("Directory '{}' not found, exiting".format(path))
        exit(1)


def eb(args):
    print('eb ' + args)
    try:
        # output = subprocess.check_output(['eb'])
        output = subprocess.check_output(
            'source /Users/lifto/.virtualenvs/{}/bin/activate;eb {}'.format(venv_name, args),
            shell=True)
    except subprocess.CalledProcessError as e:
        # note: e.returncode, e.cmd, e.output are available.
        print(
        'Elastic Beanstalk Command Line Interface (awsebcli) not findable, exiting')
        print(e.output)
        print(e.returncode)
        exit(1)
    except OSError as e:
        print(
        'Elastic Beanstalk Command Line Interface (awsebcli) not findable, exiting')
        print(e)
        exit(1)
    return output

if __name__ == "__main__":
    main(sys.argv[1:])
