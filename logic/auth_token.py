from __future__ import division, absolute_import, unicode_literals



import base64
from uuid import UUID

from flask import abort, _request_ctx_stack
from pynamodb.models import DoesNotExist
from werkzeug.local import LocalProxy

from log import log
from model import User
from util import after_this_request, patch_vary_headers


# -- Auth Notes ---------------------------------------------------------------
# Ocean uses the HTTP Authentication header. The header includes the user's
# uuid and a secret token.
#
# Constructing the Authorization Header:
# Username and token are combined into a string "username:token"
# This string is then encoded using the RFC2045-MIME variant of Base64
# The authorization method and a space i.e. "Basic " is then put before the
# encoded string.
# Example: If the user agent uses 'Aladdin' as the username and
#  'open sesame' as the token then the header is formed as follows:
#
# Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==
#
# Server Responses:
# If a URL is public then no authorization header is needed (and is ignored.)
# If a URL requires authorization the Server will reply with HTTP 401 Not
# Authorized response code containing a WWW-Authenticate HTTP header.
# The WWW-Authenticate header looks like this:
# WWW-Authenticate: Basic realm="ocean"
#
# When writing application request handlers this is all encapsulated in
# current_api_user() which will return a Model.User object representing the
# user specified in the auth header, or it will cause your handler to return
# a 401.  If you are having auth issues the reasons for auth rejection are
# logged at the DEBUG level.

# -- Auth Implementation ------------------------------------------------------

def _get_api_user():
    # any time the token is accessed, assume the vary should be applied
    @after_this_request
    def add_vary_authorization(response):
        patch_vary_headers(response, ['Authorization'])
        return response

    headers = _request_ctx_stack.top.request.headers
    authorization_header = headers.get('Authorization')
    if not authorization_header:
        log.debug('auth fail: no authorization header. (if you are sending the header, is WSGIPassAuthorization On set in wsgi.conf? see .ebextensions/auth_headers.config')
        abort(401)
    if not authorization_header.startswith('Authorization: Basic '):
        log.debug('auth fail: must start with "Authorization: Basic "')
        log.debug('    header was "%s"', authorization_header)
        abort(401)
    # Trim 'Authorization: Basic ' from the string, the length of which is 21.
    uuid_and_token_b64 = authorization_header[21:]
    try:
        uuid_and_token = base64.b64decode(uuid_and_token_b64)
    except TypeError:
        log.debug('auth fail because could not b64decode uuid and token')
        abort(401)

    try:
        user_uuid_str, token = uuid_and_token.split(':', 1)
    except ValueError:
        abort(400)

    try:
        user_uuid = UUID(user_uuid_str)
    except ValueError:
        abort(400)

    try:
        user = User.get(user_uuid, consistent_read=False)
    except DoesNotExist:
        user = None
    if user is None:
        try:
            user = User.get(user_uuid)
        except DoesNotExist:
            user = None

    if user is None:
        log.debug('auth failed because User.get returned None')
        abort(401)
    if not user.token == token:
        log.debug('auth failed because User token does not match header.')
        abort(401)
    if not user.auth_enabled:
        log.debug('auth failed because User.auth_enabled != True')
        abort(401)
    return user
current_api_user = LocalProxy(lambda: _get_api_user)
