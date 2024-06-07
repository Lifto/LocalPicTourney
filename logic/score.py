from __future__ import division, absolute_import, unicode_literals


from datetime import timedelta
from time import sleep
from uuid import UUID

from boto.kinesis.exceptions import InvalidArgumentException, \
    ProvisionedThroughputExceededException
from botocore.vendored.requests.exceptions import ConnectionError
from pynamodb.exceptions import DeleteError
from pynamodb.models import DoesNotExist

from logic import kinesis
from logic.location import LOCATIONS
from log import log
from logic.glicko2 import Glicko2, WIN, LOSS
from model import get_one, Match, Photo, PhotoGenderTag, HourLeaderboard, \
    MonthLeaderboard, ShardIterator, TodayLeaderboard, User, WeekLeaderboard, \
    YearLeaderboard
from logic.photo import get_photo_hour, get_photo_month, get_photo_today, \
    get_photo_week, get_photo_year
from logic import sentry
from settings import settings
from util import now, took


TAU = 0.2


def encode_match(match):
    photo_a_uuid, photo_b_uuid = match.photo_uuids
    return '%s%s%s' % (photo_a_uuid.hex, photo_b_uuid.hex, match.user_uuid.hex)

def decode_match(data):
    return Match.get((UUID(data[:32]), UUID(data[32:64])), UUID(data[64:96]),
                     consistent_read=False)

def log_match_for_scoring(match):
    data = encode_match(match)
    partition_key = '0'
    log.info("log_match_for_scoring {} {} {}".format(settings.SCORE_STREAM, data, partition_key))
    kinesis.get_kinesis().put_record(settings.SCORE_STREAM, data, partition_key)

def get_scores():
    result = []
    log.info("get_scores {}".format(settings.SCORE_STREAM))
    for item in kinesis.get_all(settings.SCORE_STREAM):
        try:
            match = decode_match(item)
        except Exception as e:
            log.error('decode_match had error %s', e)
            log.exception(e)
            log.info('ignoring data %s', item)
        else:
            result.append(match)
    return result

def process_scores(matches):
    """
    Given a list of (winner_g_l, winner_uuid,
    loser_g_l, loser_uuid,) tuples, run them through glicko2 and
    update the score, phi and sigma values in DynamoDB.

    """

    # Re: provision error. if there is a provision error in here the batch
    # of scores is lost. They were snarfed from kinesis, if this crashes,
    # the snarf picks up from the last snarf.

    log.info("scoring - process_scores called with match iter")
    run_time = now()
    photos_by_uuid = {}
    def get_photo(photo_uuid):
        try:
            return photos_by_uuid[photo_uuid]
        except KeyError:
            photo = get_one(Photo, 'uuid_index', photo_uuid,
                            consistent_read=False)
            photos_by_uuid[photo_uuid] = photo
            return photo

    # http://www.glicko.net/glicko/glicko2.pdf
    # https://github.com/sublee/glicko2
    #  (a) If the player is unrated, set the rating to 1500 and the RD to
    #  350. Set the player's volatility to 0.06
    # (this value depends on the
    # particular application).
    # (b) Otherwise, use the player's
    # most recent rating, RD (rating deviation, phi), and
    # volatility sigma.
    # Step 2. For each player, convert the ratings and RD's onto the
    # Glicko2 scale:
    # mu = (r - 1500)/173.7178
    # phi = RD/173.7178
    # The value of sigma, the volatility, does not change.
    #
    # (Glicko-2) rating mu, rating deviation phi, and volatility sigma

    photo_to_matches = {}  # photo_uuid -> match
    user_to_matches = {}  # user_uuid -> match
    # We get events racked up about matches that got judged and need
    # post processing. Simulate them here.
    # * for winner, loser in list of matches that happened since last scoring.
#    for (winner_g_l, winner_uuid, loser_g_l, loser_uuid) in winner_loser_tups:
    for match in matches:
        a = get_photo(match.photo_uuids[0])
        b = get_photo(match.photo_uuids[1])
        if match.a_won:
            w = a
            l = b
        else:
            w = b
            l = a
        to_winner = (WIN, l.score, l.phi, l.sigma,)
        try:
            photo_to_matches[w.uuid].append(to_winner)
        except KeyError:
            photo_to_matches[w.uuid] = [to_winner]
        to_loser = (LOSS, w.score, w.phi, w.sigma)
        try:
            photo_to_matches[l.uuid].append(to_loser)
        except KeyError:
            photo_to_matches[l.uuid] = [to_loser]
        match.scored_date = run_time
        match.save()
        if w.user_uuid not in user_to_matches:
            user_to_matches[w.user_uuid] = {}
        if 'wins' not in user_to_matches[w.user_uuid]:
            user_to_matches[w.user_uuid]['wins'] = []
        user_to_matches[w.user_uuid]['wins'].append(w)
        if l.user_uuid not in user_to_matches:
            user_to_matches[l.user_uuid] = {}
        if 'losses' not in user_to_matches[l.user_uuid]:
            user_to_matches[l.user_uuid]['losses'] = []
        user_to_matches[l.user_uuid]['losses'].append(l)
    log.info("number of keys to score-adjust %s" % len(photo_to_matches.keys()))

    for photo_uuid, record in photo_to_matches.iteritems():
        env = Glicko2(tau=TAU)
        photo = get_photo(photo_uuid)
        rating = env.create_rating(photo.score, photo.phi, photo.sigma)
        rating_record = []
        for result, score, phi, sigma in record:
            opponent_rating = env.create_rating(score, phi, sigma)
            rating_record.append((result, opponent_rating,))
        # Glicko2 calculation.
        rated = env.rate(rating, rating_record)
        before = "%s %s %s" % (photo.score, photo.phi, photo.sigma)
        score = rated.mu
        photo.score = score
        photo.phi = rated.phi
        photo.sigma = rated.sigma
        log.info("rating change for %s from %s to %s %s %s from a record count of %s",
            photo_uuid.hex, before, score,
            photo.phi, photo.sigma, len(record))
        # This could fail due to provision error.
        photo.save()
        for tag in photo.get_tags():
            gender = 'm' if photo.get_is_gender_male() else 'f'
            gender_tag = '{}_{}'.format(gender, tag.lower())
            # TODO: Could be a blind update.
            try:
                photo_gender_tag = PhotoGenderTag.get(gender_tag, photo_uuid,
                                                      consistent_read=False)
            except DoesNotExist:
                msg = "PhotoGenderTag({}, {}) did not exist, creating".format(
                    gender_tag, photo_uuid.hex)
                log.warn(msg)
                import model
                photo_gender_tag = model.PhotoGenderTag(gender_tag.lower(),
                                                        uuid=photo.uuid,
                                                        score=score,
                                                        tag_with_case=tag)
                photo_gender_tag.save()
            else:
                photo_gender_tag.score = score
                photo_gender_tag.save()

        hour_leaderboard = get_photo_hour(photo.uuid)
        if hour_leaderboard is None:
            hour_leaderboard = HourLeaderboard(photo.gender_location,
                                               uuid=photo.uuid,
                                               post_date=photo.post_date)
        hour_leaderboard.score = rated.mu
        hour_leaderboard.save()

        today_leaderboard = get_photo_today(photo.uuid)
        if today_leaderboard is None:
            today_leaderboard = TodayLeaderboard(photo.gender_location,
                                                 uuid=photo.uuid,
                                                 post_date=photo.post_date)
        today_leaderboard.score = rated.mu
        today_leaderboard.save()

        week_leaderboard = get_photo_week(photo.uuid)
        if week_leaderboard is None:
            week_leaderboard = WeekLeaderboard(photo.gender_location,
                                               uuid=photo.uuid,
                                               post_date=photo.post_date)
        week_leaderboard.score = rated.mu
        week_leaderboard.save()

        month_leaderboard = get_photo_month(photo.uuid)
        if month_leaderboard is None:
            month_leaderboard = MonthLeaderboard(photo.gender_location,
                                                 uuid=photo.uuid,
                                                 post_date=photo.post_date)
        month_leaderboard.score = rated.mu
        month_leaderboard.save()

        year_leaderboard = get_photo_year(photo.uuid)
        if year_leaderboard is None:
            year_leaderboard = YearLeaderboard(photo.gender_location,
                                               uuid=photo.uuid,
                                               post_date=photo.post_date)
        year_leaderboard.score = rated.mu
        year_leaderboard.save()

    for user_uuid, wins_and_losses in user_to_matches.iteritems():
        wins = wins_and_losses.get('wins', [])
        win_count = len(wins)
        losses = wins_and_losses.get('losses', [])
        loss_count = len(losses)

        user = User.get(user_uuid, consistent_read=False)
        if user is None:
            user = User.get(user_uuid)
        user.win_count += win_count
        user.loss_count += loss_count
        user.save()

def do_scores():
    return process_scores(get_scores())

def predict_match(match, photo_a, photo_b):
    match.a_win_delta = predict_photo(WIN, photo_a, photo_b)
    match.a_lose_delta = predict_photo(LOSS, photo_a, photo_b)
    match.b_win_delta = predict_photo(WIN, photo_b, photo_a)
    match.b_lose_delta = predict_photo(LOSS, photo_b, photo_a)

def predict_photo(result, photo_a, photo_b):
    original_rating = photo_a.score
    env = Glicko2(tau=TAU)
    rating_a = env.create_rating(photo_a.score, photo_a.phi, photo_a.sigma)
    rating_b = env.create_rating(photo_b.score, photo_b.phi, photo_b.sigma)
    rating_record = [(result, rating_b)]
    rated = env.rate(rating_a, rating_record)
    return rated.mu - original_rating

def _trim(query, cutoff_datetime):
    """Delete all items older than the cutoff date."""
    trims = 0
    for location in LOCATIONS.keys():
        for gender in ['m', 'f']:
            gender_location = '%s%s' % (gender, location.hex)
            start_time = now()
            item_count = 0
            for item in query(gender_location, consistent_read=False):
                if item.post_date < cutoff_datetime:
                    while True:
                        try:
                            # TODO: Retry should be in pynamodb, not sure
                            # why it is not automatic.
                            # I put this in because I found this exception
                            # in the logs:
                            # DeleteError: Failed to delete item: An error occurred (ProvisionedThroughputExceededException) when calling the DeleteItem operation: The level of configured provisioned throughput for the table was exceeded. Consider increasing your provisioning level with the UpdateTable API
                            item.delete()
                        except DeleteError:
                            sleep(0.2)
                        else:
                            break
                    item_count += 1
                else:
                    break
            took_str = took(item_count, 'entry', 'entries', start_time)
            log.info("leaderboard trim %s, %s" % (gender_location, took_str))
            trims += 1
    return trims

def trim_hour_leaderboards():
    start_time = now()
    count = _trim(HourLeaderboard.date_index.query, now()-timedelta(hours=1))
    log.info("trimming hour leaderboards, %s" %
                took(count, 'leaderboard', start_time))

def trim_today_leaderboards():
    start_time = now()
    count = _trim(TodayLeaderboard.date_index.query, now()-timedelta(days=1))
    log.info("trimming today leaderboards, %s" %
                took(count, 'leaderboard', start_time))

def trim_week_leaderboards():
    start_time = now()
    count = _trim(WeekLeaderboard.date_index.query, now()-timedelta(days=7))
    log.info("trimming week leaderboards, %s" %
                took(count, 'leaderboard', start_time))

def trim_month_leaderboards():
    start_time = now()
    count = _trim(MonthLeaderboard.date_index.query, now()-timedelta(days=31))
    log.info("trimming month leaderboards, %s" %
                took(count, 'leaderboard', start_time))

def trim_year_leaderboards():
    start_time = now()
    count = _trim(YearLeaderboard.date_index.query, now()-timedelta(days=365))
    log.info("trimming year leaderboards, %s" %
                took(count, 'leaderboard', start_time))

