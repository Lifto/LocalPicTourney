from __future__ import division, absolute_import, unicode_literals


class InvalidAPIUsage(Exception):
    """Flask can use this Exception to give meaningful errors for bad input."""
    pass

class InsufficientAuthorization(Exception):
    """Raised when performing an action with insufficient reg status."""
    pass

class NotFound(Exception):
    """Raised when a record can not be found."""
    pass

class FacebookError(Exception):
    """Raised when we encounter an error accessing Facebook graph API."""
    pass
