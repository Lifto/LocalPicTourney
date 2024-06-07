from __future__ import division, absolute_import, unicode_literals



import datetime
from itertools import islice
import pytz
import random
import string
import re

CC_DELIM_RE = re.compile(r'\s*,\s*')


def now():
    return datetime.datetime.now(pytz.utc)


epoch = datetime.datetime(1970, 1, 1, tzinfo=pytz.utc)
def unix_time(dt):
    delta = dt - epoch
    return int(delta.total_seconds()*10000000)


def from_unix_time(ut):
    return epoch + datetime.timedelta(seconds=(ut / 10000000.0))


def generate_random_string(n=16):
    # http://stackoverflow.com/questions/2257441/random-string-generation-with-
    # upper-case-letters-and-digits-in-python/23728630#23728630
    return b''.join(random.SystemRandom().choice(
        string.ascii_letters + string.digits) for _ in xrange(n))


def patch_vary_headers(response, new_headers):
    """
    Adds (or updates) the "Vary" header in the given Response object.
    new_headers is a list of header names that should be in "Vary". Existing
    headers in "Vary" aren't removed.
    """
    # Note that we need to keep the original order intact, because cache
    # implementations may rely on the order of the Vary contents in, say,
    # computing an MD5 hash.
    vary_header_string = response.headers.get('Vary', None)
    if vary_header_string:
        vary_headers = CC_DELIM_RE.split(vary_header_string)
    else:
        vary_headers = []

    # Use .lower() here so we treat headers as case-insensitive.
    existing = set(header.lower() for header in vary_headers)
    additional = [h for h in new_headers if h.lower() not in existing]
    response.headers['Vary'] = ', '.join(vary_headers + additional)


# XXX move to another file?
from flask import g


def after_this_request(func):
    if not hasattr(g, 'call_after_request'):
        g.call_after_request = []
    g.call_after_request.append(func)
    return func

from itsdangerous import URLSafeTimedSerializer


def _get_token_serializer(secret_key, salt):
    return URLSafeTimedSerializer(secret_key=secret_key, salt=salt)


def random_iter(input_set):
    while input_set:
        next = random.sample(input_set, 1)
        input_set.remove(next)
        yield next


def random_by_twos(input_set):
    while len(input_set) >= 2:
        next1, next2 = random.sample(input_set, 2)
        input_set.remove(next1)
        input_set.remove(next2)
        yield next1, next2


def seconds_in_units(seconds):
    """Get a tuple with most appropriate unit and value for seconds given.

    >>> seconds_in_units(7700)
    (2, 'hour')

    """
    unit_limits = [("year", 365 * 24 * 3600),
                   ("month", 30 * 24 * 3600),
                   ("week", 7 * 24 * 3600),
                   ("day", 24 * 3600),
                   ("hour", 3600),
                   ("minute", 60)]
    for unit_name, limit in unit_limits:
        if seconds >= limit:
            amount = int(round(float(seconds) / limit))
            return amount, unit_name
    return seconds, "second"


def microseconds_in_units(microseconds):
    unit_limits = [("millisecond", 1000)]
    for unit_name, limit in unit_limits:
        if microseconds >= limit:
            amount = int(round(float(microseconds) / limit))
            return amount, unit_name
    return microseconds, "microsecond"


# Singular and plural forms of time units in your language.
unit_names = dict(en = {"year" : ("year", "years"),
                        "month" : ("month", "months"),
                        "week" : ("week", "weeks"),
                        "day" : ("day", "days"),
                        "hour" : ("hour", "hours"),
                        "minute" : ("minute", "minutes"),
                        "second" : ("second", "seconds"),
                        "millisecond" : ("millisecond", "milliseconds"),
                        "microsecond" : ("microsecond", "microseconds")})


def stringify(td):
    """Converts a timedelta into a nicely readable string.

    >>> td = timedelta(days = 77, seconds = 5)
    >>> print stringify(td)
    two months

    """
    seconds = td.days * 3600 * 24 + td.seconds
    if seconds == 0:
        amount, unit_name = microseconds_in_units(td.microseconds)
    else:
        amount, unit_name = seconds_in_units(seconds)

    # Localize it.
    # No longer necessary: always display the digits, not words
#    i18n_amount = amount_to_str(amount, unit_name)
    i18n_unit = unit_names['en'][unit_name][1]
    if amount == 1:
        i18n_unit = unit_names['en'][unit_name][0]
    return "%s %s" % (amount, i18n_unit)


def took(*args):
    """Return a stringified time, or a statement like 7 pips took 5 seconds."""
    if len(args) == 1:
        return stringify(now()-args[0])
    elif len(args) == 3:
        return u'%s took %s' % (pluralize(args[0], args[1]),
                                stringify(now()-args[2]))
    elif len(args) == 4:
        return u'%s took %s' % (pluralize(args[0], args[1], args[2]),
                                stringify(now()-args[3]))
    else:
        raise ValueError # What is the right exception to raise if the args are wrong?


def since(hsn_time):
    ago_delta = now() - hsn_time
    ago = u''
    if ago_delta < datetime.timedelta(minutes=1):
        ago = u'less than a minute'
    else:
        ago = u'%s' % stringify(ago_delta)
    return ago


def pluralize(count, singular, plural='%ss'):
    """Pluralizes a number.

    >>> pluralize(22, 'goose', 'geese')
    '22 geese'

    If a list or set is given its length is used for the count.
    Notice the plural is not needed in the simple append-an-s case.

    >>> pluralize([1,2,3], 'bird')
    '3 birds'

    If the plural string contains a %s the singluar string is substituted in.

    >>> pluralize(5, 'potato', '%ses')
    '5 potatoes'

    None and 0 are acceptable inputs.

    >>> pluralize(None, 'fallacy', 'fallacies')
    'no fallacies'

    Negative input is acceptable.
    >>> pluralize(-1, 'unit')
    '-1 unit'
    >>> pluralize(-10, 'unit')
    '-10 units'

    """
    if hasattr(count, '__iter__'):
        count = len(count)
    if '%s' in plural:
        plural = plural % singular
    if count is None or count == 0:
        return "no %s" % plural
    elif count == 1 or count == -1:
        return "1 %s" % singular
    else:
        return "%s %s" % (count, plural)

FIRST_CAP_RE = re.compile('(.)([A-Z][a-z]+)')
ALL_CAP_RE = re.compile('([a-z0-9])([A-Z])')
def from_camel(txt):
    """
    >>> from_camel('CamelCase')
    'camel_case'

    """
    s1 = FIRST_CAP_RE.sub(r'\1_\2', txt)
    return ALL_CAP_RE.sub(r'\1_\2', s1).lower()


def dedupe(input, attr=None):
    """Iterator that deduplicates an iterator."""
    founds = set()
    for item in input:
        if attr is None:
            val = item
        else:
            val = getattr(item, attr)
        if val in founds:
            continue
        founds.add(val)
        yield item


def count_iter(i, count):
    yield_count = 0
    while True:
        yield i.next()
        yield_count += 1
        if yield_count == count:
            raise StopIteration

def grouper(n, iterable):
    it = iter(iterable)
    while True:
       chunk = tuple(islice(it, n))
       if not chunk:
           return
       yield chunk

def pad_center(text, pad='-', length=60):
    if len(text) > (length-4):
        return text

    pad_length = length - (len(text) + 2)
    left_pad = int(pad_length / 2)
    right_pad = pad_length - left_pad
    return '{} {} {}'.format(pad*left_pad, text, pad*right_pad)
