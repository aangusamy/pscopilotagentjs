#!/bin/bash
# Restart the agent service on the server: stop uvicorn, start it again in the background, check it answers.
# Run in a login shell so PeopleSoft's environment (psconfig.sh, its python) is set:  bash -l ~/agent-service/restart.sh
cd "$(dirname "$0")" || exit 1
for p in $(pgrep -f "uvicorn --factory psagent[.]server"); do kill "$p"; done
sleep 2
setsid nohup python -m uvicorn --factory psagent.server:app_from_env --host 0.0.0.0 --port 8080 --workers 1 \
  >> uvicorn.log 2>&1 < /dev/null &
for _ in $(seq 20); do
  sleep 1
  curl -s -m 2 -o /dev/null localhost:8080/health && break
done
grep -E "Started server|Widget for loaders|ERROR|Traceback" uvicorn.log | tail -3
curl -s -m 5 -o /dev/null -w "health %{http_code}\n" localhost:8080/health
curl -s -m 5 localhost:8080/psagent.js | grep -o "VERSION = '[^']*'" | head -1
