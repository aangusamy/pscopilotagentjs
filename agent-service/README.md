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

## Run it

```bash
cd agent-service
pip install -r requirements.txt
cp .env.example .env                      # then fill in the API key, secret and origin
uvicorn --factory psagent.server:app_from_env --host 0.0.0.0 --port 8080 --workers 1
```

Settings are read from `agent-service/.env` (see `.env.example`); real
environment variables take precedence.

| Variable | Default | |
|---|---|---|
| `PSAGENT_MODEL` | `claude-opus-5-5` | Claude model |
| `PSAGENT_EFFORT` | `low` | `low`…`max`. Low keeps chat replies fast. Raise it if extraction misses things. |
| `PSAGENT_TASKS_DIR` | `./tasks` | Where task YAML files live. Recorded tasks are saved here too, so it must be writable. |
| `PSAGENT_TEACHERS` | unset | OPRIDs allowed to record tasks, comma separated (`*` = everyone). Unset = recording off. |
| `PSAGENT_DEV_OPRID` | unset | Local testing only: requests without a valid token run as this OPRID |

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
   `PSAGENT_SHARED_SECRET`. It returns a day-scoped token
   `OPRID.YYYYMMDD.HashSHA256(secret|OPRID|YYYYMMDD)`, and the secret never
   reaches the browser.
3. **Security.** Open PeopleTools > Security > Permission Lists, go to the Web
   Libraries tab, add `WEBLIB_PSAGENT` and give `IScript_PSAgentConfig` Full
   Access. Do this in the permission lists of the users who should see the
   agent (e.g. one assigned through a "PS Agent User" role). Users without it
   get no widget. Recording is further limited to `PSAGENT_TEACHERS`.
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

1. Click **Record** in the chat header (only shown to `PSAGENT_TEACHERS`), or
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
cd agent-service && python3 -m unittest discover -s tests -t tests       # 60 unit tests, scripted LLM
cd .. && node tests/runner/run_browser_tests.mjs                          # 8 browser tests (Playwright)
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
