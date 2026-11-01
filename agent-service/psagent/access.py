"""Who may use what: the agent's four roles, from the user's PeopleSoft profile, signed into the token by the iScript.

  PSAGENT_USER     works in PeopleSoft through the agent, as themselves: tasks, live mode, documents, runbook runs,
                   Ask the data, Excel to CI, their own absence approvals, their own processes, their own access
  PSAGENT_MONITOR  also the watcher, anyone's processes, and the investigators' own read-only queries
  PSAGENT_ADMIN    also records tasks, publishes runbooks, regression tests, anyone's access (and Apply), everyone's
                   absence requests
  PSAGENT_AUDITOR  the admin dashboard and audit log (and the chat); kept apart, so administrators don't audit themselves

USER < MONITOR < ADMIN: each includes the one before. PeopleSoft's own security still applies inside every feature
(page and CI permissions, query trees, row security): the agent's role opens the door, PeopleSoft decides what is
behind it. The service checks these on every request; the widget only hides what a user can't use.
"""

WORK, MONITOR, ADMIN, AUDIT = "work", "monitor", "admin", "audit"

GRANTS = {"USER": {WORK}, "MONITOR": {WORK, MONITOR}, "ADMIN": {WORK, MONITOR, ADMIN}, "AUDITOR": {AUDIT}}
ROLE_OF = {WORK: "PSAGENT_USER", MONITOR: "PSAGENT_MONITOR", ADMIN: "PSAGENT_ADMIN", AUDIT: "PSAGENT_AUDITOR"}


def caps(roles):
    """The agent roles a user holds ({"USER", "ADMIN", ...}) -> what they may do ({"work", "monitor", ...})."""
    out = set()
    for r in roles or ():
        out |= GRANTS.get(str(r).upper(), set())
    return frozenset(out)


def needs(cap):
    """The refusal for a missing right, naming the role that gives it."""
    return f"this needs the {ROLE_OF[cap]} role on your PeopleSoft user profile"
