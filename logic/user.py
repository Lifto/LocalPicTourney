from __future__ import division, absolute_import, unicode_literals



import uuid

from pynamodb.models import DoesNotExist

from ocean_exceptions import InvalidAPIUsage
from log import log
from logic.feed import feed_activity
from logic.tournament import init_tournament_status
import model
from util import generate_random_string, now


def create_user(user_agent='no user agent given', is_test=False):
    user_uuid = uuid.uuid1()
    joined_date = now()
    # current token type is 'a'
    token = b'_a_.%s' % generate_random_string(80)
    kwargs = {
        'joined_date': joined_date,
        'token': token,
        'user_agent': user_agent,
        'registration': 'need name'
    }
    if is_test:
        kwargs['is_test'] = True
    new_user = model.User(user_uuid, **kwargs)
    init_tournament_status(new_user)  # This calls save.
    #new_user.save()
    feed_activity(user_uuid, 'Joined')

    return new_user


def get_user(user_uuid):
    return model.User.get(user_uuid, consistent_read=False)

    # 'need facebook', 'need facebook token confirmation', 'need name',
    # 'need pic', 'ok'

def update_registration_status(user):
    log.info("update registration status {}".format(user))
    existing_registration = user.registration
    log.info("update registration status existing registration: {}".format(existing_registration))
    if not user.user_name:
        new_registration = 'need name'
    elif not user.facebook_api_token:
        new_registration = 'need facebook'
    elif user.show_gender_male is None:
        new_registration = 'need gender'
    elif not user.photo:
        new_registration = 'need pic'
    elif not user.location:
        new_registration = 'need location'
    elif user.view_gender_male is None:
        new_registration = 'need view gender'
    else:
        new_registration = 'ok'
    if new_registration != existing_registration:
        log.info("update registration status updating registration: {} to {}".format(existing_registration, new_registration))
        user.registration = new_registration
        user.save()


def change_user_name(user, name):
    """Use new user name (if not None), relinquish old user name.

    If name is already in use raise InvalidAPIUsage
    """
    previous_name = user.user_name
    if name is None:
        log.error('setting user_name to None')
        user.user_name = None
        user.save()
    else:
        # In the DynamoDB Docs (don't know about PynamoDB):
        # To prevent a new item from replacing an existing item, use a
        # conditional expression that contains the attribute_not_exists
        # function with the name of the attribute being used as the partition
        # key for the table. Since every record must contain that attribute,
        # the attribute_not_exists function will only succeed if no matching
        # item exists.
        try:
            model.UserName.get(name)
        except DoesNotExist:
            user_name_object = model.UserName(name)
            user_name_object.user_uuid = user.uuid
            user_name_object.save()
            user_name_object.refresh()
            if user_name_object.user_uuid != user.uuid:
                raise InvalidAPIUsage("Name '%s' not unique" % name)
            user.user_name = name
            user.save()
            update_registration_status(user)
        else:
            raise InvalidAPIUsage("Name '%s' not unique" % name)

    # Relinquish previous name, if there was one.
    if previous_name is not None:
        try:
            previous_name_object = model.UserName.get(previous_name)
        except DoesNotExist:
            log.warn("can not delete UserName(%s) because it does not exist." % previous_name)
        else:
            previous_name_object.delete()


def delete_user(user):
    """Make re-registering with the same data possible.

    * disable auth for that user so accidental usage of the old token will
      appear as an error to the client.
    * dis-associate the facebook_id so registering a new one won't make for
      ambiguous lookups
    * delete the username so a preferred username can be re-used.
    """

    user.auth_enabled = False
    user.facebook_id = None
    user.save()
    change_user_name(user, None)
    user_name = user.user_name
    user.user_name = None
    user.save()
    if user_name is not None:
        try:
            name = model.UserName.get(user_name)
        except DoesNotExist:
            log.warn("can not delete UserName(%s) because it does not exist." % user_name)
        else:
            name.delete()

