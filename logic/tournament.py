from __future__ import division, absolute_import, unicode_literals



from datetime import timedelta
from itertools import islice
from log import log
import random
from uuid import uuid1

from pynamodb.models import DoesNotExist

import model
from logic.score import predict_match
from util import generate_random_string, now


def _seed(tournament, photo_ids):
    photo_ids = iter(photo_ids)
    tournament.one = photo_ids.next().uuid
    tournament.two = photo_ids.next().uuid
    tournament.three = photo_ids.next().uuid
    tournament.four = photo_ids.next().uuid
    tournament.five = photo_ids.next().uuid
    tournament.six = photo_ids.next().uuid
    tournament.seven = photo_ids.next().uuid
    tournament.eight = photo_ids.next().uuid
    tournament.nine = photo_ids.next().uuid
    tournament.ten = photo_ids.next().uuid
    tournament.eleven = photo_ids.next().uuid
    tournament.twelve = photo_ids.next().uuid
    tournament.thirteen = photo_ids.next().uuid
    tournament.fourteen = photo_ids.next().uuid
    tournament.fifteen = photo_ids.next().uuid
    tournament.sixteen = photo_ids.next().uuid


def _seed_local(user, tournament):
    # Right now we get these at random, but at some point we will seed with
    # an algorithm, likely top 16 ranked.
    gender = 'm' if user.view_gender_male else 'f'
    g_l = '%s%s' % (gender, user.location.hex)
    photos = list(islice(model.Photo.post_date_index.query(
                            g_l, scan_index_forward=False,
                            copy_complete__eq=True,
                            consistent_read=False),
                    200))
    bad_photos = [p for p in photos if not p.copy_complete]
    if bad_photos:
        log.debug("copy_complete__eq not working, photos with copy_complete!=True in match-query results")
    photos = [p for p in photos if p.copy_complete]

    if len(photos) < 16:
        log.info("not enough photos to make tournament in %s", g_l)
        raise ValueError
    photo_ids = random.sample(photos, 16)
    return _seed(tournament, photo_ids)


def create_local_tournament(user):
    tournament = model.Tournament(user.uuid, uuid1())
    tournament.kind = 'local'
    tournament.lat = user.lat
    tournament.lon = user.lon
    tournament.geodata = user.geodata
    tournament.location = user.location
    _seed_local(user, tournament)
    tournament.save()
    return tournament


def create_regional_tournament(user):
    # Hack -- same as local. Fix for regionals!
    tournament = model.Tournament(user.uuid, uuid1())
    tournament.kind = 'regional'
    tournament.lat = user.lat
    tournament.lon = user.lon
    tournament.geodata = user.geodata
    tournament.location = user.location
    _seed_local(user, tournament)
    tournament.save()
    return tournament


def create_global_tournament(user):
    # Hack -- same as local. Fix for global!
    tournament = model.Tournament(user.uuid, uuid1())
    tournament.kind = 'global'
    tournament.lat = user.lat
    tournament.lon = user.lon
    tournament.geodata = user.geodata
    tournament.location = user.location
    _seed_local(user, tournament)
    tournament.save()
    return tournament

def confirm_tournament_status(user):
    if user.matches_until_next_tournament is None:
        init_tournament_status(user)


def advance_tournament_status(user):
    if 'local' == user.next_tournament:
        user.matches_until_next_tournament = 8
        user.next_tournament = 'regional'
    elif 'regional' == user.next_tournament:
        user.matches_until_next_tournament = 8
        user.next_tournament = 'global'
    elif 'global' == user.next_tournament:
        user.matches_until_next_tournament = 10
        user.next_tournament = 'local'
    user.save()


def init_tournament_status(user):
    user.last_tournament_status_access = now()
    user.matches_until_next_tournament = 10
    user.next_tournament = 'local'
    user.save()


def tournament_status_log_match(user):
    user.matches_until_next_tournament -= 1
    user.last_tournament_status_access = now()
    user.save()


def tournament_is_next(user):
    confirm_tournament_status(user)
    n = now()
    if n - user.last_tournament_status_access > timedelta(hours=1):
        init_tournament_status(user)
    else:
        user.last_tournament_status_access = n
        user.save()
    return user.matches_until_next_tournament <= 0


def get_next_tournament(user):
    if 'local' == user.next_tournament:
        tournament = create_local_tournament(user)
    elif 'regional' == user.next_tournament:
        tournament = create_regional_tournament(user)
    elif 'global' == user.next_tournament:
        tournament = create_global_tournament(user)
    else:
        raise ValueError
    advance_tournament_status(user)
    return tournament


def create_match(photo_a, photo_b, user):
    match = model.Match((photo_a.uuid, photo_b.uuid), user.uuid)
    match.proposed_date = now()
    match.lat = user.lat
    match.lon = user.lon
    match.geodata = user.geodata
    match.location = user.location
    # Set the a_win_delta, a_lose_delta, b_win_delta, b_lose_delta values.
    predict_match(match, photo_a, photo_b)
    match.save()
    return match
