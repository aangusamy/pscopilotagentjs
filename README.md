# pscopilotagentjs
PeopleSoft Agent ("Pia"): a chat widget in PeopleSoft that does tasks on screen, troubleshoots, checks security,
loads Excel through Component Interfaces and answers questions from PS Queries, with a Python service (Claude) behind it.

## Files

| File | |
|---|---|
| `psagent.js` | The widget. The service serves it at `GET /psagent.js` |
| `psagent_loader.js` | What goes in PeopleSoft instead of the whole widget: classic pages in HTML definition `PS_COPILOT_HTML`, Fluid at the end of `PTNUI_MENU_JS`. It fetches `psagent.js` from the service, so a new widget needs no App Designer change |
| `psagent_iscript` | `WEBLIB_PSAGENT` iScript: gives the widget the service address and a signed token with the user's agent roles |
| `agent-service/` | The Python service. Setup, settings, tasks and tests: [agent-service/README.md](agent-service/README.md) |
| `deploy_vm.sh` | Copies the service and widget to the server and restarts it (keeps the server's `.env` and data) |
| `docs/` | Technical paper (PDF), end-to-end test workbook, sample Excel for the CI load tests, and the scripts that build them |
| `demo/` | A sample new joiner access request (HTML and PDF) |
| `tests/` | Browser tests of the widget against mock PeopleSoft pages (`tests/runner`), and an Application Engine demo |
| `FEATURES.md` | What the agent can do, and future features |
| `IMPLEMENTED_FEATURES.md` | Each feature with what proves it works |

## Deploying a change

```
./deploy_vm.sh          # from Git Bash: unit tests, copy, restart, health check
```
Then press F5 in PeopleSoft: the loader fetches the new widget.
