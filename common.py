"""Shared contract of the PANAH engines: API error and UTC time.

Error for the client:
    from server.common import ApiError
    raise ApiError(404, 'not_found', 'هشدار پیدا نشد.')
    raise ApiError(429, 'too_soon', 'لطفاً کمی صبر کنید.', resend_in=30)   # extra fields go into the response
web turns it into a response with HTTP code status and JSON {"error": code, "message": message_fa, ...extra}.

Time is UTC ISO 8601 everywhere, like '2026-11-06T01:20:00Z'. Strings of this form are compared as plain strings:
    expires_at > utcnow()   # not expired yet
"""
import datetime
import re

TIME_FORMAT = '%Y-%m-%dT%H:%M:%SZ'


class ApiError(Exception):
    """Error for the client response: HTTP code, reason code in Latin letters and a text in Dari."""

    def __init__(self, status, code, message_fa='', **extra):
        super().__init__('%s %s' % (status, code))
        self.status = int(status)
        self.code = str(code)
        self.message_fa = message_fa or ''
        self.extra = extra

    def to_dict(self):
        """Response body: {"error": code, "message": message_fa, ...extra}."""
        data = {'error': self.code, 'message': self.message_fa}
        data.update(self.extra)
        return data


def utcnow(seconds=0):
    """Now (or in `seconds` seconds, may be negative) as a string '2026-11-06T01:20:00Z'."""
    moment = datetime.datetime.now(datetime.timezone.utc)
    if seconds:
        moment += datetime.timedelta(seconds=seconds)
    return moment.strftime(TIME_FORMAT)


def to_utc(value):
    """datetime (naive is taken as UTC) or Unix seconds (int/float) → string '…Z'."""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        moment = value.astimezone(datetime.timezone.utc)
    else:
        moment = datetime.datetime.fromtimestamp(float(value), datetime.timezone.utc)
    return moment.strftime(TIME_FORMAT)


def parse_utc(text):
    """ISO 8601 string ('…Z', '…+04:30', with or without fractional seconds, date only) → datetime in UTC.

    No time zone means UTC. An invalid string → ValueError.
    """
    value = str(text).strip()
    if value[-1:] in ('Z', 'z'):
        value = value[:-1] + '+00:00'
    fraction = re.match(r'^(.+[T ]\d\d:\d\d:\d\d)[.,](\d+)(.*)$', value)
    if fraction:  # Python 3.10 only parses 3 or 6 digits of fractional seconds
        head, digits, tail = fraction.groups()
        value = head + '.' + (digits + '000000')[:6] + tail
    moment = datetime.datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc)
