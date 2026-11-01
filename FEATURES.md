# PeopleSoft Agent: features

A chat agent inside PeopleSoft that works on screen like a person: it does
tasks, learns new ones by watching, reads documents, works out unfamiliar pages
on its own, and troubleshoots failed processes and security access. Overnight
it keeps watch on its own and emails the analysis of what went wrong.

- **Widget:** `psagent.js`, loaded through Branding.
- **Service:** the Python agent service in `agent-service/`, using Claude `claude-opus-5-5`.
- **Tested on:** PeopleTools 8.61, HCM 9.2 (HR92U052, Oracle).

Setup and details are in [agent-service/README.md](agent-service/README.md).

**Contents**

1. [Features built](#features-built)
2. [Safety principles](#safety-principles)
3. [What it costs](#what-it-costs)
4. [Future features](#future-features)

---

## Features built

### 1. Chat widget

A wide, modern panel docked on the right of every PeopleSoft page.

- **Avatar:** shows what the agent is doing (ready, thinking, working, waiting for you, stopped).
- **Welcome screen:** tiles for what the agent can do, based on the user's tasks and permissions, plus recent questions.
- **Context bar ("You are on …"):** the agent knows the current page and any error shown on it. It can *Explain this page* or *Help with this error*.
- **Activity feed:** every step the agent takes, with timings.
- **Shortcuts and layout:** Ctrl+/ opens and closes the panel; wide mode is available; chat history is kept per session.
- **Sign-on:** an IScript issues a short-lived token, so no separate login is needed.

### 2. Tasks: the agent does the work

Example: *"create a user profile for JSMITH, roles EMPLOYEE and Manager"*.

1. The agent collects the values. It asks only for what's missing and never invents IDs, roles or permission lists.
2. A **confirmation card** summarizes everything. Nothing runs until the user clicks Confirm.
3. **Passwords** are typed on the card and never reach the service or Claude.
4. The **runner** then performs the steps on screen:
   - opening a record, by Add or by search;
   - setting fields, clicking, switching tabs and filling grids;
   - the Process Scheduler pop-up;
   - checking for *Saved* or a process instance.
5. It **stops on PeopleSoft errors** and explains them in plain words.

Tasks on the system today: Create User Profile, Sync Portal Security, and Run an Application Engine program.

### 3. Recorder: teach a new task by doing it once

Users with PSAGENT_ADMIN click **Record**, do the task once by hand, and the agent turns the recording into a reusable task.

- **Works on any page:** the recorder doesn't depend on special cases. It understands:
  - search pages (Add / Find / already-exists);
  - transaction pages;
  - pop-ups such as the Process Scheduler Request;
  - the outcome (Saved, a process submitted, or a pop-up closing).
- **Grids:** fixed rows are matched by their text. Only grids that allow new rows are treated as lists.
- **Ignores undone changes:** edits the user reverted, and clicks that did nothing, are left out.
- **Review card:** Claude suggests field names, which fields to ask for, and which are fixed or optional. The teacher reviews this, then the task is saved and available right away.

### 4. Live mode: works out pages it was never taught

When no task fits, or the user says *"do it live"*, Claude operates PeopleSoft one page at a time:

1. It reads the page: fields, buttons, tabs, messages and pop-ups.
2. It decides the next move and acts.
3. It repeats until the goal is done.

Along the way:

- **Questions:** it asks the user when a value is missing.
- **Passwords:** the user types them on a card.
- **Approval:** anything that saves, submits, deletes or posts needs an explicit approval click. The service enforces this, not the model.

### 5. Live recovery: when a recorded task meets a changed page

If a page changed since recording (a moved field, a new tab), the recorded run doesn't fail:

1. It hands over to live mode at the failed step.
2. Live mode finishes the task, and the save is still approved by the user.
3. The agent offers to **update the task** from what it learned.

### 6. Documents: attach a form, the agent fills PeopleSoft

Attach a PDF or an image, such as an access request form listing several people.

- **Reading:** Claude reads the document and maps it to the right task.
- **Unclear values:** handwritten, ambiguous, or described in words (*"needs manager access"*), these are **flagged** for checking.
- **Several people or records:** they appear in **one review table**. The user can fix, fill in or untick rows, and the service checks every edit again.
- **Running:** the rows run one by one, with a result per row.
- **Retention:** documents are read and discarded, never stored.

### 7. Process troubleshooter: *"why did process 557060 fail?"*

Give a process instance, or ask *"why did my last process fail?"*. The agent investigates like a PeopleSoft admin, with read-only access:

- **Log files:** stdout, .log and .trc, read in the user's browser session, the same files as View Log/Trace. For big files (traces of hundreds of MB) it reads only the end.
- **Request details:** the request, its parameters (with passwords hidden), the **message log** with the message text filled in, and earlier runs.
- **Code:**
  - Application Engine sections, steps and SQL;
  - **PeopleCode with statement numbers** matching PeopleSoft's "Statement:n";
  - **searching all PeopleCode** for functions defined elsewhere, which covers delivered (Oracle) code of any size.
- **Runs at the same time:** runs of the same process that overlapped, the Max Concurrent and restart settings, and mutual exclusion.
- **Data problems:** proved with read-only queries.
- **Diagnosis card:**
  - category (data, code, setup, security, environment) and confidence;
  - exact place, root cause and evidence;
  - fix steps and corrected code;
  - how to rerun;
  - every query it ran, plus a Copy report button.

Verified on real failures:

- **557060, AEMINITEST:** division by zero, located to statement 3 / line 5 in 21 seconds.
- **557046, PORTAL_CSS:** three syncs ran at the same time and Max Concurrent was 0. Its delivered code was proven fine because the same run control succeeded when it ran alone.

### 8. Security access troubleshooter: *"why can't ABSMITH access Create Job Opening?"*

Anyone can check their own access (*"why can't I open Job Data?"*). Checking another user's access, and **Apply**, are for PSAGENT_ADMIN users.

The agent walks the security chain from the PeopleTools tables:

1. the page → its component (`MENU.COMPONENT.MARKET`);
2. the user profile (exists, locked), roles and permission lists;
3. the component's pages and actions (Add, Update/Display, …) against what the permission lists authorize;
4. the menu link permission;
5. row security on the search record (including the menu item's override);
6. HCM row security for a person (SJT tables).

**Least-privilege fix:** every role and permission list that would grant the access is listed with:

- how many *other* pages it would also give the user;
- how many users it affects.

**The card shows** a ✓/✕ checklist, what's missing, the smallest fix and its side effects, alternatives, and links to the User Profile, Role and Permission List pages.

**Apply:** a live run makes exactly that change and **stops for approval before Save**. Nothing is ever granted automatically.

Verified on real users:

- **ABSMITH:** lacks HCCPRS1040. The smallest role that grants it is Hiring Manager, which adds 62 components against 81–178 for the others.
- **VELU:** has no row-security entries (empty `SJT_OPR_CLS`), so VELU finds no one in Job Data. The fix is a refresh, not a new role.

### 9. Absence approval reviewer: *"show my pending absence approvals"*

The approver sees the absence requests waiting for them and clicks **Review with AI** on one. PSAGENT_ADMIN users can also see everyone's.

The agent reviews the request from the database, read-only:

- **Request:** absence type, dates, duration, and whether it changes an earlier absence.
- **Balance:** the entitlement balance from the last calendar, minus absences not yet deducted, and what happens when it is short.
- **Overlaps:** another absence on the same days.
- **Team coverage:** who else under the same manager is off then.
- **Timing, employment, history:** dates in the past, an inactive employee, recent absences (information, never an accusation).

**The card shows:**

- the **approval status** from PeopleSoft (pending, step, who it waits for, who already approved);
- the **recommendation**: approve ("I reviewed this request, please approve it"), send back, deny, or needs your review;
- a **confidence score** (0–100, high / medium / low) and why. An approval despite a failed check is capped at 50;
- each check (✓ / ! / ✕), a suggested comment to send, and what could not be checked.

**The approver decides:** the agent never approves. *Open Approvals in PeopleSoft* opens the Fluid Approvals page.

Verified on a real request: **1589**, Edmund Donahue's vacation on 31 Jan 2018. Balance, overlaps, team and employment were all fine, but the date was 8 years ago and the request had been pending since 2017, so the agent recommended **needs your review** at **65/100** and drafted a note asking the employee to confirm, in 42 seconds.

### 10. Runbooks from a checklist

Teachers attach the checklist they already have (Excel, CSV or PDF) and ask *"build a runbook from this checklist"*.

- **Drafting:** Claude turns every row into a step: a check (read-only query), a task or process from the catalog with its values, an approval or a manual step. Values that change each run become inputs.
- **Checked by the service:** tasks and fields must exist, no passwords are stored, and every check query is **test-run read-only** right away, so the card shows what it returns today.
- **Review card:** the steps by type, what each task will ask when it runs, and Claude's questions. Changes are made by saying them in the chat; **Publish** saves `runbooks/<id>.yaml`.
- **Templates:** a blank checklist template, a 5-step test checklist, and a North American Payroll pay run and month-end checklist built from the demo system.

Verified with real Claude on the 5-step test checklist, in 30 seconds and on the first try: both PeopleSoft steps were mapped to the existing tasks (Create User Profile, Sync Portal Security), and the 3 check queries ran on the database. 

**Running:** *"run Set up a test user"*, or the **Run:** tile on the welcome screen. A start card asks for the inputs, then the steps run in order:

- **Checks** run read-only on the service.
- **Tasks** show their confirm card (the password is typed there and never reaches the service) and run on screen.
- **Processes** are followed in Process Monitor until they end; a failure offers *Why did process N fail?*.
- **Approvals** and **manual** steps wait for a click.

The run stops at the first problem, and every run is logged (who, inputs, each step's result). Claude is not called between steps, so a run costs nothing beyond its tasks. Following a process was verified on the real system (PORTAL_CSS 557093: Success).

**Changing a runbook:**

- **Edit the checklist and attach it again.** **Publish** says the runbook already exists; tick **Replace the existing runbook** and publish again. The new version is live straight away and goes through all the checks again.
- **Edit `runbooks/<id>.yaml` by hand**, then restart the service. A file with a mistake is skipped, and the startup log lists the runbooks that loaded.
- **Add or change a task** that a step uses: record it (or record it again under the same task ID). Recorded tasks are live straight away.

### 11. Watcher: watchdogs that run on their own, day and night

Users with the monitor role set up **watchdogs**, saved in **watch plans**.

- **From the template:**
  1. Choose **＋ Features › Watch jobs** in the chat's bottom bar.
  2. Download the watch plan template (Excel, one row per job, with drop-downs). It comes with AEMINITEST, XRFWIN and PORTAL_CSS as examples.
  3. Fill it in and upload it.
  4. The agent reads it and gives an overview: every watchdog with what it would have caught.
  5. **Confirm and schedule** saves `monitors/<plan>.yaml`.
- **In the chat:** *"watch AEMINITEST overnight and email ops@example.com when it fails"* sets up one watchdog.
- **Four kinds:**
  - **Failure:** processes that end in Error or No Success, optionally only some processes or some users' requests;
  - **Stuck:** requests still Queued, Pending or Initiated N minutes after their run time;
  - **Must succeed by:** a process must have ended in Success by a time of day. This catches a job that **never ran**, and the alert says what happened instead (failed and investigated, still waiting, running late);
  - **Data check:** a read-only query that returns the wrong rows (e.g. locked accounts), every N minutes or at set times.
- **Preview before saving:** e.g. *"would have alerted 11 times in the last 30 days"*, *"1 request is stuck right now"*, *"succeeded on 2 of the last 30 days"*.
- **Managing them:** **◉ N watchdogs** in the bottom bar opens the list, plan by plan. You can:
  - pause, resume, run now or delete a watchdog;
  - download a plan as Excel, pause it or delete it;
  - **Stop all watching**.

  To change a plan, download it, edit it and upload it again; the overview shows what changes before you confirm. Every change is kept with who made it.
- **Runs with nobody signed in:** every minute, one read-only query per monitor that is due, and no Claude.
- **Every failure is investigated:** the troubleshooter reads the message log, the posted log files (downloaded as a service user, `PSAGENT_SVC`), the code and the data.
  - A repeat failure (same process, run control and user within 12 hours) is counted on the open alert, not investigated again.
  - Investigations are capped per day.
- **Alert email:** what happened, the **analysis**, the **action needed**, and links to open the alert in the agent and to Process Monitor. A daily summary goes to the team.
- **In the panel:** the welcome screen shows *"N alerts from the watcher"*. Users can also ask *"what happened overnight?"*, *"what is being watched?"* or *"pause the stuck monitor"*. The email's link opens the agent on that alert.
- **Only reads:** nothing is rerun or changed; the email says what a person should do.

Verified on the real system:

- **Failure:** the watcher found AEMINITEST 557089. It downloaded `AE_AEMINITEST_557089.log` as PSAGENT_SVC and produced the analysis in 43 seconds: a code issue, the hard-coded `&test = 1 / 0;` at Step02 statement 3, failing the same way on every run since 27 Oct. The action needed covered the fix and a new run, because restart is disabled.
- **Stuck:** it found 60 requests stuck while PRCS8622 was running.
- **Chat:** real Claude drafted all three kinds from one sentence each.
- **Template:** real Claude read the uploaded template (4 rows) in 33 seconds. The overview showed:
  - AEMINITEST 11 failures in 30 days;
  - XRFWIN no failures but 1 request stuck now;
  - PORTAL_CSS successful on only 2 of the last 30 days.

  **Run now** raised the stuck alert and the missed deadline ("last success 557097 on 29 Oct"). Uploading the same file again showed "4 unchanged".

### 12. Roles from PeopleSoft

Who can do what is managed on the normal PeopleSoft user profile:

USER < MONITOR < ADMIN (each includes the one before); AUDITOR is kept apart:

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

- **Checked by the service on every request,** from the signed token: a role taken away stops working at once.
- **PeopleSoft's own security still applies inside each feature** (page and CI permissions, query trees, row security).

- **Signed into the token:** the iScript puts the user's roles in the signed token, so they cannot be changed in the browser.
- **No role, no widget:** a user with none of these roles gets no token, and the widget does not show.
- **The `.env` lists** (`PSAGENT_TEACHERS`, `_MONITORS`, `_AUDITORS`) count only in development; a token without roles counts as PSAGENT_USER.

### 13. Regression tests: replay the tasks after a patch or a PUM image

Every recorded task is already an exact replay. On a test environment, it becomes a regression test.

- **Smoke test, no setup:** every task once, with its defaults and a new user ID each run.
- **Test plan template** (Excel, generated from your tasks): one sheet per task, its fields as columns, one row per test case with the expected result:
  - *Saved*;
  - *Process submitted*;
  - *Error* with the expected message, for negative tests such as "a duplicate user ID is refused".

  `{{n}}` gives every run new IDs. Upload it, check the overview and save the suite. Download it to edit it again.
- **Three results:**
  - **Passed**;
  - **Passed with changes**: the page changed and the case was finished live. The report says what changed, e.g. "Description moved: Long Description";
  - **Failed**: the step and PeopleSoft's message.
- **Compared with the last run:** *failing since the last run* and *fixed*. Each report downloads as Excel.
- **Run here** (in the workspace) or **Run unattended:** a headless browser on the agent service signs in as a test user and runs the same widget code. The unattended run starts:
  - on request;
  - every day at a set time;
  - after a patch, when PeopleSoft's update log (`PS_MAINTENANCE_LOG`) changes.

  The report is emailed.
- **Test environments only:** off unless `PSAGENT_TEST_ENV=on`, because the cases really save data.

Verified:

- **Browser test:**
  - run 1: 2 of 2 passed, including the negative case;
  - run 2, on a changed page: *1 passed with changes*, "was Passed", with the changes listed;
  - run 3: unattended, in the service's own headless Chrome: 2 of 2 passed.
- **Real system:** the headless runner signed in to PeopleTools 8.61 as PSAGENT_SVC and connected to the agent service.

### 14. Feature bar

Under the message box: **＋ Features** lists the fixed things the agent does, by the user's roles:

- Watch jobs and Manage watchdogs;
- Regression tests;
- Build or run a runbook;
- Troubleshoot a process;
- Check someone's access;
- Review absence approvals;
- Record a task;
- Ask the data;
- Load with a CI;
- Audit.

Picking one starts a guided flow, such as downloading the template, filling it in and uploading it, or asks the question for you. On the right, **◉ N watchdogs** shows what is being watched and the alerts from the last 24 hours.

### 15. Admin dashboard and audit log

Everything the agent does is recorded:
- every Claude call, with its tokens and an estimated cost;
- every database query, with its SQL and row count;
- every task run, with the confirmed values;
- every save, publish, watchdog change, investigation, alert and regression run.

Auditors (`PSAGENT_AUDITOR`) open it from **＋ Features › Audit**. It opens on the left of the screen; the chat stays on the right.

- **Overview:**
  - people, conversations, task runs (succeeded or failed), Claude cost against the period before, failures, watcher alerts and regression runs;
  - the month's spend against the budget;
  - Claude cost per day;
  - cost by feature, and the top users.
- **Usage and cost:** by user or by feature (messages, conversations, Claude calls, cost, cost per conversation, task runs, failures, queries). It downloads as Excel. Click a user to see what they did.
- **Audit log:** filter by user, feature, kind (actions, database queries, Claude calls), outcome, dates and words. Click a row for the SQL or the confirmed values. It downloads as Excel.
- **Failures:** failed task runs, Claude and database errors, failed investigations, grouped by feature.
- **Refused attempts:** features or tools used without the role, someone else's process or access, rejected tokens. A count on the Overview, and a *Refused* filter in the audit log.
- **Watcher and tests:** watchdogs, recent alerts, regression runs.
- **Ask about it in the chat:** *"who spent the most this week?"*, *"what failed for VELU yesterday?"*. Claude answers from the audit data, for auditors only.
- **Budget alert:** with `PSAGENT_BUDGET`, an email at 80% and at 100% of the monthly Claude budget.
- **Weekly email** (Monday 08:00) to the auditors: users, runs, failures and cost for the week.
- **Privacy:**
  - what people type in the chat and Claude's answers are not kept, only that a message was sent;
  - passwords never reach the service;
  - events are kept for 90 days (`PSAGENT_AUDIT_DAYS`).
- **Cost is an estimate** from the tokens at list prices; the Anthropic invoice is the real figure.

### 16. Excel to CI: load spreadsheet rows through Component Interfaces

ExcelToCI's job without the macro workbook: no VBA, no Windows-only Excel, no connection set up on every PC.

- **＋ Features › Load with a CI** lists the Component Interfaces the user's permission lists allow (457 on HR92U052), with search.
- **Pick one and choose the columns:**
  - the agent reads the CI's shape with **GetCIShape** through the delivered `WEBLIB_SOAPTOCI` iScript, the same call as ExcelToCI's "New Template";
  - it adds PeopleTools metadata: prompt tables, translate values, Yes/No and required edits;
  - keys and required properties are always in the template.
- **Download the template:** a plain `.xlsx` that opens in any spreadsheet app:
  - the CI's sheet: Action (Create / Update), the keys, the chosen properties;
  - one sheet per collection (Roles, Email addresses, …), linked by the parent keys, with a Row action column for updates (Change / Add / Delete);
  - a Help sheet, and drop-downs for fixed values.
- **Upload it,** on the card or by dropping it in the chat. Every row is checked before anything is saved:
  - required fields and keys, lengths, numbers, dates (sent as YYYYMMDD) and times;
  - translate values, accepting the code or its label;
  - prompt table values, checked against the prompt table's key with read-only queries;
  - Create rows whose key exists already, and Update rows whose key doesn't;
  - duplicate rows, and collection rows with no parent.

  The review card shows *"3 ready, 1 with problems"*, each problem with its reason.
- **Load:**
  - each row goes to `WEBLIB_SOAPTOCI` **from the browser, in the user's own PeopleSoft session**, so it is saved as them, with their CI permissions;
  - the CI's own PeopleCode and messages apply, exactly as with ExcelToCI;
  - the card shows progress and the time left, with Pause, Stop and Resume. A load survives closing the browser or restarting the service.
- **Results:**
  - each row Saved, Saved with warnings, or Failed with the CI's message;
  - **Download results**, and **Download the rows to fix**: the template again with only the rows that weren't saved, to correct and upload;
  - **Explain the errors** asks Claude in plain words.
- **Asking in the chat** (*"I need to create 200 users from Excel"*) opens the picker. Claude is not used for the checks or the load, so they cost nothing.
- **Passwords** are never in the file. A CI that has a password property gets it typed on the load card, and the browser fills it in.
- **Every row** goes into the audit log.
- **PeopleSoft access:** the same as ExcelToCI users already have: `WEBLIB_SOAPTOCI` (Web Libraries) and the CI's methods on the permission list's Component Interfaces tab.

Verified:

- the request and answer format was read from the delivered SOAPTOCI PeopleCode on PeopleTools 8.61 (`SOAPTOCI:SOAPToCI`, `ExcelToCI`): the iScript needs `?postDataBin=y`; Create uses the create keys and Update the get keys (they differ on 26 CIs); dates `YYYYMMDD`, times `HHMMSS`, datetimes `YYYY-MM-DD-HH.MM.SS.000000`;
- **every CI on HR92U052 (457)**: its real GetCIShape answer through the whole path (template, filled in, read back, checks, requests), with 1,546 collections up to 3 levels and every property type: no failures;
- **read-only on the live system**: for 250 CIs PeopleSoft found an existing record from exactly the keys our Update request sends (sent as Get, which never saves); the rest were the service account's CI or row security, or no data;
- the browser test runs a full load against a mock SOAPTOCI: 2 saved, 1 failed with the CI's error, 1 refused by the checks.

### 17. Ask the data: answers from curated PS Queries
*"How many new hires this year by department?"*, answered only from PS Queries named `AI_...` that an administrator writes in Query Manager: the agent never writes SQL.

- **The user's queries only:** public AI_ queries and the user's own private ones, whose every record is in the user's query trees (permission lists → query access groups → records), as Query Viewer decides it. Query Viewer's run link does not check query trees (verified on PT 8.61), so the agent builds links only for these.
- **Claude picks the query** (`find_queries`) and fills its prompts from the question (`run_query`), or asks for a value it can't tell. Every value is checked first:
  - dates are sent as `YYYY-MM-DD`: PeopleSoft reads `12/31/2026` or a typo as *no rows*, without an error (verified);
  - translate values (code or label), prompt-table values, Uppercase fields;
  - queries with unnamed prompts (`:1`, from old PeopleTools) are listed with a note, not run.
- **The browser runs it** with Query Viewer's *Run to Excel* link, in the user's own session: PeopleSoft applies the user's row security (query security records) and query limits.
- **The answer card:** the query and prompt values, the row count, the first 200 rows, a chart for a breakdown, **Download Excel** (PeopleSoft's own file) and **Open in Query Viewer**.
- **Claude sees totals, never rows:** the row count and, per column, counts of repeating values, sums, date ranges. A column that tells rows apart (names, IDs) gives only its number of distinct values; sensitive columns (pay, national IDs, birth dates, bank details) nothing. `summarize_result` breaks the result down (by department, by status) under the same rules, optionally of the rows matching a filter.
- **Who has a value:** for *"who has the PSAGENT_ADMIN role?"* Claude calls `find_rows` with the filter (case ignored, `%` = any characters): it gets the number of matches and the first 25, without sensitive columns, and names them; the user gets a card with all the matching rows (up to 200), taken from the whole result, not only the rows on the first card. No filtering on sensitive columns.
- **＋ Features › Ask the data** lists the user's AI_ queries; the audit log records each run (query, prompt values, row count), never the results. Results stay in the service's memory for the conversation, never on disk.

Verified on HR92U052: the catalog SQL (PS and VP1 see AI_ACTIVE_EMP and AI_NEW_HIRES, PSAGENT_SVC none), the run link with prompt values, the date format, and reading a real answer (FGPER803: 324 rows, dates from Excel serial numbers).

### 18. Platform

- **Read-only database access**, enforced three ways even with a privileged account:
  - every statement must be a single SELECT/WITH;
  - every session runs `SET TRANSACTION READ ONLY`;
  - every query is rolled back.
- **Query limits:**
  - results capped at 200 rows and 20 seconds;
  - **sensitive columns masked** (national ID, birth date, pay, bank details, passwords);
  - every query logged with who asked and why.
- **Security of the service:**
  - signed tokens from PeopleSoft;
  - CORS limited to the PeopleSoft address;
  - sessions bound to the signed-in user.
- **Prompt caching** keeps repeated calls cheap.
- **Tests:**
  - 232 unit tests, one of them calling the real Claude API on request (`PSAGENT_LIVE_API=1`);
  - 23 browser tests against mock PeopleSoft pages and a scripted Claude;
  - each feature also run on the real system.

---

## Safety principles

These apply to every feature:

1. **Nothing is saved, submitted or granted without the user's click.** The service enforces this, not the model.
2. **Passwords are never seen** by the service or Claude.
3. **Troubleshooting only reads.** Reruns and fixes happen only when the user asks.
4. **Values are never invented.** If something is missing or unclear, the agent asks or flags it.
5. **Page, document, log and database text is treated as data, never as instructions.**
6. **Sensitive data stays masked**, and every database query is logged.

## What it costs

Measured on Claude Opus 5.5:

| Action | Cost | Time |
|---|---|---|
| Recorded task run (e.g. create a user) | about $0.02 | |
| Live mode, per page step | about $0.015–0.02 | |
| Process investigation | a few tens of cents | 20–60 seconds |
| Security access check | similar to a process investigation | about 25 seconds |
| Absence request review | similar to a process investigation | 30–45 seconds |
| Runbook draft from a checklist | a few cents to a few tens of cents | about 30 seconds |
| Monitor set up in the chat | a few cents | 7–25 seconds |
| Watch plan from the template (4 rows) | a few cents | about 30 seconds |
| Watcher, per monitor run | nothing (one read-only query, no Claude) | |
| Watcher, per new failure | like a process investigation | about 45 seconds |
| Admin dashboard and audit log | nothing (a local SQLite file); asking about it in the chat is a normal chat turn | |
| Ask the data | two to three chat turns per question (choosing the query, then answering from the totals) | |
| Excel to CI load | nothing: no Claude call for the checks or the rows ("Explain the errors" is one chat turn) | about a second per row |

---

## Future features

### Next up

The four big features to build next, in the order chosen.

1. **Admin dashboard and audit log**: built (section 15).
2. **Excel to CI**: built (section 16). Still to come:
   - the same bulk load for recorded tasks (screen replay), for pages without a CI;
   - large files through the Process Scheduler: above a set number of rows (about 1,000), the browser stages the checked rows through an iScript in the user's session and submits an App Engine that loads them with the delivered `SOAPTOCI:ExcelToCI` class, as the user, with a process instance; the card follows it in Process Monitor (needs an App Designer project);
   - Claude matching the columns of a spreadsheet that isn't the template.
3. **Ask the data**: built (section 17), from curated `AI_` PS Queries. Still to come:
   - a query's result as a watchdog (*"tell me when this number changes"*);
   - favourite questions on the welcome screen.
4. **Fix and rerun:** this closes the loop on the watcher and the troubleshooter.
   - After an alert and its diagnosis, the agent proposes the fix when it is a known one.
   - After a person's click, **Rerun** resubmits the request. The watcher confirms the new run succeeded and closes the incident.
   - The night shift goes from *tells you* to *fixes with you*.

### Security

- **Apply for row security:** run *Refresh SJT_OPR_CLS* for one user, with approval. This closes the VELU case end to end.
- **"Why can A but not B?":** compare two users' access to the same page.
- **More checks:**
  - sign-on times;
  - dynamic role rules;
  - query security;
  - process security;
  - Finance row security (business unit, SetID).
- **Access reviews:** who can open a page, segregation-of-duties conflicts, and unused roles, all as reports.
- **Access request workflow:** a user asks for access, and the agent proposes the smallest role and routes it to a security admin.

### Troubleshooting

- **Stuck processes:** requests that stay Queued (no server, server down, Max Concurrent reached).
- **SQR and COBOL:** read the source from the process server for failures outside Application Engine.
- **Integration Broker:** failed or stuck messages and subscriptions.
- **Demos with delivered HCM programs:** for example two Refresh EMPLOYEES Table (PER099) runs at once, and a data issue in compa-ratio (`tests/ae_demo/PSA_COMPA.md`).

### Runbooks

Closing the gaps in the first version:

- **Edit a published runbook in the chat:** *"add a step to Set up a test user that checks the email address"*. The agent loads the published runbook, shows the changed card, and you publish again.
- **No step is ever dropped:** a step whose task doesn't exist yet becomes a manual step, with a **Record this task now** button. Once it is recorded, the step becomes a task step.
- **Real approvals:** the approval goes to the named approver (role-checked), by Teams or email with Approve and Reject buttons. The run waits and carries on when they answer.
- **Resume and hand-off:** a run is kept on the service, so it survives closing the browser, and another person can take over a step (analyst to payroll manager).
- **Version history:** every publish keeps the previous version, with a diff of what changed and who changed it.

Making it stand out:

- **Fix and carry on:** when a process fails, the troubleshooter runs straight away and shows the diagnosis in the run. After the fix, the run restarts from the failed step, not from the beginning.
- **A step for every row:** a check that returns rows (employees with a calculation error, users without a role) runs the fix task once per row, with one confirm card for all of them.
- **Scheduled runs from the pay calendar:** the pay run runbook starts itself for each pay period. Checks and processes run unattended; the run stops for tasks and approvals and notifies the person.
- **Run cockpit:** one page with every run in progress (month-end close, the pay run), each step green, red or waiting, how long it took and who did it.
- **Compare with the last run:** at the end Claude summarises what is different from last time (*"412 paychecks, 3% more than last period: 12 new hires in KU1"*) so the unusual is noticed before confirming.
- **Audit evidence pack:** one PDF per run with each check's result, every process instance, the approvals and the timestamps, for auditors (SOX).
- **Rehearsal:** run all the checks and show every task's confirm card without saving anything, to try a runbook safely before the real run.
- **Record a whole runbook:** the recorder follows you across several pages and processes and turns the sequence into a runbook, for teams that don't have a written checklist.

### Watcher

- **More kinds:** Integration Broker errors, long-running processes (much longer than usual), and approvals stuck for days.
- **Pay cycle sheet:** NA Payroll stages (PSPPYBLD, PSPPYRUN, PSPCNFRM, PAY003, DDP001) with deadlines relative to the pay calendar's check date, and calc-error checks after each stage.
- **Small edits in the list:** change a schedule or email without the Excel round trip.
- **Teams** as well as email.
- **Regression tests, next:** screenshots of failed steps in the report, comparing whole runs before and after a PUM image side by side, and test data cleanup after a run.
- **Learn what is normal:** alert when a process takes much longer, or processes far fewer rows, than its usual runs.

### Data and documents

- **Finance extension:** invoice → voucher, and PO and requisition from documents, with matching to POs and receipts.
- **Data fixes:** propose correction SQL, or the page steps, for review and approval.

### Agent and recorder

- **Fluid pages** in live mode and the recorder (classic pages today).
- **Task library:** versioning, sharing tasks between environments, and test runs before publishing.
- **More approvals:** the absence reviewer for other approval types (timesheets, promotions, job openings), and approve or deny from the review card through live mode, after the approver's click.
- **Explain the process:** walk a new user through a business process step by step on their own screen.

### Operations

- **Dedicated read-only database account** (`PSAGENT_RO`) instead of SYSADM.
- **Scaling:** shared sessions (Redis) and several workers.
- **Production rollout:** a reverse proxy under the PeopleSoft host, keys in a vault, and a review of the security model.
