"""Verifies the short-lived token minted by IScript_PSAgentConfig.

Token format:  <OPRID>.<YYYYMMDD>.<HashSHA256(secret|OPRID|YYYYMMDD)>
PeopleCode's HashSHA256() hashes the string as UTF-16LE (PeopleSoft's internal
encoding) and returns "{9}" followed by the base64 digest; checked on a
PeopleTools 8.5x system on 2026-10-23. The prefix is accepted with or without
braces content. The date makes a token valid for the day it was issued
(+/- 1 day for time zones).
"""

import base64
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone


# Printable ASCII without "|": also blocks SHA-256 length-extension forgeries,
# whose padding bytes would have to end up inside the OPRID.
_OPRID = re.compile(r"[!-{}~]{1,30}")
_PREFIX = re.compile(r"^\{[^}]{0,8}\}")  # PeopleSoft's hash version marker, e.g. "{9}"


class AuthError(Exception):
    pass


def sign(secret, oprid, day):
    """Base64 SHA-256 of "secret|OPRID|day" in UTF-16LE, as PeopleCode HashSHA256 computes it."""
    digest = hashlib.sha256(f"{secret}|{oprid}|{day}".encode("utf-16-le")).digest()
    return base64.b64encode(digest).decode()


def make_token(secret, oprid, day=None):
    """A token in the exact shape IScript_PSAgentConfig returns (for tests and local runs)."""
    day = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{oprid}.{day}.{{9}}{sign(secret, oprid, day)}"


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
    sig = _PREFIX.sub("", sig)
    if not _OPRID.fullmatch(oprid) or not hmac.compare_digest(sig, sign(secret, oprid, day)):
        raise AuthError("bad signature")
    return oprid
