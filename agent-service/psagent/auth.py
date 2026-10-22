"""Verifies the short-lived token minted by IScript_PSAgentConfig.

Token format:  <OPRID>.<YYYYMMDD>.<base64(sha256(secret|OPRID|YYYYMMDD))>
PeopleCode's HashSHA256() returns exactly that base64 digest. The date makes
a token valid for the day it was issued (+/- 1 day for time zones).
"""

import base64
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone


# Printable ASCII without "|": also blocks SHA-256 length-extension forgeries,
# whose padding bytes would have to end up inside the OPRID.
_OPRID = re.compile(r"[!-{}~]{1,30}")


class AuthError(Exception):
    pass


def sign(secret, oprid, day):
    digest = hashlib.sha256(f"{secret}|{oprid}|{day}".encode()).digest()
    return base64.b64encode(digest).decode()


def make_token(secret, oprid, day=None):
    day = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{oprid}.{day}.{sign(secret, oprid, day)}"


def verify(token, secret, now=None):
    """Return the OPRID the token was issued for, or raise AuthError."""
    try:
        oprid, day, sig = token.rsplit(".", 2)
        issued = datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise AuthError("malformed token") from e
    now = now or datetime.now(timezone.utc)
    if not (now - timedelta(days=2) < issued <= now + timedelta(days=1)):
        raise AuthError("token expired")
    if not _OPRID.fullmatch(oprid) or not hmac.compare_digest(sig, sign(secret, oprid, day)):
        raise AuthError("bad signature")
    return oprid
