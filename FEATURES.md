# PeopleSoft Agent: features

A chat agent inside PeopleSoft that works on screen like a person: it does
tasks, learns new ones by watching, reads documents, works out unfamiliar pages
on its own, and troubleshoots failed processes and security access.

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

Users allowed to teach (`PSAGENT_TEACHERS`) click **Record**, do the task once by hand, and the agent turns the recording into a reusable task.

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

For security administrators only: users with the Security Administrator or PeopleSoft Administrator role, or listed in `PSAGENT_SECURITY_ADMINS`.

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

### 9. Platform

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
  - 94 unit tests;
  - 13 browser tests against mock PeopleSoft pages and a scripted Claude;
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

---

## Future features

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

- **Rerun on request** after a fix, then confirm the new run succeeded.
- **Stuck processes:** requests that stay Queued (no server, server down, Max Concurrent reached).
- **SQR and COBOL:** read the source from the process server for failures outside Application Engine.
- **Integration Broker:** failed or stuck messages and subscriptions.
- **Monitoring:** watch for failed processes, investigate automatically, and notify by email or Teams with the diagnosis.
- **Demos with delivered HCM programs:** for example two Refresh EMPLOYEES Table (PER099) runs at once, and a data issue in compa-ratio (`tests/ae_demo/PSA_COMPA.md`).

### Data and documents

- **Finance extension:** invoice → voucher, and PO and requisition from documents, with matching to POs and receipts.
- **Bulk loads (ExcelToCI-style):** upload a spreadsheet, the agent validates every row and shows one review table, then loads row by row with results.
- **Questions in plain language:** *"how many active employees in company S13?"* answered with a read-only query and its result.
- **Data fixes:** propose correction SQL, or the page steps, for review and approval.

### Agent and recorder

- **Fluid pages** in live mode and the recorder (classic pages today).
- **Task library:** versioning, sharing tasks between environments, and test runs before publishing.
- **Approvals assistant:** summarize pending approvals and approve with one click each.
- **Explain the process:** walk a new user through a business process step by step on their own screen.

### Operations

- **Dedicated read-only database account** (`PSAGENT_RO`) instead of SYSADM.
- **Scaling:** shared sessions (Redis) and several workers.
- **Admin dashboard:** usage, cost per user and feature, an audit log of every action and query, and failed runs.
- **Production rollout:** a reverse proxy under the PeopleSoft host, keys in a vault, and a review of the security model.
