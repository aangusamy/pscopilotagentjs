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
| `PSAGENT_TASKS_DIR` | `./tasks` | Where task YAML files live |
| `PSAGENT_DEV_OPRID` | unset | Local testing only: requests without a valid token run as this OPRID |

Sessions are held in memory, so run **one worker**. Move `SessionStore` to Redis
before scaling out.

Requests use the server-side refusal fallback (`fallbacks: "default"`). If
Claude's safety classifier declines a request, the API retries it on
Anthropic's recommended fallback model.

## PeopleSoft setup

1. **iScript**: create record `WEBLIB_PSAGENT`, add the code in
   `../psagent_iscript` to `ISCRIPT1.FieldFormula`, set `&AGENT_URL` and
   `&AGENT_SECRET`, and grant the WEBLIB in the users' permission lists. It
   returns a day-scoped token `OPRID.YYYYMMDD.HashSHA256(secret|OPRID|YYYYMMDD)`.
   The secret never reaches the browser. HashSHA256 needs PeopleTools 8.54+.
2. **Widget**: paste `../psagent.js` at the bottom of `PTNUI_MENU_JS`. Update
   `CONFIG_ISCRIPT_URL` if your site, portal or node differ.
3. **Network**: the browser calls the service directly, so it must be HTTPS and
   listed in `PSAGENT_ALLOWED_ORIGINS`. The cleanest setup is to reverse-proxy it
   under the PeopleSoft host (e.g. `/psagent/`), so there's no CORS at all.

The agent works in a frame on the same origin
(`/psc/<site>/<portal>/<node>/c/MENU.COMPONENT.GBL`). If your web server sends
`X-Frame-Options: DENY` for `/psc/`, allow `SAMEORIGIN`.

## Adding a task (voucher, requisition, …)

A task is one YAML file in `tasks/`: the **fields** the agent must collect, and
the **playbook** of steps a human performs. No code or JS changes are needed,
and the agent picks new files up on restart.

Easiest way to write the playbook is to **do the task once while the widget records it**:

```js
// browser console on any PeopleSoft page with the widget loaded
PSAgent.teach('MAINTAIN_SECURITY.USERMAINT.GBL', {Action: 'A'})
```

Complete the task by hand in the workspace and click **Finish teaching**. You
get the recorded steps as JSON, which is valid YAML. Paste them under
`playbook:` and replace the values you typed with `{{field}}` placeholders.
`PSAgent.inspect()` lists every field ID and label on the page in the workspace.

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
grid `column` header. Any step can have `when: <field>` (skipped if that field is
empty) and `say:` (progress text). Use `{{secret:name}}` for `sensitive` fields.
`load_tasks` checks every placeholder and `when` against the field list, so a
typo fails at startup.

For a **voucher from an invoice**, the same pattern applies: an `invoice` upload
plus Claude reading it can fill vendor, invoice number, date and lines, and the
playbook drives `VCHR_EXPRESS`. That needs file upload in the widget, which is
the natural next step.

## Tests

```bash
cd agent-service && python3 -m unittest discover -s tests -t tests       # 26 unit tests, scripted LLM
cd .. && node tests/runner/run_browser_tests.mjs                          # 5 browser tests (Playwright)
```

The browser tests run the real widget and the real service (with a scripted
LLM) against `tests/runner/mock_usermaint.html`. That file is a small imitation
of the User Profiles component, not real PeopleSoft HTML. It proves the runner
mechanics: AJAX waits, a full reload on Add, label/column fallbacks, grid rows,
message-box detection, and that the password never reaches the service. It does
not prove that the field IDs match your release. Run `PSAgent.inspect()` once on
the real page for that.
