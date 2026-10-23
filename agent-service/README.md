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
export ANTHROPIC_API_KEY=...              # Claude API key
export PSAGENT_SHARED_SECRET=...          # same as &AGENT_SECRET in the iScript
export PSAGENT_ALLOWED_ORIGINS=https://your-peoplesoft-host
uvicorn --factory psagent.server:app_from_env --host 0.0.0.0 --port 8080 --workers 1
```

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
2. Name the task and pick the page. The page you are on is filled in, or you can
   paste its URL or `MENU.COMPONENT.GBL`. Choose Add or Find mode.
3. The page opens in the workspace. **Do the task by hand, including Save**, and
   click **Finish recording**.
   * Every value you type, pick or tick is recorded. That includes values a
     lookup fills in.
   * Password fields are recorded as "a password goes here", never the value.
   * To make a list (roles, requisition lines), fill **at least two rows**.
4. On the review card, Claude suggests how to treat each recorded value. Change
   any of them, then click **Save task**:
   * **Ask every time**: for IDs, names, amounts and dates.
   * **Ask, can be blank**: for values that are only sometimes needed.
   * **Ask, default = recorded**: for values that are usually the same (business
     unit, symbolic ID).
   * **Always the recorded value**: for values that are part of the procedure
     (e.g. ID Type = Employee).
5. The task is saved as `tasks/<task_id>.yaml` and is live immediately, including
   in open conversations. Ask for it in your own words, like any other task.

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
| `navigate` | `query` | Opens the task's `component` in the workspace (`{Action: A}` = Add mode) |
| `set` | `field`, `value` | Types into a text box, picks a dropdown option (value or visible text) or ticks a checkbox, then waits for PeopleSoft |
| `click` | `button` | Clicks a button/link (`#ICSearch`, `#ICSave`, …) and waits for PeopleSoft |
| `tab` | `label` | Opens a page tab by its visible name |
| `grid` | `field`, `values` | Fills one grid row per value, pressing the row's **+** button to add rows |
| `wait` | `field` | Waits until a field appears |
| `expect_saved` | | Succeeds only if PeopleSoft shows **Saved** and no message |

Fields are found by `id` first, then by `label` (the `<label for>` text), then by
grid `column` header. Any step can have `when: <field>` or `when: [a, b]` (skipped
unless one of them has a value) and `say:` (progress text). Use `{{secret:name}}` for `sensitive` fields.
`load_tasks` checks every placeholder and `when` against the field list, so a
typo fails at startup.

For a **voucher from an invoice**: record the voucher entry once on
`VCHR_EXPRESS`. The missing piece is an invoice upload in the widget so Claude
can read the vendor, invoice number, date and lines from the document instead
of asking for them. That is the natural next step.

## Tests

```bash
cd agent-service && python3 -m unittest discover -s tests -t tests       # 44 unit tests, scripted LLM
cd .. && node tests/runner/run_browser_tests.mjs                          # 6 browser tests (Playwright)
```

The browser tests run the real widget and the real service (with a scripted
LLM) against `tests/runner/mock_usermaint.html`. That file is a small imitation
of the User Profiles component, not real PeopleSoft HTML. It proves the runner
mechanics: AJAX waits, a full reload on Add, label/column fallbacks, grid rows,
message-box detection, and that the password never reaches the service. One
test records a user creation by hand, saves it as a task, and has the agent
replay it for a different user. It does
not prove that the field IDs match your release. Run `PSAgent.inspect()` once on
the real page for that.
