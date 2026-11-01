# Implemented features: review list

What the PeopleSoft Agent does today, who can use it, what has been proven on the real system (HR92U052, PeopleTools
8.61), and the known gaps to tighten. Details of each feature are in [FEATURES.md](FEATURES.md); setup is in
[agent-service/README.md](agent-service/README.md).

*Proven* means tested on the real PeopleSoft system. *Tested* means only against mock pages and a scripted Claude.

| # | Feature | Who | Status |
|---|---|---|---|
| 1 | Chat widget | Everyone with a PSAGENT role | Proven |
| 2 | Tasks: the agent does the work | PSAGENT_USER | Proven |
| 3 | Recorder: teach a task by doing it once | PSAGENT_ADMIN (teachers) | Proven |
| 4 | Live mode: pages it was never taught | PSAGENT_USER | Proven |
| 5 | Live recovery: a recorded task meets a changed page | PSAGENT_USER | Tested |
| 6 | Documents: a form fills PeopleSoft | PSAGENT_USER | Tested |
| 7 | Process troubleshooter | PSAGENT_USER | Proven |
| 8 | Security access troubleshooter | Security administrators | Proven |
| 9 | Absence approval reviewer | Approvers | Proven |
| 10 | Runbooks from a checklist | Build: PSAGENT_ADMIN; run: PSAGENT_USER | Proven |
| 11 | Watcher: watchdogs and alert emails | PSAGENT_MONITOR | Proven |
| 12 | Roles from PeopleSoft | Everyone | Proven |
| 13 | Regression tests | PSAGENT_ADMIN, test environments only | Partly proven |
| 14 | Feature bar (＋ Features) | Everyone, by role | Tested |
| 15 | Admin dashboard and audit log | PSAGENT_AUDITOR | Tested |
| 16 | Excel to CI | PSAGENT_USER with CI access | Partly proven |
| 17 | Ask the data (curated AI_ PS Queries) | PSAGENT_USER | Partly proven |
| 18 | Platform: read-only database, tokens, masking, tests | n/a | Proven |

See [Security: who can use what](#security-who-can-use-what) for the access by role.

---

## 1. Chat widget
- **Does:** a panel docked on every PeopleSoft page: welcome tiles by role, the page the user is on and its error, an activity feed, history per session, Ctrl+/.
- **Proven:** loaded through Branding on HR92U052; sign-on through the `WEBLIB_PSAGENT` iScript token.
- **Gaps:** chat history lasts only for the browser session; new conversations start empty.

## 2. Tasks: the agent does the work
- **Does:** collects the values in the chat, shows a confirm card (the password is typed there), then runs the steps on screen and stops on PeopleSoft errors.
- **Proven:** Create User Profile, Sync Portal Security, Run an Application Engine program.
- **Gaps:** only 3 tasks so far; classic pages only (no Fluid).

## 3. Recorder: teach a task by doing it once
- **Does:** records search pages, transaction pages, grids and the Process Scheduler pop-up; Claude suggests the fields; the teacher reviews and saves.
- **Proven:** recorded User Profile and a process run on the real system.
- **Gaps:** classic pages only; one component per task.

## 4. Live mode: pages it was never taught
- **Does:** Claude reads the page, decides the next move, acts; it asks for missing values; saving needs the user's approval click (enforced by the service).
- **Proven:** on real classic pages.
- **Gaps:** classic pages only; each step is a Claude call, so it is slower and costs more than a recorded task.

## 5. Live recovery
- **Does:** when a recorded task fails on a changed page, live mode finishes it (Save approved again) and offers to update the task.
- **Tested:** browser test with a moved field.
- **Gaps:** not yet seen on a real page change (for example after a PUM image).

## 6. Documents
- **Does:** reads an attached PDF, image or spreadsheet, flags unclear values, shows several people in one review table, runs them one by one.
- **Tested:** browser test with a two-person access form.
- **Gaps:** one task per document; documents are not kept, so there is nothing to look back at.

## 7. Process troubleshooter
- **Does:** reads the log files (in the user's session), the message log, Application Engine code and PeopleCode with statement numbers, overlapping runs and data (read-only); a diagnosis card with the root cause, evidence and fix.
- **Proven:** AEMINITEST 557060 (division by zero, statement 3) and PORTAL_CSS 557046 (three runs at once, Max Concurrent 0).
- **Gaps:** strongest on Application Engine; SQR and COBOL source is not read; Integration Broker failures are not covered.

## 8. Security access troubleshooter
- **Does:** walks the security chain (component, roles, permission lists, actions, menu, row security, HCM SJT), finds the smallest role that grants access and its side effects; **Apply** makes the change live and stops before Save.
- **Proven:** ABSMITH (Hiring Manager, 62 components) and VELU (empty SJT_OPR_CLS).
- **Gaps:** no query, process or Finance row security checks; row-security refresh is not applied by the agent.

## 9. Absence approval reviewer
- **Does:** lists pending absence requests; **Review with AI** checks balance, overlaps, team coverage, timing and history; recommendation with a confidence score; the approver decides in PeopleSoft.
- **Proven:** request 1589 (needs your review, 65/100).
- **Gaps:** absence requests only (not timesheets, promotions, job openings).

## 10. Runbooks from a checklist
- **Does:** an Excel, CSV or PDF checklist becomes checks (read-only queries, test-run), tasks, processes, approvals and manual steps; publish, then run step by step.
- **Proven:** the 5-step test checklist drafted by real Claude in 30 seconds; a process followed to Success (PORTAL_CSS 557093).
- **Gaps:** an approval is a click in the same browser, not routed to the approver; a run doesn't survive closing the browser; edits mean re-uploading the checklist.

## 11. Watcher: watchdogs and alert emails
- **Does:** failure, stuck, must-succeed-by and data-check watchdogs, from a template or the chat; preview of what each would have caught; every failure investigated; email with the analysis and the action needed; manage, pause, delete, download.
- **Proven:** AEMINITEST 557089 analysed in 43 seconds; 60 stuck requests found; the template read by real Claude.
- **Gaps:** email only (no Teams); **the SMTP server is not chosen yet**; nothing is rerun (Fix and rerun is a future feature).

## 12. Roles from PeopleSoft
- **Does:** PSAGENT_USER, _MONITOR, _ADMIN, _AUDITOR on the user profile, signed into the token; no role, no widget. What each role may use is in [Security: who can use what](#security-who-can-use-what).
- **Proven:** USER, MONITOR, ADMIN on HR92U052.
- **Gaps:** **the PSAGENT_AUDITOR role is not created yet and the updated iScript is not deployed.**

## 13. Regression tests
- **Does:** smoke test of every task; test plans from a template; passed, passed with changes, failed; compared with the last run; run here or unattended (daily, or after a patch).
- **Proven:** the headless runner signed in to PeopleTools 8.61.
- **Gaps:** **unattended runs need PSAGENT_TEST_USER (not set)**; test data is not cleaned up after a run.

## 14. Feature bar (＋ Features)
- **Does:** the guided flows by role: watch jobs, manage watchdogs, regression tests, runbooks, troubleshoot, access check, approvals, record a task, ask the data, load with a CI, audit.
- **Tested:** browser tests.
- **Gaps:** none known.

## 15. Admin dashboard and audit log
- **Does:** every Claude call (tokens, cost), database query, task run, save and alert recorded; overview, usage and cost, audit log, failures, watcher and tests; Excel exports; budget alerts; weekly email; chat questions for auditors.
- **Tested:** browser test with 30 days of seeded history.
- **Gaps:** **real data starts after the service restart**; the budget and weekly emails need the SMTP server.

## 16. Excel to CI
- **Does:** pick a CI, download its template, upload it filled in; every row checked; loaded through `WEBLIB_SOAPTOCI` in the user's session, so it is saved as them; results and the rows to fix.
- **Proven:** all 457 CIs through template, checks and requests (offline); the Update keys found an existing record for 250 CIs on the live system (read-only); formats proven (`?postDataBin=y`, dates, datetimes).
- **Gaps:** **no real Create or Update has been run yet**; 5,000 rows per load; prompts set at runtime (`%EDITTABLE`) are not checked; very large files through the Process Scheduler are a future feature.

## 17. Ask the data (curated AI_ PS Queries)
- **Does:** Claude picks one of the user's `AI_` queries and its prompt values; the browser runs it with Query Viewer's Run to Excel link, as the user; a card with the rows, a chart, Download Excel; Claude sees totals, never all the rows; for "who has X" it looks up the matching rows (`find_rows`: the count and the first 25, no sensitive columns) and the user gets a card of them.
- **Proven:** the catalog per user (PS and VP1: 2 queries, PSAGENT_SVC: none), the link with prompt values, the date format, reading a real answer (324 rows); AI_USERS_WITH_ROLE run from the chat (11,646 rows).
- **Gaps:** small groups (a count of 1) are shown to Claude; sensitive columns are found by name only.

## 18. Platform
- **Does:** read-only database (single SELECT, read-only transaction, rollback), 200 rows and 20 seconds per query, sensitive columns masked, every query logged; signed tokens; CORS limited to PeopleSoft; 232 unit tests (one calls the real Claude API on request) and 23 browser tests.
- **Gaps:** **the database account is SYSADM** (a dedicated read-only `PSAGENT_RO` is planned); one worker with in-memory sessions; reached through ngrok; keys in `.env`.

---

## Security: who can use what

The four agent roles are given on the user's PeopleSoft profile and signed into the token by the `WEBLIB_PSAGENT`
iScript. **USER < MONITOR < ADMIN**: each includes the one before. **AUDITOR** is kept apart, so administrators don't
audit themselves; give a person both roles if they need both.

| Feature | USER | MONITOR | ADMIN | AUDITOR |
|---|:-:|:-:|:-:|:-:|
| Chat, explain a page or an error | ✓ | ✓ | ✓ | ✓ |
| Run recorded tasks, live mode, documents | ✓ | ✓ | ✓ | |
| Run published runbooks | ✓ | ✓ | ✓ | |
| Ask the data (AI_ queries) | ✓ | ✓ | ✓ | |
| Excel to CI | ✓ | ✓ | ✓ | |
| Absence approval reviewer, own approvals | ✓ | ✓ | ✓ | |
| Troubleshoot **own** processes | ✓ | ✓ | ✓ | |
| Troubleshoot **anyone's** processes, with Claude's own read-only queries | | ✓ | ✓ | |
| Watcher: watchdogs, alerts, manage | | ✓ | ✓ | |
| Check **my own** access (*"why can't I open…"*) | ✓ | ✓ | ✓ | |
| Check **anyone's** access, and Apply a fix | | | ✓ | |
| Record tasks, publish runbooks | | | ✓ | |
| Regression tests (test environments) | | | ✓ | |
| Everyone's absence requests | | | ✓ | |
| Admin dashboard and audit log | | | | ✓ |

**How it is enforced:**

- **By the service, on every request.** Each request's rights are worked out from its signed token
  (`agent-service/psagent/access.py`), so a role taken away in PeopleSoft stops working at once, even in an open
  conversation. The widget only hides what a user can't use.
- **PeopleSoft's own security still applies inside each feature.** Tasks, live mode, Excel to CI and Ask the data run
  in the user's own session: their page and CI permissions, query trees and row security decide what they can do. The
  agent's role decides which features they see.
- **Claude's own read-only queries** (in the troubleshooter, the access checker and the absence reviewer) are only for
  PSAGENT_MONITOR and ADMIN. A plain user's investigation uses the fixed lookups only (the request, the message log,
  the code, the log files), and other people's runs of the same process are shown without their user and run control.
- **A token without any agent role** gets no widget and is refused. A token from an iScript without roles counts as
  PSAGENT_USER.
- **The `.env` lists** (`PSAGENT_TEACHERS`, `_MONITORS`, `_AUDITORS`) only count in development (`PSAGENT_DEV_OPRID`).
  In production only the roles count, and the service warns at startup if the lists are set.
  `PSAGENT_SECURITY_ADMINS` / `_ROLES` and `PSAGENT_APPROVAL_ADMINS` / `_ROLES` are no longer used.
- **Development mode can't be left on in production:** the service refuses to start when `PSAGENT_DEV_OPRID` and
  `PSAGENT_SHARED_SECRET` are both set (requests without a token would run as the development user).
- **Refused attempts are in the audit log** (`access.denied`, shown as *Refused*): a feature or tool used without the
  role, someone else's process, access, AI_ query or absence request, a token without any agent role, and a forged or
  malformed token. The Overview shows how many; the audit log filters them. Expired tokens are not counted: the widget
  simply fetches a new one.

Tested by `agent-service/tests/test_access.py` (each role through the API) and the troubleshooter, access checker and
approval tests.

## Open items found along the way

| Item | Why it matters |
|---|---|
| **PSAGENT_SVC has the PSADMIN permission list** | Far more than the read-only service account needs. Remove it; keep Process Monitor and Report Manager access. |
| **Change the PSAGENT_SVC password** | It is in `.env` and was used throughout testing. |
| **Query Viewer's run link doesn't check query trees** | Tested as PSAGENT_SVC (which has PSADMIN): it ran a query on a record outside its query trees. Retest with a normal user. |
| **SMTP server** | Needed for alert, budget and weekly emails. |
| **PSAGENT_AUDITOR role and the updated iScript** | Needed for the admin dashboard. |
| **Give the roles in PeopleSoft** | `PSAGENT_TEACHERS` in `.env` is now ignored in production: the people who record tasks need PSAGENT_ADMIN, monitor users PSAGENT_MONITOR. |
| **PSAGENT_TEST_USER** | Needed for unattended regression runs. |
| **Nothing is committed** | All of the work so far is local. |
