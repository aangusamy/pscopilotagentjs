"""Verifies the short-lived token minted by IScript_PSAgentConfig.

Token format:  <OPRID>.<YYYYMMDD>.<ROLES>.<HashSHA256(secret|OPRID|YYYYMMDD|ROLES)>
ROLES are the user's agent roles from PeopleSoft (PSAGENT_ADMIN, PSAGENT_AUDITOR, PSAGENT_MONITOR,
PSAGENT_USER) without the prefix, joined by "+", e.g. "ADMIN+USER". Tokens from
the first iScript have no roles part (<OPRID>.<YYYYMMDD>.<hash of secret|OPRID|YYYYMMDD>);
they are still accepted and carry roles=None.

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
_ROLES = re.compile(r"[A-Z]+(\+[A-Z]+)*|")
ROLES = ("ADMIN", "AUDITOR", "MONITOR", "USER")   # PeopleSoft roles PSAGENT_ADMIN, _AUDITOR, _MONITOR, _USER


class AuthError(Exception):
    pass


def sign(secret, oprid, day, roles=None):
    """Base64 SHA-256 of "secret|OPRID|day[|roles]" in UTF-16LE, as PeopleCode HashSHA256 computes it."""
    text = f"{secret}|{oprid}|{day}" + (f"|{roles}" if roles is not None else "")
    return base64.b64encode(hashlib.sha256(text.encode("utf-16-le")).digest()).decode()


def make_token(secret, oprid, day=None, roles=None):
    """A token in the exact shape IScript_PSAgentConfig returns (for tests and local runs). roles: a list of
    ROLES, or None for the old shape without roles."""
    day = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    if roles is None:
        return f"{oprid}.{day}.{{9}}{sign(secret, oprid, day)}"
    joined = "+".join(roles)
    return f"{oprid}.{day}.{joined}.{{9}}{sign(secret, oprid, day, joined)}"


def identify(token, secret, now=None):
    """Return (OPRID, roles) for the token, or raise AuthError. roles is a frozenset of ROLES, or None for a
    token without roles (the first iScript)."""
    parts = token.rsplit(".", 3)
    if len(parts) == 4 and re.fullmatch(r"\d{8}", parts[1]) and _ROLES.fullmatch(parts[2]):
        oprid, day, roles, sig = parts
    else:
        roles = None
        try:
            oprid, day, sig = token.rsplit(".", 2)
        except ValueError as e:
            raise AuthError("malformed token") from e
    try:
        issued = datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise AuthError("malformed token") from e
    now = now or datetime.now(timezone.utc)
    if not (now - timedelta(days=2) < issued <= now + timedelta(days=1)):
        raise AuthError("token expired")
    sig = _PREFIX.sub("", sig)
    if not _OPRID.fullmatch(oprid) or not hmac.compare_digest(sig, sign(secret, oprid, day, roles)):
        raise AuthError("bad signature")
    return oprid, (frozenset(r for r in roles.split("+") if r in ROLES) if roles is not None else None)


def verify(token, secret, now=None):
    """Return the OPRID the token was issued for, or raise AuthError."""
    return identify(token, secret, now)[0]
