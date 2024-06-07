from __future__ import division, absolute_import, unicode_literals



import heapq
from uuid import uuid1

import model
from util import now

def create_award(photo_uuid, kind, awarded_on=None):
    if awarded_on is None:
        awarded_on = now()
    award = model.Award(photo_uuid=photo_uuid,
                        uuid=uuid1(),
                        kind=kind,
                        awarded_on=awarded_on)
    award.save()
    return award

def get_awards_by_photo(photo_uuid):
    return list(model.Award.query(photo_uuid, consistent_read=False))

def leaderboard_awards():
    """Detect new leaderboard award winners and grant their awards."""
    # At the end of every week, #1 on the leaderboard earns a special award for their photo.  So do the runner-ups #2 and #3.
    # At the end of every week, #1 in each category earns a special award for their photo. So do the runner-ups #2 and #3.
    all_time_best = []

    for category in CATEGORIES:
        # Get the top 3 from the Week leaderboard for each category.
        items = list(model.WeekLeaderboard.score_index.query(
                    category, scan_index_forward=False, limit=4,
                    consistent_read=False))
        for i, item in enumerate(items):
            if 3 == i:
                break
            all_time_best.append(item)
            if 2 == i:
                create_award(item.uuid, 'Third Place in {}'.format(
                        category))
            elif 1 == i:
                create_award(item.uuid, 'Second Place in {}'.format(
                        category))
            elif 0 == i:
                create_award(item.uuid, 'First Place in {}'.format(
                        category))

    top = heapq.nlargest(3, all_time_best, key=lambda x: x.score)
    for i, item in enumerate(top):
        if 3 == i:
            break
        if 2 == i:
                create_award(item.uuid, 'Third Place overall')
        elif 1 == i:
                create_award(item.uuid, 'Second Place overall')
        elif 0 == i:
                create_award(item.uuid, 'First Place overall')
