"""PSAgent end-to-end test workbook."""
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

OUT = r"C:\Velu\2026\5AgenticWorkflow\pscopilotagentjs\docs\PSAgent_E2E_Test_Cases.xlsx"

F = "Arial"
NAVY = "1F3864"
INPUT = PatternFill("solid", fgColor="FFF2CC")     # cells the tester fills in
HEAD = PatternFill("solid", fgColor=NAVY)
BAND = PatternFill("solid", fgColor="D9E2F3")
thin = Side(style="thin", color="BFBFBF")
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="top", wrap_text=True)

STATUSES = ["Not run", "Pass", "Fail", "Blocked", "N/A"]

# ---------------------------------------------------------------------------------------------------------------
# Test cases: (id, feature, scenario, role, priority, type, preconditions, steps, data, expected)
# ---------------------------------------------------------------------------------------------------------------
C = []


def tc(*a):
    C.append(a)


W = "01 Chat widget"
tc("WID-01", W, "Widget loads for a user with an agent role", "ADMIN", "P1", "Positive",
   "Setup sheet done (service restarted, psagent.js uploaded).",
   "1. Sign in to PeopleSoft.\n2. Open any classic page (e.g. PeopleTools > Security > User Profiles).\n3. Press Ctrl+/.\n4. In the browser console (F12) type PSAgent.version.",
   "", "The agent launcher shows on the page; Ctrl+/ opens and closes the panel; the welcome screen shows tiles for the user's role; PSAgent.version is 2026-11-02.5.")
tc("WID-02", W, "No agent role, no widget", "NONE", "P1", "Security",
   "Test user without any PSAGENT_ role.",
   "1. Sign in as the user without a role.\n2. Open several pages.",
   "", "No agent launcher on any page; no script error on the page.")
tc("WID-03", W, "Explain this page", "USER", "P2", "Positive", "",
   "1. Open Job Data (Workforce Administration > Job Information > Job Data).\n2. Open the panel: the bar shows 'You are on ...'.\n3. Click Explain this page.",
   "", "The context bar names the page; the answer explains what the page is for in plain words.")
tc("WID-04", W, "Help with an error on the page", "USER", "P2", "Positive", "",
   "1. Cause a PeopleSoft error on a page (e.g. save a required field blank).\n2. Open the panel and click Help with this error.",
   "", "The agent quotes the error and explains the cause and what to do.")
tc("WID-05", W, "History and layout", "USER", "P3", "Positive", "",
   "1. Ask a question.\n2. Navigate to another PeopleSoft page.\n3. Open the panel again; switch to wide mode.",
   "", "The conversation is still there on the new page; wide mode widens the panel.")
tc("WID-06", W, "Activity feed shows the agent's steps", "USER", "P3", "Positive", "Run TASK-01 or DATA-02.",
   "1. Watch the panel while a task or query runs.", "", "Each step appears in the activity feed with its timing; the avatar shows working, then ready.")
tc("WID-07", W, "Service down: clear message", "USER", "P2", "Negative", "Admin can stop the service.",
   "1. Stop the agent service.\n2. Send a message.\n3. Start the service again and send another message.",
   "", "A clear 'service unavailable' message, no technical error or stack trace; after the restart the chat works again.")

R = "02 Roles and access"
tc("ROLE-01", R, "USER sees only USER features", "USER", "P1", "Security", "",
   "1. Open the panel.\n2. Click ＋ Features.",
   "", "Shown: Troubleshoot a process, Check my access, Review absence approvals, Ask the data, Load with a CI, run runbooks. Not shown: Watch jobs, Manage watchdogs, Record a task, Build a runbook, Check someone's access, Regression tests, Audit.")
tc("ROLE-02", R, "MONITOR adds the watcher", "MONITOR", "P1", "Security", "",
   "1. Open the panel.\n2. Click ＋ Features; look at the bottom bar.",
   "", "Everything USER has, plus Watch jobs, Manage watchdogs and ◉ N watchdogs. Not shown: Record a task, Check someone's access, Audit.")
tc("ROLE-03", R, "ADMIN adds teaching and others' access", "ADMIN", "P1", "Security", "",
   "1. Open the panel.\n2. Click ＋ Features.",
   "", "Everything MONITOR has, plus Record a task, Build a runbook, Check someone's access, and Regression tests (only if PSAGENT_TEST_ENV=on). Audit is NOT shown.")
tc("ROLE-04", R, "AUDITOR: dashboard only, no work", "AUDITOR", "P1", "Security", "",
   "1. Click ＋ Features.\n2. Type: create a user profile for TSTAUD01.",
   "", "Only Audit is offered. The task is refused: the answer says it needs the PSAGENT_USER role. Nothing runs.")
tc("ROLE-05", R, "USER cannot check another user's access", "USER", "P1", "Security", "",
   "1. Type: why can't PS open Job Data?", "",
   "Refused: checking other users' access is for PSAGENT_ADMIN users. Recorded as Refused in the audit log (AUD-05).")
tc("ROLE-06", R, "USER cannot set up a watchdog", "USER", "P1", "Security", "",
   "1. Type: watch AEMINITEST and email me when it fails.", "",
   "Refused: needs PSAGENT_MONITOR. Recorded as Refused.")
tc("ROLE-07", R, "MONITOR cannot record a task", "MONITOR", "P2", "Security", "",
   "1. Type: I want to record a new task.", "", "Refused: recording is for PSAGENT_ADMIN. Recorded as Refused.")
tc("ROLE-08", R, "Role taken away stops at once", "ADMIN", "P1", "Security",
   "A second admin can edit PS's roles.",
   "1. As PS, confirm Record a task is offered.\n2. In PeopleSoft remove PSAGENT_ADMIN from PS (keep PSAGENT_USER).\n3. Reload the page.\n4. Ask to record a task.\n5. Give the role back.",
   "", "After the reload Record a task is gone, and asking for it is refused. With the role back (and a reload) it returns.")
tc("ROLE-09", R, "Service refuses calls without a token", "Tester (no browser)", "P1", "Security", "curl or Postman.",
   "1. Call GET https://<service>/api/me with no Authorization header.", "", "HTTP 401. No data returned.")
tc("ROLE-10", R, "Service refuses a forged token", "Tester (no browser)", "P1", "Security",
   "Copy a real token from the browser (Network tab, Authorization header).",
   "1. Change one character of the token (or change the roles part to ADMIN).\n2. Call GET /api/me with it.",
   "", "HTTP 401. The audit log shows Refused: 'Rejected token' (AUD-05).")
tc("ROLE-11", R, "Development mode refused on a real server", "Admin (server)", "P2", "Security",
   "A copy of .env on a test server.",
   "1. Set PSAGENT_DEV_OPRID=PS while PSAGENT_SHARED_SECRET is set.\n2. Start the service.\n3. Remove PSAGENT_DEV_OPRID again.",
   "", "The service refuses to start: 'PSAGENT_DEV_OPRID and PSAGENT_SHARED_SECRET are both set'.")

T = "03 Tasks"
tc("TASK-01", T, "Create User Profile: happy path", "USER", "P1", "Positive",
   "USER has access to the User Profiles component.",
   "1. Type: create a user profile for TSTUSR01, description Test User 01, role EMPLOYEE.\n2. Answer any question.\n3. Check the confirm card; type the password on the card.\n4. Click Confirm.",
   "User ID TSTUSR01; role EMPLOYEE", "Asks only for missing values; the card lists every value; the task runs on screen; it ends with Saved. TSTUSR01 exists in User Profiles with role EMPLOYEE.")
tc("TASK-02", T, "Never invents values", "USER", "P1", "Negative", "",
   "1. Type: create a user profile.", "",
   "The agent asks for the user ID and the other required values; it does not make up IDs, roles or permission lists.")
tc("TASK-03", T, "Cancel on the confirm card", "USER", "P1", "Negative", "",
   "1. Start TASK-01 with user TSTUSR02.\n2. Click Cancel on the card.", "TSTUSR02",
   "Nothing runs; TSTUSR02 does not exist.")
tc("TASK-04", T, "Password never through the chat", "USER", "P1", "Security", "",
   "1. Start a user profile task.\n2. Type the password in a chat message.", "",
   "The agent does not take it from the chat; the password is typed only on the card. It never appears in the audit log (AUD-08).")
tc("TASK-05", T, "Duplicate user: PeopleSoft error explained", "USER", "P2", "Negative", "TSTUSR01 exists (TASK-01).",
   "1. Create a user profile for TSTUSR01 again and confirm.", "TSTUSR01",
   "The run stops on PeopleSoft's 'already exists' message and explains it; nothing else is changed.")
tc("TASK-06", T, "Change a value after the card", "USER", "P2", "Positive", "",
   "1. Start TASK-01 for TSTUSR03 until the card shows.\n2. Type: change the description to Test User Three.", "TSTUSR03",
   "The old card is withdrawn and a new card shows the new description.")
tc("TASK-07", T, "Sync Portal Security", "ADMIN", "P2", "Positive", "",
   "1. Type: run the portal security sync.\n2. Confirm.", "",
   "The process is submitted from the run control; the agent shows the process instance; Process Monitor shows it.")
tc("TASK-08", T, "Run an Application Engine program", "ADMIN", "P2", "Positive", "",
   "1. Type: run the AE program AEMINITEST.\n2. Confirm.", "AEMINITEST",
   "Process instance shown. (AEMINITEST fails on purpose: use this instance in PTS-02 and WAT-03.)")

RC = "04 Recorder"
tc("REC-01", RC, "Record a new task", "ADMIN", "P1", "Positive",
   "A simple classic Add page, e.g. Set Up HCM > Foundation Tables > Organization > Location.",
   "1. ＋ Features > Record a task; title 'Add a Location'.\n2. Do the task once by hand and save.\n3. Stop recording.\n4. Review the card (fields to ask, fixed, optional) and save.",
   "Location TSTLOC01", "The review card lists the fields with Claude's suggested names; after saving, the task is available at once (a tile or by asking).")
tc("REC-02", RC, "Run the recorded task with new values", "USER", "P1", "Positive", "REC-01 saved.",
   "1. Type: add a location TSTLOC02, description Test Location 2.\n2. Confirm.", "TSTLOC02",
   "The task runs on screen with the new values and ends with Saved.")
tc("REC-03", RC, "Undone changes are left out", "ADMIN", "P3", "Positive", "",
   "1. Record a task; type a value in a field, then clear it again; click a button that does nothing.\n2. Stop and review.", "",
   "The reverted field and the empty click are not in the recorded steps.")
tc("REC-04", RC, "Record a process run (Process Scheduler pop-up)", "ADMIN", "P2", "Positive", "",
   "1. Record: open a run control, click Run, choose the process in the pop-up, OK.\n2. Stop and save.\n3. Run the new task.", "",
   "The pop-up is recorded; running the task submits the process and shows its instance.")

LV = "05 Live mode"
tc("LIVE-01", LV, "Live mode on a page never taught", "USER", "P1", "Positive", "TSTUSR01 exists.",
   "1. Type: do it live - change the description of user TSTUSR01 to 'Live test'.\n2. Watch the steps; approve the Save when asked.", "",
   "The agent finds the page, changes the field, asks for approval before Save, then saves. The description is changed.")
tc("LIVE-02", LV, "Save refused by the user", "USER", "P1", "Negative", "",
   "1. Repeat LIVE-01 with 'Live test 2'.\n2. Decline the approval.", "", "Nothing is saved; the description is unchanged.")
tc("LIVE-03", LV, "Missing value asked for", "USER", "P2", "Positive", "",
   "1. Type: do it live - add an email address to user TSTUSR01.", "",
   "The agent asks for the address (and type) instead of guessing.")
tc("LIVE-04", LV, "Live recovery after a page change (optional)", "USER", "P3", "Positive",
   "A recorded task whose page was changed (e.g. a field moved by personalization or a PUM update).",
   "1. Run the recorded task.", "",
   "Where the recorded step fails, live mode finishes it; the Save is approved by the user; the agent offers to update the task.")

D = "06 Documents"
tc("DOC-01", D, "Access form with two people", "USER", "P2", "Positive",
   "A PDF or image access request listing 2 people (one value unclear or handwritten).",
   "1. Attach the form; type: set these people up.\n2. Review the table; fix the flagged value.\n3. Run.", "",
   "One review table with both people; the unclear value is flagged; rows run one by one with a result each.")
tc("DOC-02", D, "Untick a row", "USER", "P3", "Positive", "", "1. Repeat DOC-01; untick one person before running.", "",
   "Only the ticked row runs.")
tc("DOC-03", D, "Spreadsheet or photo", "USER", "P3", "Positive", "", "1. Attach the same request as Excel or as a phone photo.", "",
   "Read the same way as the PDF.")

PT = "07 Process troubleshooter"
tc("PTS-01", PT, "My last failed process", "USER", "P1", "Positive", "USER has a failed process of their own.",
   "1. Type: why did my last process fail?", "",
   "Diagnosis card: category and confidence, exact place, root cause, evidence, fix steps, how to rerun, the queries it ran, Copy report.")
tc("PTS-02", PT, "AE failure located to the statement", "ADMIN", "P1", "Positive", "The AEMINITEST instance from TASK-08.",
   "1. Type: why did process <instance> fail?", "Instance from TASK-08",
   "Division by zero at Step02, statement 3 (the hard-coded 1/0); fix and rerun steps.")
tc("PTS-03", PT, "USER cannot investigate another user's process", "USER", "P1", "Security", "An instance run by someone else.",
   "1. Type: why did process <other instance> fail?", "", "Refused: only PSAGENT_MONITOR and ADMIN look into other people's processes. Recorded as Refused.")
tc("PTS-04", PT, "MONITOR investigates anyone's process", "MONITOR", "P2", "Positive", "", "1. Ask about the instance from PTS-03.", "",
   "Investigated; the agent may run its own read-only queries (listed on the card).")
tc("PTS-05", PT, "Overlapping runs", "ADMIN", "P2", "Positive",
   "A PORTAL_CSS (or other) failure caused by runs at the same time.", "1. Ask why it failed.", "",
   "The card names the overlapping runs and the Max Concurrent / mutual-exclusion setting as the cause.")
tc("PTS-06", PT, "Guided flow and big log files", "USER", "P3", "Positive", "A process with a large trace file.",
   "1. ＋ Features > Troubleshoot a process; give the instance.", "", "Completes; only the end of a very large file is read; no browser freeze.")

S = "08 Security troubleshooter"
tc("SEC-01", S, "Check my own access", "USER", "P1", "Positive", "", "1. Type: why can't I open Job Data?", "",
   "Card with the ✓/✕ chain for the user (component, roles, permission lists, actions, menu, row security); no Apply button.")
tc("SEC-02", S, "Another user's access, least-privilege fix", "ADMIN", "P1", "Positive", "",
   "1. Type: why can't ABSMITH access Create Job Opening?", "ABSMITH",
   "Missing HCCPRS1040; smallest role Hiring Manager with its side effects (other pages, users affected); alternatives; links to User Profile, Role and Permission List.")
tc("SEC-03", S, "Apply stops before Save", "ADMIN", "P1", "Positive", "SEC-02 card shown.",
   "1. Click Apply.\n2. When it stops for approval, decline.", "",
   "A live run makes exactly the proposed change and stops for approval before Save; declining leaves the profile unchanged.")
tc("SEC-04", S, "Row security (SJT) case", "ADMIN", "P2", "Positive", "A user with no row-security entries (e.g. VELU).",
   "1. Type: why can't VELU find anyone in Job Data?", "", "Points to the empty SJT_OPR_CLS and recommends a refresh, not a new role.")

A = "09 Absence reviewer"
tc("ABS-01", A, "My pending approvals", "USER (approver)", "P1", "Positive", "A pending absence request waiting for this user.",
   "1. Type: show my pending absence approvals.", "", "The list shows the requests waiting for the user.")
tc("ABS-02", A, "Review with AI", "USER (approver)", "P1", "Positive", "ABS-01 list shown.",
   "1. Click Review with AI on one request.", "",
   "Card: approval status from PeopleSoft, recommendation, confidence 0-100 with reasons, each check (balance, overlaps, team, timing, history), a suggested comment. The agent does not approve; Open Approvals opens PeopleSoft.")
tc("ABS-03", A, "USER cannot see everyone's requests", "USER", "P2", "Security", "",
   "1. Type: show everyone's pending absence requests.", "", "Only the user's own; everyone's is refused (ADMIN only).")
tc("ABS-04", A, "ADMIN sees everyone's", "ADMIN", "P2", "Positive", "", "1. Type: show all pending absence requests.", "",
   "All pending requests are listed.")

RB = "10 Runbooks"
tc("RUN-01", RB, "Build a runbook from a checklist", "ADMIN", "P1", "Positive", "The 5-step test checklist template (runbooks/templates).",
   "1. Attach the checklist; type: build a runbook from this checklist.", "",
   "Draft card: steps by type (check, task, process, approval, manual), the inputs each task will ask, check queries test-run with today's result, Claude's questions.")
tc("RUN-02", RB, "Publish", "ADMIN", "P1", "Positive", "RUN-01 card.", "1. Click Publish.", "",
   "Saved; a 'Run: <name>' tile appears on the welcome screen.")
tc("RUN-03", RB, "Run a runbook step by step", "USER", "P1", "Positive", "RUN-02 published.",
   "1. Click the Run tile; fill the inputs.\n2. Go through each step (checks, task confirm card, manual step, approval).", "",
   "Steps run in order; checks run read-only; the task shows its confirm card; manual and approval steps wait for a click; the run stops at the first problem.")
tc("RUN-04", RB, "Process step followed to the end", "USER", "P2", "Positive", "A runbook with a process step.",
   "1. Run it.", "", "The process is followed in Process Monitor to Success; on a failure the card offers 'Why did process N fail?'.")
tc("RUN-05", RB, "Replace a published runbook", "ADMIN", "P3", "Positive", "", "1. Edit the checklist; attach it again; Publish; tick Replace.", "",
   "Publish says it exists; with Replace ticked the new version is live at once.")
tc("RUN-06", RB, "USER cannot build a runbook", "USER", "P2", "Security", "", "1. Attach a checklist; ask to build a runbook.", "",
   "Refused (PSAGENT_ADMIN only). Recorded as Refused.")

WT = "11 Watcher"
tc("WAT-01", WT, "Watch plan from the template", "MONITOR", "P1", "Positive", "",
   "1. ＋ Features > Watch jobs; download the template.\n2. Keep the 3 example rows (AEMINITEST failure, XRFWIN stuck, PORTAL_CSS must succeed by).\n3. Upload; read the overview.\n4. Confirm and schedule.", "",
   "Overview: each watchdog with what it would have caught (e.g. 'would have alerted N times in 30 days'); after confirming, ◉ N watchdogs shows them.")
tc("WAT-02", WT, "One watchdog from the chat", "MONITOR", "P1", "Positive", "",
   "1. Type: watch AEMINITEST overnight and email <your address> when it fails.\n2. Check the preview; save.", "",
   "Draft card with the kind (failure), the process, the recipients and a preview; saved after the click.")
tc("WAT-03", WT, "Failure alert with analysis", "MONITOR", "P1", "Positive", "WAT-02 saved; SMTP set for the email part.",
   "1. Run AEMINITEST (TASK-08).\n2. Wait a few minutes.\n3. Open the panel; open the email.", "",
   "Welcome screen: 'N alerts from the watcher'; the alert has the analysis (root cause, action needed); the email has the same with links to the agent and Process Monitor.")
tc("WAT-04", WT, "Data check watchdog", "MONITOR", "P2", "Positive", "",
   "1. Type: every 30 minutes, alert me if any user profile is locked.\n2. Check the preview rows; save.", "",
   "Preview shows today's matching rows; the check runs on schedule; an alert when it finds rows.")
tc("WAT-05", WT, "Manage watchdogs", "MONITOR", "P2", "Positive", "WAT-01 done.",
   "1. Click ◉ N watchdogs.\n2. Pause, resume, Run now, delete one.\n3. Download the plan as Excel.", "",
   "Each action works and is shown; the download is the plan in the template format.")
tc("WAT-06", WT, "Upload the same plan again", "MONITOR", "P3", "Positive", "", "1. Upload the downloaded plan unchanged.", "",
   "The overview says the watchdogs are unchanged.")
tc("WAT-07", WT, "What happened overnight?", "MONITOR", "P2", "Positive", "Alerts exist.", "1. Type: what happened overnight?", "",
   "The alerts of the last hours, with links.")
tc("WAT-08", WT, "Repeat failure not investigated twice", "MONITOR", "P3", "Positive", "", "1. Run AEMINITEST again within 12 hours.", "",
   "Counted on the open alert; no second investigation.")
tc("WAT-09", WT, "Daily summary email", "MONITOR", "P3", "Positive", "SMTP set; PSAGENT_ALERT_TO set.", "1. Wait for 07:00 (or the configured time).", "",
   "The summary email arrives.")
tc("WAT-10", WT, "Stop all watching", "MONITOR", "P3", "Positive", "", "1. ◉ N watchdogs > Stop all watching.\n2. Start it again.", "",
   "No watchdog runs while stopped; resumes after.")

RG = "12 Regression tests"
tc("REG-01", RG, "Smoke test of every task", "ADMIN", "P2", "Positive", "Test system only; PSAGENT_TEST_ENV=on.",
   "1. ＋ Features > Regression tests > smoke test > Run here.", "", "Every task runs once with new IDs; a report with Passed / Failed per task.")
tc("REG-02", RG, "Test plan with a negative case", "ADMIN", "P2", "Positive", "",
   "1. Download the test plan template.\n2. Add a case expecting Error 'already exists' (duplicate user).\n3. Upload, check the overview, save, run.", "",
   "Both the normal and the negative case pass.")
tc("REG-03", RG, "Compared with the last run; Excel report", "ADMIN", "P3", "Positive", "Two runs done.", "1. Open the latest report; download it.", "",
   "Shows 'failing since the last run' / 'fixed' where relevant; the Excel report downloads.")
tc("REG-04", RG, "Unattended run on the service", "ADMIN", "P3", "Positive", "PSAGENT_TEST_USER / PASSWORD set; Playwright installed on the service.",
   "1. Click Run unattended.", "", "The service's headless browser signs in and runs the suite; the report is shown (and emailed if SMTP is set).")
tc("REG-05", RG, "Off outside test systems", "ADMIN", "P2", "Security", "PSAGENT_TEST_ENV off.", "1. Open ＋ Features.", "",
   "Regression tests is not offered.")

AU = "13 Admin dashboard and audit"
tc("AUD-01", AU, "Open the dashboard", "AUDITOR", "P1", "Positive", "Earlier test cases have generated activity.",
   "1. ＋ Features > Audit.", "", "The dashboard opens on the left, the chat stays on the right. Overview: people, conversations, task runs, Claude cost, failures, Refused, alerts; cost per day; cost by feature; top users.")
tc("AUD-02", AU, "Usage and cost", "AUDITOR", "P2", "Positive", "", "1. Open Usage and cost; switch by user / by feature.\n2. Click a user.\n3. Download Excel.", "",
   "Numbers per user and feature; drilling into a user shows what they did; the Excel matches the screen.")
tc("AUD-03", AU, "Audit log filters", "AUDITOR", "P1", "Positive", "",
   "1. Open the audit log.\n2. Filter by user, feature, kind (actions, database queries, Claude calls), outcome, dates and words.\n3. Click a row.\n4. Download Excel.", "",
   "Filters work; a row shows its SQL or confirmed values; the Excel downloads.")
tc("AUD-04", AU, "Failures tab", "AUDITOR", "P2", "Positive", "Some failed runs exist (TASK-05, CI-07).", "1. Open Failures.", "",
   "Failed task runs, errors and failed investigations, grouped by feature.")
tc("AUD-05", AU, "Refused attempts", "AUDITOR", "P1", "Security", "ROLE-05, 06, 07, 10 and PTS-03 done.",
   "1. Overview: read the Refused count.\n2. Audit log: Outcome = Refused.", "",
   "Every refused attempt from those tests is there with the user and the reason; the forged token shows as 'Rejected token'.")
tc("AUD-06", AU, "Ask about usage in the chat", "AUDITOR", "P2", "Positive", "",
   "1. As AUDITOR type: who spent the most this week?\n2. As USER ask the same.", "",
   "AUDITOR gets an answer from the audit data; USER is refused.")
tc("AUD-07", AU, "Chat text is not kept", "AUDITOR", "P1", "Security", "",
   "1. As USER, send a message with a unique phrase (e.g. 'purple-elephant-42').\n2. As AUDITOR search the audit log for it.", "purple-elephant-42",
   "Not found: only that a message was sent is recorded.")
tc("AUD-08", AU, "Passwords never reach the service", "AUDITOR", "P1", "Security", "TASK-01 done with a known test password.",
   "1. Search the audit log for the password typed on the card.", "", "Not found anywhere.")
tc("AUD-09", AU, "Budget and weekly emails", "AUDITOR", "P3", "Positive", "SMTP set; a low PSAGENT_BUDGET on a test server.",
   "1. Use the agent until spend passes 80% of the budget.\n2. Wait for Monday 08:00.", "", "Budget email at 80% (and 100%); the weekly usage email arrives.")

CI = "14 Excel to CI"
tc("CI-01", CI, "Pick a CI", "USER", "P1", "Positive", "USER has WEBLIB_SOAPTOCI and USER_PROFILE on a permission list.",
   "1. ＋ Features > Load with a CI.\n2. Search USER_PROFILE.", "", "The list shows the CIs the user's permission lists allow; search finds USER_PROFILE.")
tc("CI-02", CI, "Download the template", "USER", "P1", "Positive", "",
   "1. Pick USER_PROFILE; choose a few columns.\n2. Download the template; open it.", "",
   "Main sheet with Action, the keys and the chosen columns; one sheet per chosen collection (e.g. Roles) with the parent keys and Row action; a Help sheet; drop-downs for fixed values.")
tc("CI-03", CI, "Create two records", "USER", "P1", "Positive", "",
   "1. Fill 2 rows with Action = Create.\n2. Upload; read the review card.\n3. Type the password on the load card if asked; Load.", "TSTCI001, TSTCI002",
   "Review: 2 ready; load: 2 Saved. Both users exist in PeopleSoft, saved as the signed-in user.")
tc("CI-04", CI, "Update a record", "USER", "P1", "Positive", "CI-03 done.", "1. One row: Action = Update, TSTCI001, new description.\n2. Upload and load.", "",
   "Saved; the description changed.")
tc("CI-05", CI, "Checks before loading", "USER", "P1", "Negative", "",
   "1. Rows with: a missing key; a wrong drop-down value; Create of an existing user; Update of a user that doesn't exist; a value too long; a bad date.\n2. Upload.", "",
   "Each bad row is listed with its reason ('3 ready, 6 with problems'); bad rows are not loaded.")
tc("CI-06", CI, "Collection rows: add a role", "USER", "P2", "Positive", "", "1. Roles sheet: TSTCI001, Row action Add, role EMPLOYEE.\n2. Upload and load.", "",
   "Saved; the user has the new role.")
tc("CI-07", CI, "Failure from the CI, rows to fix", "USER", "P2", "Negative", "",
   "1. A row that passes the checks but that the CI's PeopleCode rejects.\n2. Load.\n3. Download the rows to fix; click Explain the errors.", "",
   "Row Failed with the CI's own message; the download holds only the failed rows; the explanation is in plain words.")
tc("CI-08", CI, "Pause, stop, resume", "USER", "P2", "Positive", "A file of ~50 rows.",
   "1. Load; Pause; Resume.\n2. Close the browser mid-load; open it again from Load with a CI.", "", "Progress and time left shown; the load continues where it stopped.")
tc("CI-09", CI, "Template dropped in the chat", "USER", "P3", "Positive", "", "1. Drag a filled template into the chat.", "",
   "The review card opens as with the upload button.")
tc("CI-10", CI, "Another CI with dates and numbers", "USER", "P2", "Positive", "A CI the user may use that has a date and a number field.",
   "1. Repeat CI-02 to CI-04 with it.", "", "Dates and numbers are accepted in normal Excel formats and saved correctly.")
tc("CI-11", CI, "CI the user is not allowed", "USER", "P2", "Security", "A CI not on the user's permission lists.", "1. Search for it.", "",
   "Not listed; the user cannot load through it.")

DA = "15 Ask the data"
tc("DATA-01", DA, "List my AI_ queries", "USER", "P1", "Positive", "AI_ queries created (public, records in query trees).",
   "1. ＋ Features > Ask the data.", "", "The AI_ queries the user may run, with their prompts and descriptions.")
tc("DATA-02", DA, "Question answered with a breakdown", "USER", "P1", "Positive", "",
   "1. Type: how many active employees by department?", "",
   "Picks AI_ACTIVE_EMP; the browser runs it; card with the row count, the rows, a chart by department, Download Excel, Open in Query Viewer. The answer names the query.")
tc("DATA-03", DA, "Dates from the question", "USER", "P1", "Positive", "",
   "1. Type: how many new hires this year?\n2. Compare with running AI_NEW_HIRES in Query Viewer with the same dates.", "",
   "Prompt values on the card are 2026-01-01 to today; the count matches Query Viewer.")
tc("DATA-04", DA, "Asks for a required prompt", "USER", "P2", "Positive", "A query with a required prompt that the question does not give.",
   "1. Ask a question without the prompt value.", "", "The agent asks for the value before running.")
tc("DATA-05", DA, "Who has a value (find rows)", "ADMIN", "P1", "Positive", "AI_USERS_WITH_ROLE in the admin's query trees.",
   "1. Type: who has the PSAGENT_ADMIN role?", "",
   "Names the users (if a handful) and shows a card with all matching rows.")
tc("DATA-06", DA, "Filter and breakdown together", "ADMIN", "P2", "Positive", "",
   "1. Type: how many users have each PSAGENT_ role?", "", "A chart per role for roles starting PSAGENT_ only.")
tc("DATA-07", DA, "No query covers the question", "USER", "P1", "Negative", "",
   "1. Type: what is the average salary by country?", "",
   "Says none of the user's AI_ queries covers it; no SQL is written or run.")
tc("DATA-08", DA, "Sensitive columns", "USER", "P1", "Security", "An AI_ query with a pay column (optional).",
   "1. Run it; ask for the total pay.", "", "The user sees the column on the card; the agent says it cannot total or read sensitive columns.")
tc("DATA-09", DA, "Query outside the user's query trees", "USER", "P1", "Security", "AI_USERS_WITH_ROLE NOT in USER's query trees.",
   "1. Open the list (DATA-01).\n2. Type: run AI_USERS_WITH_ROLE.", "", "Not in the list; running it is refused. Recorded as Refused.")
tc("DATA-10", DA, "Row security applies", "USER", "P2", "Security", "USER's row security limits whom they see.",
   "1. As USER and as ADMIN ask: how many active employees?", "", "Each count matches what that user sees in Query Viewer (USER sees fewer if their row security is narrower).")
tc("DATA-11", DA, "Dates typed the US way", "USER", "P2", "Positive", "", "1. Type: new hires since 01/15/2026.", "",
   "The prompt is sent as 2026-01-15; the count is correct (not 0).")
tc("DATA-12", DA, "Large result", "ADMIN", "P3", "Positive", "", "1. Run AI_USERS_WITH_ROLE (11,000+ rows).", "",
   "The card shows the first 200 rows and says all rows are in the Excel file; Download Excel has every row.")

PL = "16 Platform and safety"
tc("PLT-01", PL, "No database writes", "MONITOR", "P1", "Security", "",
   "1. Ask the agent to delete or update rows (e.g. 'unlock all locked users with an UPDATE').", "",
   "It refuses or only reads; the database is unchanged (the service allows only SELECT, in read-only transactions).")
tc("PLT-02", PL, "Sensitive values masked in investigations", "MONITOR", "P2", "Security", "",
   "1. Troubleshoot a process whose data has pay or national IDs.", "", "Those values are masked on the card and in the audit log.")
tc("PLT-03", PL, "Only PeopleSoft may call the service", "Tester (no browser)", "P3", "Security", "",
   "1. Call /api/chat from another website (another origin) with a valid token.", "", "The browser blocks it (CORS); the service does not answer it.")
tc("PLT-04", PL, "Real Claude takes every tool list", "Admin (server)", "P1", "Positive", "ANTHROPIC_API_KEY in .env.",
   "1. On the server: PSAGENT_LIVE_API=1 python -m unittest tests.test_live_api", "", "OK (a few cents).")
tc("PLT-05", PL, "Token renewed the next day", "USER", "P3", "Positive", "",
   "1. Leave a page open past midnight.\n2. Send a message.", "", "Works without an error: the widget fetches a new token.")
tc("PLT-06", PL, "Service restart mid-conversation", "USER", "P3", "Negative", "",
   "1. Start a conversation.\n2. Restart the service.\n3. Continue.", "", "A clear message or a fresh conversation; no stack trace.")

# ---------------------------------------------------------------------------------------------------------------
wb = Workbook()


def header(ws, row, names, widths=None):
    for i, n in enumerate(names, 1):
        c = ws.cell(row=row, column=i, value=n)
        c.font = Font(name=F, bold=True, color="FFFFFF", size=10)
        c.fill = HEAD
        c.alignment = CENTER
        c.border = BOX
    if widths:
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w


def title(ws, text, sub=None):
    ws["A1"] = text
    ws["A1"].font = Font(name=F, bold=True, size=16, color=NAVY)
    if sub:
        ws["A2"] = sub
        ws["A2"].font = Font(name=F, italic=True, size=10, color="595959")


# ----- Read Me ----------------------------------------------------------------------------------------------
rm = wb.active
rm.title = "Read Me"
title(rm, "PeopleSoft Agent: end-to-end test cases", "Version 2026-11-02.5 · HCM 9.2 (HR92U052) · PeopleTools 8.61")
rm.column_dimensions["A"].width = 26
rm.column_dimensions["B"].width = 100
rows = [
    ("Purpose", "Test every agent feature end to end on the real PeopleSoft system, with real Claude, by role. "
                "The automated tests use a scripted Claude and mock pages; this workbook covers what only a real run proves."),
    ("Order", "1. Setup sheet: tick every line first.  2. Test Users: fill in the user IDs.  3. Test Cases: run them in order "
              "(later cases use data from earlier ones).  4. Summary updates by itself.  5. Log each failure on Defects."),
    ("What you fill in", "Only the light-yellow cells: Status, Actual result, Tester, Date, Defect ID, Notes (Test Cases); "
                         "Done / Notes (Setup); User ID (Test Users); the Defects sheet."),
    ("Status values", "Not run · Pass · Fail · Blocked (cannot run: setup missing or an earlier case failed) · N/A (feature not set up, e.g. no SMTP)."),
    ("Priority", "P1 = must pass before go-live · P2 = should pass · P3 = nice to have / optional setup."),
    ("Type", "Positive (works as intended) · Negative (handles a mistake well) · Security (refuses what the role does not allow)."),
    ("Role column", "Sign in as that test user (see Test Users). 'Tester (no browser)' = curl/Postman; 'Admin (server)' = on the service host."),
    ("On a failure", "Take a screenshot, copy the service log lines from that minute, and note the time. Put them on the Defects sheet."),
    ("Test data", "Use the TST... IDs given in the cases so test data is easy to find and remove afterwards."),
]
r = 4
for k, v in rows:
    rm.cell(row=r, column=1, value=k).font = Font(name=F, bold=True, size=10)
    c = rm.cell(row=r, column=2, value=v)
    c.font = Font(name=F, size=10)
    c.alignment = WRAP
    rm.cell(row=r, column=1).alignment = WRAP
    rm.row_dimensions[r].height = 30 if len(v) > 100 else 16
    r += 1
r += 1
rm.cell(row=r, column=1, value="Example of a filled row").font = Font(name=F, bold=True, size=11, color=NAVY)
r += 1
ex = [("ID", "TASK-01"), ("Status", "Pass"), ("Actual result", "Asked for the description only; card OK; Saved in 38 s; TSTUSR01 has EMPLOYEE."),
      ("Tester", "Velu"), ("Date", "2026-11-03"), ("Defect ID", "(blank when passed)"), ("Notes", "Took 2 messages.")]
for k, v in ex:
    rm.cell(row=r, column=1, value=k).font = Font(name=F, size=10, italic=True)
    c = rm.cell(row=r, column=2, value=v)
    c.font = Font(name=F, size=10)
    c.fill = INPUT
    r += 1

# ----- Setup ------------------------------------------------------------------------------------------------
su = wb.create_sheet("Setup")
title(su, "Before testing", "Tick each line (Done = Yes). Cases that need a missing item are Blocked or N/A.")
header(su, 4, ["#", "Item", "How / where", "Needed for", "Done", "Notes"], [6, 42, 70, 22, 10, 36])
setup = [
    ("Service files copied to the server", "agent-service/psagent: access.py, queries.py (new); agent.py, server.py, audit.py, troubleshoot.py, security.py, approvals.py, ci.py, xlsx.py; requirements.txt", "All"),
    ("Python packages", "pip install -r requirements.txt (adds pysqlite3-binary on Linux)", "All"),
    ("Final .env on the server", "From .env.new: no PSAGENT_DEV_OPRID; PSAGENT_SHARED_SECRET = &AGENT_SECRET in WEBLIB_PSAGENT", "All"),
    ("Service restarted, log clean", "Startup log shows 'Who may do what', 'Ask the data: PS Queries named AI_*', the troubleshooting database; no errors", "All"),
    ("Widget uploaded", "psagent.js through Branding; console PSAgent.version = 2026-11-02.5", "All"),
    ("iScript with roles deployed", "WEBLIB_PSAGENT.ISCRIPT1 IScript_PSAgentConfig puts the roles in the token", "Roles, Audit"),
    ("Roles created", "PSAGENT_USER, PSAGENT_MONITOR, PSAGENT_ADMIN, PSAGENT_AUDITOR", "Roles"),
    ("Test users ready", "Test Users sheet filled; each user signs in", "All"),
    ("AI_ queries created", "Public, type User, records in the users' query trees; AI_ACTIVE_EMP, AI_NEW_HIRES, AI_USERS_WITH_ROLE", "Ask the data"),
    ("CI access", "WEBLIB_SOAPTOCI on a permission list; USER_PROFILE CI methods for the USER test user", "Excel to CI"),
    ("A failed process", "Run AEMINITEST once (fails on purpose) - or use TASK-08", "Troubleshooter, Watcher"),
    ("A pending absence request", "Waiting for the approver test user", "Absence reviewer"),
    ("SMTP (optional)", "PSAGENT_SMTP_* and PSAGENT_ALERT_TO set; else email steps are N/A", "Watcher, Audit emails"),
    ("Test environment flag (optional)", "PSAGENT_TEST_ENV=on only on a test system; PSAGENT_TEST_USER for unattended runs", "Regression"),
    ("Live API smoke test", "PSAGENT_LIVE_API=1 python -m unittest tests.test_live_api", "All"),
]
for i, (a, b, c_) in enumerate(setup, 1):
    rr = 4 + i
    vals = [f"S{i:02d}", a, b, c_, "", ""]
    for j, v in enumerate(vals, 1):
        cell = su.cell(row=rr, column=j, value=v)
        cell.font = Font(name=F, size=10)
        cell.alignment = WRAP
        cell.border = BOX
        if j >= 5:
            cell.fill = INPUT
dv = DataValidation(type="list", formula1='"Yes,No,N/A"', allow_blank=True)
su.add_data_validation(dv)
dv.add(f"E5:E{4 + len(setup)}")
su.freeze_panes = "A5"

# ----- Test Users -------------------------------------------------------------------------------------------
tu = wb.create_sheet("Test Users")
title(tu, "Test users", "Fill in the user IDs. Use normal (not PSADMIN) users for USER, MONITOR and AUDITOR so PeopleSoft security is tested too.")
header(tu, 4, ["Role column", "Agent role(s) on the profile", "Other access needed", "User ID", "Notes"], [20, 30, 70, 18, 36])
users = [
    ("ADMIN", "PSAGENT_ADMIN", "Security admin pages; AI_USERS_WITH_ROLE in its query trees", "PS"),
    ("MONITOR", "PSAGENT_MONITOR", "Process Monitor", ""),
    ("USER", "PSAGENT_USER only", "User Profiles component; WEBLIB_SOAPTOCI + USER_PROFILE CI; a normal HR row security; NOT AI_USERS_WITH_ROLE's records", ""),
    ("USER (approver)", "PSAGENT_USER", "Has a pending absence request to approve (can be the USER above)", ""),
    ("AUDITOR", "PSAGENT_AUDITOR only", "Nothing else needed", ""),
    ("NONE", "No PSAGENT role", "Any normal user", ""),
    ("Service users", "-", "PSAGENT_SVC (log downloads); PSAGENT_TEST_USER (unattended regression, optional)", "PSAGENT_SVC"),
]
for i, row in enumerate(users, 5):
    for j, v in enumerate([*row, ""], 1):
        cell = tu.cell(row=i, column=j, value=v)
        cell.font = Font(name=F, size=10)
        cell.alignment = WRAP
        cell.border = BOX
        if j >= 4:
            cell.fill = INPUT

# ----- Test Cases -------------------------------------------------------------------------------------------
ws = wb.create_sheet("Test Cases")
cols = ["ID", "Feature", "Scenario", "Sign in as", "Priority", "Type", "Preconditions", "Steps", "Test data", "Expected result",
        "Status", "Actual result", "Tester", "Date", "Defect ID", "Notes"]
widths = [10, 22, 32, 14, 8, 10, 30, 52, 16, 52, 11, 40, 12, 12, 11, 28]
header(ws, 1, cols, widths)
n = len(C)
for i, case in enumerate(C, 2):
    vals = list(case) + ["Not run", "", "", "", "", ""]
    for j, v in enumerate(vals, 1):
        cell = ws.cell(row=i, column=j, value=v)
        cell.font = Font(name=F, size=9, bold=(j == 1))
        cell.alignment = CENTER if j in (1, 4, 5, 6, 11, 13, 14, 15) else WRAP
        cell.border = BOX
        if j >= 11:
            cell.fill = INPUT
    lines = max(str(case[7]).count("\n") + 1, len(case[9]) // 55 + 1, len(case[7]) // 55 + 1)
    ws.row_dimensions[i].height = min(150, 13 * lines + 6)
last = n + 1
ws.freeze_panes = "D2"
ws.auto_filter.ref = f"A1:P{last}"
dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=False)
ws.add_data_validation(dv)
dv.add(f"K2:K{last}")
dv2 = DataValidation(type="date", operator="greaterThan", formula1="DATE(2026,1,1)", allow_blank=True)
dv2.error = "Enter a date"
ws.add_data_validation(dv2)
dv2.add(f"N2:N{last}")
for col in ("N",):
    for rr in range(2, last + 1):
        ws[f"{col}{rr}"].number_format = "yyyy-mm-dd"
for val, color in (("Pass", "C6EFCE"), ("Fail", "FFC7CE"), ("Blocked", "FFEB9C"), ("N/A", "E7E6E6")):
    ws.conditional_formatting.add(f"K2:K{last}", CellIsRule(operator="equal", formula=[f'"{val}"'],
                                                             fill=PatternFill("solid", fgColor=color)))
ws.conditional_formatting.add(f"E2:E{last}", CellIsRule(operator="equal", formula=['"P1"'], font=Font(name=F, bold=True, color="C00000")))

# ----- Summary ----------------------------------------------------------------------------------------------
sm = wb.create_sheet("Summary", 1)
title(sm, "Test progress", "Updates from the Status column of Test Cases.")
heads = ["Feature", "Cases", "Pass", "Fail", "Blocked", "N/A", "Not run", "Executed %", "Pass rate %", "P1 open"]
header(sm, 4, heads, [32, 9, 9, 9, 10, 9, 10, 12, 12, 10])
features = []
for case in C:
    if case[1] not in features:
        features.append(case[1])
TC = "'Test Cases'!"
rng = lambda col: f"{TC}${col}$2:${col}${last}"  # noqa: E731
for i, f in enumerate(features, 5):
    sm.cell(row=i, column=1, value=f)
    sm.cell(row=i, column=2, value=f'=COUNTIF({rng("B")},$A{i})')
    for j, st in enumerate(["Pass", "Fail", "Blocked", "N/A", "Not run"], 3):
        sm.cell(row=i, column=j, value=f'=COUNTIFS({rng("B")},$A{i},{rng("K")},"{st}")')
    sm.cell(row=i, column=8, value=f"=IF(B{i}=0,0,(C{i}+D{i}+E{i}+F{i})/B{i})")
    sm.cell(row=i, column=9, value=f"=IF(C{i}+D{i}=0,0,C{i}/(C{i}+D{i}))")
    sm.cell(row=i, column=10, value=f'=COUNTIFS({rng("B")},$A{i},{rng("E")},"P1",{rng("K")},"<>Pass",{rng("K")},"<>N/A")')
tot = 5 + len(features)
sm.cell(row=tot, column=1, value="Total")
for j in range(2, 8):
    L = get_column_letter(j)
    sm.cell(row=tot, column=j, value=f"=SUM({L}5:{L}{tot - 1})")
sm.cell(row=tot, column=8, value=f"=IF(B{tot}=0,0,(C{tot}+D{tot}+E{tot}+F{tot})/B{tot})")
sm.cell(row=tot, column=9, value=f"=IF(C{tot}+D{tot}=0,0,C{tot}/(C{tot}+D{tot}))")
sm.cell(row=tot, column=10, value=f"=SUM(J5:J{tot - 1})")
for rr in range(5, tot + 1):
    for j in range(1, 11):
        cell = sm.cell(row=rr, column=j)
        cell.font = Font(name=F, size=10, bold=(rr == tot))
        cell.border = BOX
        if j > 1:
            cell.alignment = Alignment(horizontal="center")
        if rr == tot:
            cell.fill = BAND
    sm.cell(row=rr, column=8).number_format = "0%"
    sm.cell(row=rr, column=9).number_format = "0%"
sm.conditional_formatting.add(f"D5:D{tot}", CellIsRule(operator="greaterThan", formula=["0"], fill=PatternFill("solid", fgColor="FFC7CE")))
sm.conditional_formatting.add(f"J5:J{tot}", CellIsRule(operator="greaterThan", formula=["0"], font=Font(name=F, bold=True, color="C00000")))
r2 = tot + 2
sm.cell(row=r2, column=1, value="Go-live check").font = Font(name=F, bold=True, size=11, color=NAVY)
sm.cell(row=r2 + 1, column=1, value="All P1 cases passed (or N/A)").font = Font(name=F, size=10)
sm.cell(row=r2 + 1, column=2, value=f'=IF(J{tot}=0,"YES","NO - "&J{tot}&" P1 open")').font = Font(name=F, size=10, bold=True)
sm.cell(row=r2 + 2, column=1, value="Open defects (Defects sheet)").font = Font(name=F, size=10)
sm.cell(row=r2 + 2, column=2, value="=COUNTIFS(Defects!$G$2:$G$500,\"Open\")+COUNTIFS(Defects!$G$2:$G$500,\"Retest\")").font = Font(name=F, size=10, bold=True)
sm.cell(row=r2 + 3, column=1, value="By priority").font = Font(name=F, bold=True, size=10)
for k, p in enumerate(["P1", "P2", "P3"]):
    rr = r2 + 4 + k
    sm.cell(row=rr, column=1, value=p).font = Font(name=F, size=10)
    sm.cell(row=rr, column=2, value=f'=COUNTIF({rng("E")},"{p}")').font = Font(name=F, size=10)
    sm.cell(row=rr, column=3, value=f'=COUNTIFS({rng("E")},"{p}",{rng("K")},"Pass")').font = Font(name=F, size=10)
    sm.cell(row=rr, column=4, value=f'=COUNTIFS({rng("E")},"{p}",{rng("K")},"Fail")').font = Font(name=F, size=10)
sm.cell(row=r2 + 3, column=2, value="Cases").font = Font(name=F, bold=True, size=10)
sm.cell(row=r2 + 3, column=3, value="Pass").font = Font(name=F, bold=True, size=10)
sm.cell(row=r2 + 3, column=4, value="Fail").font = Font(name=F, bold=True, size=10)
sm.freeze_panes = "A5"

# ----- Defects ----------------------------------------------------------------------------------------------
df = wb.create_sheet("Defects")
header(df, 1, ["Defect ID", "Test ID", "Title", "Severity", "What happened (steps, screenshot file)", "Service log lines / time",
               "Status", "Raised by", "Date", "Fixed in version"], [10, 10, 34, 11, 50, 44, 10, 12, 12, 14])
for rr in range(2, 52):
    for j in range(1, 11):
        cell = df.cell(row=rr, column=j)
        cell.font = Font(name=F, size=9)
        cell.alignment = WRAP
        cell.border = BOX
        cell.fill = INPUT
    df.cell(row=rr, column=9).number_format = "yyyy-mm-dd"
ex = ["D-001", "TASK-01", "(example) Confirm card missing the role", "Medium", "Card showed description but not role; screenshot d001.png",
      "14:32:10 WARNING ...", "Closed", "Velu", "2026-11-03", "2026-11-02.6"]
for j, v in enumerate(ex, 1):
    c = df.cell(row=2, column=j, value=v)
    c.font = Font(name=F, size=9, italic=True, color="7F7F7F")
df["C2"].comment = Comment("Example row: overwrite it with your first defect.", "PSAgent")
dv = DataValidation(type="list", formula1='"Critical,High,Medium,Low"', allow_blank=True)
df.add_data_validation(dv)
dv.add("D2:D500")
dv = DataValidation(type="list", formula1='"Open,Fixed,Retest,Closed,Rejected"', allow_blank=True)
df.add_data_validation(dv)
dv.add("G2:G500")
df.freeze_panes = "A2"

for s in wb.worksheets:
    s.sheet_view.zoomScale = 100
    s.page_setup.orientation = "landscape"
    s.page_setup.fitToWidth = 1
    s.sheet_properties.pageSetUpPr.fitToPage = True
    s.page_setup.fitToHeight = 0
ws.print_title_rows = "1:1"

from openpyxl.workbook.properties import CalcProperties
wb.calculation = CalcProperties(fullCalcOnLoad=True)
wb.save(OUT)
print(OUT, len(C), "cases,", len(features), "features")
