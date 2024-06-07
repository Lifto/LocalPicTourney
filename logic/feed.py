from __future__ import division, absolute_import, unicode_literals



"""A feed of activity notifications to distribute to followers."""

from uuid import uuid1

from pynamodb.models import DoesNotExist

from log import log
from model import FeedActivity, Follower, User
from util import from_camel, now

# -- Activity for Followers ---------------------------------------------------

def feed_new_photo(followed_uuid, photo_uuid):
    when = now()
    followers = Follower.query(followed_uuid, consistent_read=False)
    for f in followers:
        feed_activity(f.follower,
                      created_on=when,
                      activity='NewPhoto',
                      user_uuid=followed_uuid,
                      photo_uuid=photo_uuid)

def feed_new_comment(comment_owner_uuid, commenter_uuid, photo_uuid,
                     comment_uuid):
    feed_activity(comment_owner_uuid,
                  activity='NewComment',
                  user_uuid=commenter_uuid,
                  photo_uuid=photo_uuid,
                  comment_uuid=comment_uuid,
                  push_enabled=comment_owner_uuid!=commenter_uuid)

def feed_tournament_win(winner_uuid, photo_uuid):
    when = now()
    followers = Follower.query(winner_uuid, consistent_read=False)
    for f in followers:
        feed_activity(f.follower,
                      created_on=when,
                      activity='WonTournament',
                      user_uuid=winner_uuid,
                      photo_uuid=photo_uuid)
    feed_activity(winner_uuid, created_on=when, activity='YouWonTournament',
                  photo_uuid=photo_uuid)

def feed_message(user_uuid, message):
    feed_activity(user_uuid, activity='message: %s' % message)


# -- Self Activity ------------------------------------------------------------

def feed_activity(feed_owner_uuid,
                  activity,
                  created_on=None,
                  user_uuid=None,
                  photo_uuid=None,
                  comment_uuid=None,
                  push_enabled=True):
    if created_on is None:
        created_on = now()
    feed = FeedActivity(feed_owner_uuid, created_on=created_on,
                        activity=activity, user=user_uuid, photo=photo_uuid,
                        comment=comment_uuid, uuid=uuid1())
    feed.save()
    if push_enabled:
        permission_attr = 'notify_%s' % from_camel(activity)
        try:
            feed_owner = User.get(feed_owner_uuid, consistent_read=False)
        except DoesNotExist:
            log.info('did not push to user {} got DoesNotExist'.format(
                    feed_owner_uuid.hex))
            return
        if hasattr(feed_owner, permission_attr):
            # TODO: Isn't this supposed to be None, False?
            if getattr(feed_owner, permission_attr) not in (None, True):
                # TODO : push
                log.info(
                        "push notification - user:{} activity:{}".format(
                            feed_owner_uuid.hex, activity
                        ))

def get_feed_count(user_uuid):
    """Returns number of unread items in User's feed."""
    def own_photo_filter(i):
        for item in i:
            if not ('NewPhoto' == item.activity and user_uuid == item.user):
                yield item

    feed = FeedActivity.query(user_uuid, consistent_read=False, read__eq=False)
    feed = own_photo_filter(feed)
    return len(list(feed))
