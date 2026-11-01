# PeopleSoft Agent service

A small Python service that lets the PeopleSoft widget (`../psagent.js`) **do
the work a person does on screen**, not just chat. You type *"create user
profile named velu"*. The agent asks for whatever is missing and shows a summary
card. You type the password on the card and click **Confirm**. The widget then
opens the real component in a visible workspace and fills it in, the way you
would by hand. Finally it reports PeopleSoft's answer back to the agent.

```
 PeopleSoft page (browser, your own session)          agent-service (this folder)
 ┌──────────────────────────────────┐  /api/chat     ┌──────────────────────────────┐
 │ psagent.js                        │ ─────────────▶ │ Claude + 4 tools               │
 │  chat panel                       │ ◀───────────── │   start_task / set_fields      │
 │  confirm card (password typed     │  reply + card  │   request_confirmation         │
 │    here, never sent to service)   │  /api/confirm  │   cancel_task                  │
 │  playbook runner → workspace      │ ─────────────▶ │ tasks/*.yaml: fields + steps   │
 │    iframe drives the component    │ ◀── steps ──── │ confirm gate enforced in code  │
 │                                   │  /api/result   │                                │
 └──────────────────────────────────┘ ─────────────▶ └──────────────────────────────┘
```

Design points:

* **The LLM never executes anything.** It can only record values and ask for a
  confirmation card. The steps come from the task YAML, are rendered server-side
  only after the user clicks Confirm, and use the values exactly as shown on the card.
* **PeopleSoft security still applies.** The steps run in the user's own browser
  session, so a user can only do what their roles already allow, and the audit
  trail shows that user.
* **Secrets stay in the browser.** Fields marked `sensitive` (the password) are
  not in the LLM's tool schema. They are typed on the card and substituted into
  the steps in the browser.
* **Any PeopleSoft message stops the run.** That includes an error, a warning or
  a yes/no prompt. The page stays open in the workspace, and the agent explains
  the message and offers to fix the values.

## Two ways to work: tasks and live mode

**Tasks** (`tasks/*.yaml`, recorded or hand-written) are the fast, repeatable
path: the agent collects the values, you confirm, the steps run in one go.

**Live mode** is for everything else, like Claude Code works in a terminal:
when no task fits, the agent calls `work_live` and then operates PeopleSoft one
page at a time. Each round the widget sends a snapshot of the workspace page
(fields with labels and values, buttons, tabs, messages, the pop-up, search
results); Claude picks the next move (`act`, `ask_user`, `type_secret`,
`request_commit`, `finish`); the widget does it and reports what happened.

```
 widget: snapshot of the page ──▶ /api/live/step ──▶ Claude chooses one move
        ▲                                               │
        └──────── does it, reports the result ◀─────────┘   until finish
```

Safety is enforced in `live.py`, not left to the model:

* every target must be on the current page; read-only fields are refused;
* Save, Submit, Post, Approve, Delete, Yes on a question and OK on the Process
  Scheduler Request can only be clicked after **you approve** a card that
  summarizes the change. The click is handed to the widget only against that
  approval;
* passwords are typed by you on a card in the browser (`type_secret`) and go
  straight into PeopleSoft;
* questions appear in the chat and your answer goes back into the run.

**Recorded tasks recover live.** When a recorded step no longer matches the
page (a field, button or tab is missing, or PeopleSoft does not respond), the run
does not stop: Claude gets the confirmed values, the steps already done, the
failed step and the rest of the recording, and finishes from that point in the
same workspace (at most 20 steps; Save still waits for your approval; the
password from the confirm card is reused in the browser). PeopleSoft's own
messages ("already exists", invalid value) still stop the run and come back to
you, because they need your decision. After a recovery, teachers are offered
**"Update the task?"** with the run as the new version. Say *"do it live"* to
skip the recorded task.

While it works, the recorder watches the workspace. When a live run succeeds,
teachers get **"Make this a task?"**: one click turns what Claude worked out into
a reviewed, reusable task, so the next time is one step.

## Documents

Attach a **PDF or image** with the paperclip (or drop it on the panel), say
what to do (*"create these users"*), and send. Claude reads the document and
fills the matching task with exactly what it says; values that are unclear,
handwritten or not an exact PeopleSoft value are flagged.

* One item: the usual confirmation card, flagged values highlighted, with a
  link to the document.
* Several items (e.g. a list of new joiners): **one review table**. Yellow cells
  need a look (hover for why), red cells are missing; edit any cell, untick rows
  to skip, type one password for all rows, click *Create N*. The service checks
  the edited values again, then the widget runs one row after another and the
  agent reports each result.

Files go to the agent service and Claude only. They are kept in memory for the
conversation, never written to disk, and sessions expire after 8 hours. Limits:
PDF 10 MB, images 5 MB (PNG, JPEG, GIF, WebP).

The pilot is an access request form → user profiles. The same pipeline takes any
task: a supplier invoice → voucher in Financials only needs the voucher task and
its lookups (vendor, PO).

## Troubleshooting failed processes

Ask *"why did process 557060 fail?"* or *"why did my last process fail?"* (also a
tile on the welcome screen). The agent investigates like a PeopleSoft admin and
shows each step in the activity feed:

1. the posted log files (stdout, .log, .trc), read in your browser session, the
   same links as View Log/Trace in Process Monitor;
2. the request, its message log (catalog text with parameters), parameters and
   earlier runs of the same process;
3. the failing code: Application Engine sections, steps and SQL, and the
   PeopleCode of a step with statement numbers matching PeopleSoft's
   "Statement:n";
4. read-only queries to prove a data problem;
5. a diagnosis card: category (data, code, setup, security, environment),
   confidence, where, root cause, evidence, fix steps, suggested code, how to
   rerun, and every query it ran. *Copy report* puts it on the clipboard.

It needs read-only database access, set in `.env`:

```
PSAGENT_DB_DSN=dbhost:1521/SERVICE      # e.g. 192.168.56.103:1521/HR92U052
PSAGENT_DB_USER=PSAGENT_RO
PSAGENT_DB_PASSWORD=...
PSAGENT_DB_SCHEMA=SYSADM                # owner of the PeopleSoft tables
PSAGENT_TROUBLESHOOT_EFFORT=medium      # Claude effort for investigations
```

Read-only is enforced three ways, so it holds even with a privileged account:
every statement must be one SELECT/WITH (no DML, DDL, PL/SQL, FOR UPDATE or
database links), every session runs `SET TRANSACTION READ ONLY`, and every query
is rolled back. Results are capped at 200 rows and 20 seconds; national IDs,
birth dates, pay rates, bank details and password hashes come back as `***`;
every query is logged with the user and its purpose. Use a SELECT-only account
in production:

```sql
CREATE USER PSAGENT_RO IDENTIFIED BY "...";
GRANT CREATE SESSION TO PSAGENT_RO;
BEGIN
  FOR t IN (SELECT object_name FROM dba_objects WHERE owner = 'SYSADM' AND object_type IN ('TABLE', 'VIEW')) LOOP
    EXECUTE IMMEDIATE 'GRANT SELECT ON SYSADM."' || t.object_name || '" TO PSAGENT_RO';
  END LOOP;
END;
/
```

The driver is `python-oracledb` in thin mode: nothing to install on the database
server. A typical investigation takes 20-60 seconds and costs a few tens of cents.

## Security access checks

Security administrators can ask *"why can't ABSMITH access Create Job Opening?"*
or *"why can't VELU find employee KU0001 in Job Data?"* (also the *Check
someone's access* tile). The agent walks the chain a security admin would, from
the same read-only database, and shows each step:

1. the page: menu label → `MENU.COMPONENT.MARKET` (portal registry);
2. the user profile (exists, locked), roles and permission lists;
3. the component's pages and actions (Add, Update/Display, ...) against what the
   user's permission lists authorize;
4. the menu link (content reference permission);
5. row security: whether the search record (the menu item's override if any)
   filters rows, and for a person, whether the user's security classes
   (SJT_OPR_CLS / SJT_CLASS_ALL) reach that person's SJT_PERSON rows;
6. every permission list and role that would grant the access, with how many
   new components each would also give the user and how many users each
   affects, so the smallest (least-privilege) fix can be chosen.

The card shows the chain (✓ / ✕), what is missing, the smallest fix with its
side effects, alternatives, links to the User Profile, Role and Permission List
pages and the page itself, and *Copy report*. Nothing is granted automatically.
For adding a role, adding a permission list to a role, authorizing a page in a
permission list or unlocking an account, *Apply* starts a live run that makes
exactly that change on screen and stops for your approval before Save.

Who may use it: anyone checks their own access (*"why can't I open Job Data?"*, without Claude's own
queries and without Apply); checking another user's access, and Apply, need **PSAGENT_ADMIN**.

## Absence approval reviews

*"Show my pending absence approvals"* (also the *Review absence approvals*
tile) lists the absence requests waiting for you (Absence Management approvals,
`EOAWPRCS_ID = AbsenceManagement`), with a **Review with AI** button on each.
The review reads, from the same read-only database:

1. the request (`GP_ABSSS_V_XREF`, `GP_ABS_EVENT`): absence type, dates,
   duration, and whether it changes an earlier absence;
2. the approval status (`EOAW_STEPINST` / `EOAW_USERINST`): who already
   approved, who it is waiting for, which step;
3. the employee's job on the first day (active, department, manager);
4. the balance: each entitlement of the absence type, its `<entitlement>_BAL`
   accumulator from the last calendar before the absence, minus approved and
   in-approval absences not yet deducted; and what the type does when the
   balance is short (`NEG_BAL_OPTN`);
5. the employee's other absences: overlaps (duplicates) and the last 12 months;
6. team coverage: who else under the same manager (else department) is off on
   the same days.

The card shows the **approval status**, the **recommendation** (approve, send
back, deny, or needs your review), a **confidence score** from 0 to 100 with its
reason, every check (✓ / ! / ✕), a suggested comment to send, and what could not
be checked. An "approve" despite a failed check is capped at 50. Nothing is
approved by the agent: *Open Approvals in PeopleSoft* takes the approver to the
Fluid Approvals page to decide. A review takes about 30-45 seconds.

Approvers can review the requests waiting for them. **PSAGENT_ADMIN** users can
also list and review everyone's (*"show everyone's pending absence requests"*).

## Runbooks from a checklist

A runbook is a checklist the agent runs in order: **checks** (read-only
queries), **tasks** and **processes** (tasks from the catalog), **approvals**
and **manual** steps. Admins (`PSAGENT_ADMIN`) attach the checklist they
already have (Excel `.xlsx`, `.csv` or PDF) and ask *"build a runbook from this
checklist"*:

1. Claude maps every row to a step: a task and its values, or a check with its
   query, or an approval or manual step; values that change each run become
   inputs (the checklist's example is the default).
2. The service checks the draft: tasks and fields exist, no passwords in it,
   inputs are defined, check queries are single SELECTs and are **test-run now,
   read-only** with the inputs' defaults. Problems go back to Claude to fix.
3. The review card shows each step, what each check returns today, what a task
   will ask when it runs, and Claude's questions. Changes: say them in the chat.
4. **Publish** saves `runbooks/<id>.yaml`. Nothing runs at this point.

Running one: anyone can say *"run Set up a test user"* or click its **Run:**
tile on the welcome screen. A start card asks for the inputs (with their
defaults), then the steps run in order and the chat shows each one:

| Step | What happens |
|---|---|
| check | The service runs its read-only query; a failure stops the run (unless the step is "warn only") |
| task | The task's confirm card: the runbook's values, anything still needed, and the password (typed there, never sent to the service). Then it runs on screen |
| process | Like a task, then the service follows the request in `PSPRCSRQST` (by its instance, or the user's first request after the step started) until it ends: Success goes on, Error / Not Successful stops the run and offers *Why did process N fail?* |
| approval | Waits for Approve or Reject (first version: whoever runs it approves; the approver is logged) |
| manual | Waits until the person ticks it off |

The run stops at the first problem or at **Stop the run**. Claude is not called
between steps. Every run is logged to `runbooks/runs/<id>-<run>.json` (who,
inputs, each step's result).

`runbooks/templates/` has the checklist template (`runbook_checklist_template.xlsx`),
a 5-step test checklist that uses tasks on the demo system
(`test_user_setup_checklist.xlsx`) and a North American Payroll pay run and
month-end checklist. `runbooks/design/` has a hand-written sketch of the
payroll runbook. Published runbooks go to `PSAGENT_RUNBOOKS_DIR` (default
`runbooks/`).

## The watcher: watchdogs that run on their own

Users with the PeopleSoft role `PSAGENT_MONITOR` (or `PSAGENT_ADMIN`) set up
**watchdogs**, saved in **watch plans** (`monitors/<plan>.yaml`, one file per plan).

**From the template** (the usual way): **＋ Features › Watch jobs** in the chat's
bottom bar shows the steps, with **Download the template** and **Upload the filled
template** buttons. The template (`runbooks/templates/watch_plan_template.xlsx`,
also served by the service) has one row per watchdog, with drop-downs:

| Watchdog | Process | Watch for | Schedule | Time | Stuck after | Run by | Rule / Query (data checks) | Email | Status |
|---|---|---|---|---|---|---|---|---|---|

It comes with examples: AEMINITEST and XRFWIN failures, XRFWIN stuck, and
PORTAL_CSS must succeed by 07:00. A second sheet explains every column. Claude
reads the uploaded sheet (`draft_watch_plan`), the service checks every row
against PeopleSoft (the process exists, the query is allowed) and shows an
**overview**: each watchdog with what it would have caught. **Confirm and
schedule** saves the plan and the watchdogs start.

**In the chat**: *"watch AEMINITEST overnight and email ops@example.com"* drafts
one watchdog (`draft_monitor`), saved as a plan of its own.

| Kind (Watch for) | What it watches | Every alert has |
|---|---|---|
| failures (Failure) | Requests ending in Error or No Success; optionally some processes (`%` = any characters) or some users' requests | The troubleshooter's analysis: message log, the posted log files (as the service user), code and data |
| stuck (Stuck) | Requests still Queued, Pending or Initiated N minutes after their run time | The process servers' status and the waiting requests |
| deadline (Must succeed by) | At the time of day: the process must have ended in Success in the 24 hours before | What happened instead: never ran, failed (investigated), still waiting (servers), running late; the latest runs |
| check (Data check) | A read-only SELECT returning the wrong rows, every N minutes or at set times | The rows; cleared when the check passes again |

**Managing them**: the bottom bar shows **◉ N watchdogs** (red when there are
alerts). It opens the watchdog list, plan by plan:
- per watchdog: **Pause** / **Resume**, **Run now** (a test, whatever the schedule), **Delete**;
- per plan: **Download as Excel**, **Pause all**, **Delete plan**;
- **Stop all watching**: nothing runs until it is started again, even after a restart.

Deleting and stopping ask first. To change a plan, download it as Excel, edit
it and upload it again: the overview says what is added, removed and changed
before you confirm. Every change is kept in the history with who made it.

The watcher runs every minute inside the service, with nobody signed in. Each
monitor that is due costs one read-only query and no Claude; only a new failure
is investigated (`PSAGENT_WATCH_MAX_INVESTIGATIONS` per day). The same process,
run control and user failing again within 12 hours is counted on the open
alert, not investigated again. After the service was down, failures older than
24 hours are not alerted.

Alerts are emailed (`PSAGENT_SMTP_*`) to the monitor's recipients: what
happened, the **analysis**, the **action needed**, and links to open the alert
in the agent and to Process Monitor. A daily summary goes to
`PSAGENT_ALERT_TO`. In the panel, monitor users see *"N alerts from the watcher"*
on the welcome screen, can ask *"what happened overnight?"*, *"what is being
watched?"* or *"pause the stuck monitor"*. Nothing is rerun or changed.

Log files overnight: the widget normally reads them in the user's session. The
watcher signs in as `PSAGENT_SVC_USER` (a PeopleSoft user with only Process
Monitor and Report Manager access) the way a browser does and downloads the same
files *View Log/Trace* opens. Without it, the message log is used.

Kept in `monitors/`: the plans, `incidents/<id>.json` (each alert) and
`state.json` (when each watchdog last ran, what it has seen, stopped or not, the history).

## Regression tests: replay the tasks after a patch

On a **test environment** (`PSAGENT_TEST_ENV=on`; the cases really save data),
admins open **＋ Features › Regression tests**:

- **Smoke test**: every task once with its defaults, no setup. A required field
  without a default gets `RT{{n}}` when it is the key of a record the task adds
  (a new user ID), or "Regression test {{n}}" for free text. Other tasks are
  listed as needing a test case.
- **Test plan template**: generated from your tasks. It has a *Suite* sheet (name,
  run unattended at, run after a patch, who gets the report), then one sheet per
  task with that task's fields as columns. One row is one test case: the values
  and the expected result (*Saved*, *Process submitted*, or *Error* with part of
  the expected message, for negative tests). `{{n}}` becomes a new number on every
  run. Upload it; Claude reads it (`draft_test_suite`), every case is checked
  against its task, and **Save test suite** writes `regression/<suite>.yaml`. A
  saved suite downloads as the same template, to edit and upload again.
- **Run here**: the password fields are typed once, then each case runs in the
  workspace, one after another:

| Result | When |
|---|---|
| Passed | The expected result: Saved / Process submitted, or the expected error message |
| Passed with changes | A recorded step no longer matched the page; the case was finished live (Save approved automatically, test environments only) and reached the expected result. The report lists what changed |
| Failed | Anything else: the step and PeopleSoft's message |

- **Report**: compared with the suite's last run (*failing since the last run*,
  *fixed*), kept in `regression/runs/` and downloadable as Excel.
- **Run unattended**: a headless browser on the agent service signs in as
  `PSAGENT_TEST_USER` and runs the suite with the **same widget code**
  (`PSAgent.runSuite`). If Branding has an older `psagent.js`, the service's copy
  is used. A suite also runs on its own at its time of day (`run_at`), and after
  a patch when it is marked *after_patch*: the runner notices a change in
  `PS_MAINTENANCE_LOG` and `PSRELEASE`. The report is emailed (`PSAGENT_SMTP_*`)
  to the suite's recipients, or to `PSAGENT_ALERT_TO`. Needs `pip install
  playwright` and `python -m playwright install chromium`.

## Excel to CI: load spreadsheet rows through Component Interfaces

**＋ Features › Load with a CI** does what ExcelToCI does, without the macro workbook:

1. **Pick a CI.** The list shows the CIs the user's permission lists allow
   (`PSBCDEFN` and `PSAUTHBUSCOMP` through `PS_USERCLASS_VW`, as SOAPTOCI decides it).
2. **Choose the columns, download the template.** The browser asks PeopleSoft for
   the CI's shape (`GetCIShape__CompIntfc__<CI>` posted to `WEBLIB_SOAPTOCI`, like
   ExcelToCI's *New Template*). The service adds metadata from `PSBCITEM`,
   `PSRECFIELD` (prompt table, translate, Yes/No and required edits) and `PSXLATITEM`,
   keeps the shape (`ci_loads/shapes/`), and builds the `.xlsx`:
   - the CI's sheet: *Action* (Create / Update), the keys, the chosen properties;
   - one sheet per collection, linked by the parent keys, with *Row action*
     (Change / Add / Delete) for updates;
   - a Help sheet.
3. **Upload it** (on the card, or drop it in the chat). The service checks every row:
   - required fields and keys, lengths, numbers, dates and times;
   - translate values (code or label);
   - prompt table values, against the prompt table's key of the same name, else its
     last key that isn't EFFDT/EFFSEQ; dynamic prompts (`%EDITTABLE`) aren't checked;
   - the key existing for Update and not for Create, against the CI's level 0 table,
     else its search record unless that is a dynamic view;
   - duplicates, and collection rows without a parent.
4. **Load.** For each ready row the service builds the SOAP request
   (`Create__CompIntfc__<CI>` or `Updatedata__…`, collections as repeated child
   elements, `CINodeAction="INSERT|DELETE"` for added or deleted collection rows,
   one row per request). **The browser posts it to `WEBLIB_SOAPTOCI` in the user's
   own session** and returns the answer: `notification 1` means saved, and the CI's
   messages are kept. Pause, Stop and Resume work; loads are kept in `ci_loads/loads/`.
5. **Results:** a results Excel, the rows to fix (the template with the rows that
   weren't saved), and *Explain the errors* (one chat turn).

Passwords are never in the file: a CI password property is typed on the load card and
filled in by the browser. Every check and row is in the audit log. Users need what
ExcelToCI users need: `WEBLIB_SOAPTOCI` in a permission list's Web Libraries (for
example via `PTPT1200` or `HCSPWEBLIB`), and the CI's methods on the Component
Interfaces tab. It needs the read-only database (`PSAGENT_DB_*`) for the checks.

## Ask the data: answers from curated PS Queries

**＋ Features › Ask the data**, or just a question in the chat (*"how many new hires this year by department?"*).
Answers come only from PS Queries named `AI_...` (`PSAGENT_QUERY_PREFIX`), written in Query Manager. The agent never
writes SQL for them.

1. **Which queries:** public AI_ queries and the user's own private ones, whose every record is in the user's query
   trees: `PS_USERCLASS_VW` → `PS_SCRTY_ACC_GRP` (accessible access groups) → the current `PSTREEDEFN` /
   `PSTREENODE` → records; `PSQRYRECORD` must have no record outside them. Columns from `PSQRYFIELD`, prompts from
   `PSQRYBIND` (translate values, prompt tables). Kept 10 minutes per user. The run link does not check query trees
   (verified on PT 8.61), so the service builds links only for these.
2. **Claude picks one** (`find_queries`) and its prompt values (`run_query`). The service checks them: dates sent as
   `YYYY-MM-DD` (PeopleSoft reads other formats as no rows, silently), numbers, translate values, prompt-table values
   (read-only), Uppercase fields; every prompt goes in the link, an optional one empty. Prompts need names (`BIND1`:
   Query Manager gives them); a query with `:1` prompts is listed but not run.
3. **The browser runs it** in the user's session: `/psc/<site>/<portal>/<node>/q/?ICAction=ICQryNameExcelURL=PUBLIC.<name>&BIND1=...`
   (`PRIVATE.` for a private query). PeopleSoft applies the user's row security. The file goes to
   `POST /api/query/result`; the service reads it (row 1: title and count, row 2: headings; dates from Excel serial
   numbers), keeps the rows in memory for the conversation, and sends the card the first 200.
4. **Claude gets totals only** (`queries.summary`): the row count; per column, counts of repeating values, sums,
   date ranges. Columns that tell rows apart (names, IDs) give only their number of distinct values; sensitive columns
   (`db.masked`: pay, national IDs, birth dates, bank details) nothing. `summarize_result` (`queries.aggregate`)
   breaks it down under the same rules, drawn as a chart.

The audit log records each run (`query.run`: query, prompt values, row count), never the results. Needs the read-only
database (`PSAGENT_DB_*`); `PSAGENT_QUERIES=off` turns it off.

## Admin dashboard and audit log

Everything the agent does is recorded in `audit/psagent.db` (SQLite, next to the
service; `PSAGENT_AUDIT_DIR`):

| Recorded | With |
|---|---|
| Every Claude call | The feature it was for, tokens (input, output, cache write, cache read), estimated cost, seconds |
| Every database query | Who and why, the SQL, rows, milliseconds, any error |
| Every task run | The confirmed values (never passwords: they never reach the service), the result and PeopleSoft's message |
| Feature events | Investigations, access checks, absence reviews, security fixes, recordings, runbooks published and run, watch plans and watchdog changes, alerts, test suites and regression runs, sign-ins |

**What people type in the chat and Claude's answers are not kept**, only that a
message was sent (or a document's name). Events older than `PSAGENT_AUDIT_DAYS`
(default 90) are removed every day. If the file cannot be written, the agent
carries on and logs a warning once.

Users with the PeopleSoft role **PSAGENT_AUDITOR** open **＋ Features › Audit**
(or the *Audit dashboard* card on the welcome screen). It opens on the left,
beside the chat:

- **Overview**: people, conversations, task runs, Claude cost against the period
  before, failures, watcher alerts, regression runs; the month's spend against
  `PSAGENT_BUDGET`; cost per day; cost by feature; top users.
- **Usage & cost**: by user or by feature, with Excel download. Click a row for
  that user's or feature's audit log.
- **Audit log**: filters for user, feature, kind (actions, database queries,
  Claude calls), outcome, dates and search; click a row for the SQL or the values;
  Excel download (up to 1,000 rows).
- **Failures** and **Watcher & tests**.

In the chat, auditors can ask about it (*"who spent the most this week?"*): the
`audit_report` tool answers from the same data. Others get no audit data.

Cost is estimated from the tokens at list prices (Opus 5.5: $4 input, $20 output,
$5 cache write, $0.20 cache read per million tokens); set `PSAGENT_PRICES` if
yours differ. With SMTP set up, `PSAGENT_BUDGET` sends an email at 80% and 100%
of the monthly budget, and a usage summary goes out every week
(`PSAGENT_AUDIT_WEEKLY`, default Monday 08:00) to `PSAGENT_AUDIT_TO` (or
`PSAGENT_ALERT_TO`).

## The chat panel

The agent is called **Pia** (PeopleSoft Intelligent Assistant); the name is
`NAME` at the top of `psagent.js` and the first line of `SYSTEM_PROMPT` in
`psagent/agent.py`. The widget is a panel docked on the right, with the
workspace beside it:

* the agent's state is always visible (ready, thinking, working, waiting for
  you), on its avatar and in the header;
* an activity feed shows each step as it happens, with a tick or the error;
* the welcome screen greets you by time of day and shows:
  * an alert when processes of yours failed in the last 24 hours (with the
    troubleshooting database configured), one button per process to find out why;
  * cards with an icon per kind (task, troubleshoot, access check, explain this
    page, help with this error); the ones for the page you are on come first,
    marked *This page*;
  * *What's new*, once, until dismissed or the first message (`NEWS` in
    `psagent.js`; change its `id` to announce something new);
* while the welcome screen is up, the message box cycles through examples of
  what you can ask;
* the clock button (or the Up arrow in an empty box) brings back recent
  questions; the arrows button makes the panel wider; **Ctrl+/** opens and
  closes it.

## Run it

```bash
cd agent-service
pip install -r requirements.txt
cp .env.example .env                      # then fill in the API key, secret and origin
uvicorn --factory psagent.server:app_from_env --host 0.0.0.0 --port 8080 --workers 1
```

### On a Linux server (e.g. the PeopleSoft VirtualBox image)

Use the system `python3` (3.10 or newer) in its own virtual environment, not
the `python` that comes with PeopleTools (`$PS_HOME/python`): it doesn't have
the service's packages. Start uvicorn with `python -m uvicorn`; `python uvicorn`
looks for a script file named `uvicorn` and fails with *can't open file*.

```bash
cd ~/pscopilotagentjs/agent-service       # wherever the folder was copied
python3 --version                         # 3.10+; else e.g. sudo dnf install python3.11
python3 -m venv .venv                     # once
source .venv/bin/activate
pip install -r requirements.txt           # once
cp .env.example .env                      # once, then fill it in
python -m uvicorn --factory psagent.server:app_from_env --host 0.0.0.0 --port 8080 --workers 1
```

Next time, only `cd`, `source .venv/bin/activate` and the last line are needed.
If the browser can't reach port 8080, open it:
`sudo firewall-cmd --add-port=8080/tcp --permanent && sudo firewall-cmd --reload`.
`PSAGENT_ALLOWED_ORIGINS` must match the PeopleSoft URL in the browser
(e.g. `http://vbox:8000`).

Settings are read from `agent-service/.env` (see `.env.example`); real
environment variables take precedence.

| Variable | Default | |
|---|---|---|
| `PSAGENT_MODEL` | `claude-opus-5-5` | Claude model |
| `PSAGENT_EFFORT` | `low` | `low`…`max`. Low keeps chat replies fast. Raise it if extraction misses things. |
| `PSAGENT_TASKS_DIR` | `./tasks` | Where task YAML files live. Recorded tasks are saved here too, so it must be writable. |
| `PSAGENT_TEACHERS` / `_MONITORS` / `_AUDITORS` | unset | Development only (`PSAGENT_DEV_OPRID`): OPRIDs given ADMIN / MONITOR / AUDITOR rights (`*` = everyone). Ignored in production, with a warning: use the roles |
| `PSAGENT_DEV_OPRID` | unset | Local testing only: requests without a valid token run as this OPRID (roles: `PSAGENT_DEV_ROLES`) |
| `PSAGENT_MONITORS_DIR` | `./monitors` | Monitors, their alerts and the watcher's state. `PSAGENT_WATCH=off` turns the watcher off. |
| `PSAGENT_PS_URL` | unset | PeopleSoft address users sign in on (`…/psp/ps/EMPLOYEE/HRMS/`): links in alert emails, sign-in of the service user |
| `PSAGENT_SVC_USER` / `_PASSWORD` | unset | Service user that downloads posted log files for overnight investigations |
| `PSAGENT_SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_FROM` | unset | Alert emails (587 = STARTTLS, 465 = SSL) |
| `PSAGENT_TEST_ENV` | unset | `on` turns regression tests on (test environments only) |
| `PSAGENT_TEST_USER` / `_PASSWORD` / `PSAGENT_TEST_SECRETS` | unset | Unattended regression runs: the user they sign in as, and values for password fields (`password=...`) |
| `PSAGENT_ALERT_TO` | unset | Default recipients and the daily summary (`PSAGENT_WATCH_SUMMARY`, default `07:00`) |

Sessions are held in memory, so run **one worker**. Move `SessionStore` to Redis
before scaling out.

Requests use the server-side refusal fallback (`fallbacks: "default"`). If
Claude's safety classifier declines a request, the API retries it on
Anthropic's recommended fallback model.

## PeopleSoft setup

Three pieces: the agent service runs on a server, an iScript tells the browser
where it is and who the user is, and the widget script is loaded on every page.
The widget needs no App Designer changes to delivered objects.

1. **Agent service.** Run it on a server PeopleSoft users can reach over HTTPS.
   Best is to reverse-proxy it under the PeopleSoft host, e.g.
   `https://ps.example.com/psagent/` → `http://agent-host:8080/`. Then there's
   no CORS and no extra certificate. Otherwise give it its own HTTPS name and
   list the PeopleSoft origin in `PSAGENT_ALLOWED_ORIGINS`.
2. **iScript** (App Designer). Create a derived record `WEBLIB_PSAGENT` with
   field `ISCRIPT1` and paste `../psagent_iscript` into its FieldFormula. Set
   `&AGENT_URL` (e.g. `/psagent`) and `&AGENT_SECRET`, the same value as
   `PSAGENT_SHARED_SECRET`. It returns a day-scoped token with the user's agent
   roles, `OPRID.YYYYMMDD.ROLES.HashSHA256(secret|OPRID|YYYYMMDD|ROLES)` (e.g.
   ROLES = `ADMIN+USER`), and the secret never reaches the browser.
3. **Security.** Open PeopleTools > Security > Permission Lists, go to the Web
   Libraries tab, add `WEBLIB_PSAGENT` and give `IScript_PSAgentConfig` Full
   Access, in a permission list every agent user has. Then create three roles
   and give users the ones they need. USER < MONITOR < ADMIN (each includes the one
   before); AUDITOR is kept apart (give both when a person needs both):

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

   A user with none of them gets no token and no widget. The service checks the roles
   on every request (`psagent/access.py`); PeopleSoft's own security still applies
   inside each feature.
4. **Widget** (no App Designer). Open PeopleTools > Portal > Branding > Branding
   Objects, JavaScript tab, and upload `../psagent.js` as `PSAGENT_JS`. Then
   open PeopleTools > Portal > Branding > Branding System Options and add
   `PSAGENT_JS` under *Additional JavaScript Objects*. Sign out and back in. It
   loads on Fluid and Classic pages. It only shows in the top window, never
   inside the content frame or its own workspace.
   *Alternative:* paste it at the bottom of `PTNUI_MENU_JS`, as the Copilot
   widget does. That only covers Fluid pages and modifies a delivered object.

The widget derives the iScript URL from the page (`/psc/<site>/<portal>/<node>/s/…`).
The agent works in a frame on the same origin
(`/psc/<site>/<portal>/<node>/c/MENU.COMPONENT.GBL`). If your web server sends
`X-Frame-Options: DENY` for `/psc/`, allow `SAMEORIGIN`.

## Teaching a new task by recording it (voucher, requisition, …)

Do the transaction once by hand and the agent can do it from then on:

1. Click **Record** in the chat header (only shown to `PSAGENT_ADMIN`), or
   type *"let me teach you how to create a requisition"*.
2. Name the task. The page is optional: leave it empty and click **Start recording**.
3. A red bar appears at the top. **Go to the page the way you normally do** (NavBar,
   menu, search) and do the task once by hand, to the end: **Save**, or **Run** and
   **OK** for a process. The bar follows you across page loads and counts the
   steps. When it shows *Saved* or *process … submitted*, click **Finish recording**.
   * Every value you type, pick or tick is recorded. That includes values a
     lookup fills in.
   * Password fields are recorded as "a password goes here", never the value.
   * The NavBar, header and message boxes are not part of the task.
   * To make a list (roles, requisition lines), fill **at least two rows**.
   * A change you undo (tick, then untick) leaves no step.
   * A task covers one page (component). If you move to another one with values
     already entered, the bar says so; go back or finish. What you do after Saved
     or the process instance (e.g. opening Process Monitor) is left out.
4. On the review card, check how the record is opened each time (add a new one;
   add it, or open it if it already exists; open an existing one) and how each
   recorded value is treated. Claude suggests the values' modes; change any of
   them, then click **Save task**:
   * **Ask every time**: for IDs, names, amounts and dates.
   * **Ask, can be blank**: for values that are only sometimes needed.
   * **Ask, default = recorded**: for values that are usually the same (business
     unit, symbolic ID).
   * **Always the recorded value**: for values that are part of the procedure
     (e.g. ID Type = Employee).
5. The task is saved as `tasks/<task_id>.yaml` and is live immediately, including
   in open conversations. Ask for it in your own words, like any other task.

### How a recording becomes a task

The recorder doesn't know any particular page. Every classic component works the
same way, and each recorded step notes where it happened:

| Where | What it becomes |
|---|---|
| **Search page**: key values, Add / Search, a result row | One `open` step: the keys and a mode. *add* when you pressed Add, *find* when you searched or picked a row, *add_or_open* when you added and then opened the existing row, and for every process run (a run control is reused) |
| **Page**: values, tabs, grid rows, buttons | `set`, `tab`, `grid` and `click` steps |
| **Pop-up** over the page (e.g. Process Scheduler Request) | The same steps, marked `popup: true` |
| **What PeopleSoft showed**: Saved, or the process request accepted | `expect_saved` / `expect_process` at the end. The **last** result counts (Run saves the run control before the pop-up opens), and a process run outranks Saved. If you finish on an error message instead, the review card warns you |
| **Grids** | A grid you added rows to (with **+**) becomes one list value. In a fixed grid, such as the pop-up's Process List, each row is found by the text it shows; text that is one of your values (the program name) follows that value, so another program ticks its own row |

When the agent runs an `open` step it does what a person does on the search page:
it types the keys, presses Add or Search, opens the only match or the matching
result row, and, in *add_or_open* mode, opens the existing record when PeopleSoft
says it already exists.

When you leave an optional value blank, the agent skips the steps that only
existed for it. If a whole tab section only holds optional values, it is skipped
entirely: no EMPLID means the ID tab isn't touched. If Claude is unavailable,
simple rules suggest the modes instead. Saving the same task ID again asks you
to confirm the replacement. Replay a new task once on test data before relying
on it.

Recorded tasks are plain YAML in the same format as hand-written ones, so you
can open one and adjust it. `PSAgent.inspect()` lists every field ID and label
on the workspace page. `PSAgent.teach('MENU.COMPONENT.GBL', {Action: 'A'})`
prints a raw recording in the console.

## Task file format

A task is one YAML file in `tasks/`: the **fields** the agent must collect, and
the **playbook** of steps a human performs.

Step types:

| `do` | Fields | What it does |
|---|---|---|
| `open` | `mode`, `keys`, `pick`?, `query`? | Opens the component through its search page: types `keys` (`field` + `value`), presses Add (`mode: add`, `add_or_open`) or Search (`find`), then opens the result whose key is `pick` (default: the first key). `add_or_open` opens the existing record when PeopleSoft says it already exists |
| `navigate` | `query` | Opens the task's `component` directly (`{Action: A}` = Add mode); for pages without a search page |
| `set` | `field`, `value` | Types into a text box, picks a dropdown option (value or visible text) or ticks a checkbox, then waits for PeopleSoft |
| `click` | `button` | Clicks a button/link (`#ICSave`, the Run button, …) and waits for PeopleSoft |
| `tab` | `label` | Opens a page tab by its visible name |
| `grid` | `field`, `values` | Fills one grid row per value, pressing the row's **+** button to add rows |
| `wait` | `field` | Waits until a field appears |
| `expect_saved` | | Succeeds only if PeopleSoft shows **Saved** and no message |
| `expect_process` | | After Run, then OK: succeeds when the page shows a **Process Instance** number or, on pages that show none (e.g. AE requests), when the Process Scheduler Request closed without a message |

Fields are found by `id` first, then by `label` (the `<label for>` text), then by
grid `column` header. `popup: true` on a `set`, `click` or `wait` step means the
field is on a pop-up page PeopleSoft opens over the page (the Process Scheduler
Request). Any step can have `when: <field>` or `when: [a, b]` (skipped unless one
of them has a value) and `say:` (progress text). Use `{{secret:name}}` for `sensitive` fields.
`load_tasks` checks every placeholder and `when` against the field list, so a
typo fails at startup.

For a **voucher from an invoice**: record the voucher entry once on
`VCHR_EXPRESS`. The missing piece is an invoice upload in the widget so Claude
can read the vendor, invoice number, date and lines from the document instead
of asking for them. That is the natural next step.

## Tests

```bash
cd agent-service && python3 -m unittest discover -s tests -t tests       # 84 unit tests, scripted LLM
cd .. && node tests/runner/run_browser_tests.mjs                          # 12 browser tests (Playwright)
```

The browser tests run the real widget and the real service (with a scripted
LLM) against two small imitations of PeopleSoft pages in `tests/runner/`, not
real PeopleSoft HTML: `mock_usermaint.html` (User Profiles) and
`mock_runctl.html` (Sync Portal Security, a run control page with the Process
Scheduler Request pop-up). They prove the runner mechanics: AJAX waits, a full
reload on Add, label/column fallbacks, grid rows, message-box detection, the
search page (already exists, result rows), pop-ups, and that the password never
reaches the service. Two tests record a task by hand (a user creation, a
process run), save it, and have the agent run it again. The unit tests for the
recorder use event sequences recorded on a real PeopleTools 8.5x system. They do
not prove that the field IDs match your release. Run `PSAgent.inspect()` once on
the real page for that.
