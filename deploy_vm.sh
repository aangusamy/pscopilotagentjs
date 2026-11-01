#!/bin/bash
# Copy the agent service and psagent.js from this checkout to the server, then restart it.
# Run from Git Bash on Windows (uses PuTTY's plink and pscp):  ./deploy_vm.sh
#
#   Copies    agent-service/psagent, requirements.txt, README.md, .env.example, restart.sh, psagent.js
#   Adds      new files in tasks/ and runbooks/, never overwriting (the agent saves users' work there)
#   Never     touches .env or what the service writes (audit, ci_loads, regression, monitors, runs)
#   Keeps     a backup of the server folder first (the last 3)
#
# Settings (environment variables), with this project's defaults:
VM=${PSAGENT_VM:-psadm2@192.168.56.103}
KEY=${PSAGENT_VM_KEY:-$HOME/.ssh/psagent_vm.ppk}
HOSTKEY=${PSAGENT_VM_HOSTKEY:-SHA256:sng1jVV6sN2RDx4tT20tVCQDmV0mnCal+mQKgce128Y}
DIR=${PSAGENT_VM_DIR:-agent-service}            # on the server, relative to the user's home
PUTTY=${PUTTY:-/c/Program Files/PuTTY}
set -euo pipefail
cd "$(dirname "$0")"
plink() { "$PUTTY/plink.exe" -batch -hostkey "$HOSTKEY" -i "$KEY" "$VM" "$@"; }
pscp() { "$PUTTY/pscp.exe" -batch -q -hostkey "$HOSTKEY" -i "$KEY" "$@"; }

echo "== Unit tests"
(cd agent-service && python -m unittest discover -s tests -q 2>&1 | tail -1) | grep -q "^OK" || { echo "unit tests fail: not deploying"; exit 1; }

echo "== Package"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/code" "$stage/data"
git ls-files -z agent-service/psagent agent-service/requirements.txt agent-service/README.md agent-service/.env.example \
  agent-service/restart.sh | xargs -0 -I{} cp --parents {} "$stage/code/"
cp psagent.js "$stage/code/agent-service/"
git ls-files -z agent-service/tasks agent-service/runbooks | xargs -0 -I{} cp --parents {} "$stage/data/"
tar -C "$stage/code/agent-service" -czf "$stage/code.tgz" .
tar -C "$stage/data/agent-service" -czf "$stage/data.tgz" .
pscp "$stage/code.tgz" "$stage/data.tgz" "$VM:"

echo "== Install on $VM:~/$DIR"
plink "bash -s" <<EOF
set -e
cd ~
b=$DIR.bak-\$(date +%Y%m%d-%H%M%S)
cp -a $DIR "\$b" 2>/dev/null || true
echo "backup \$b"
ls -dt $DIR.bak-* | tail -n +4 | xargs -r rm -rf
rm -rf deploy_stage && mkdir -p deploy_stage/code deploy_stage/data
tar -C deploy_stage/code -xzf code.tgz && tar -C deploy_stage/data -xzf data.tgz && rm -f code.tgz data.tgz
# mv, not cp: it replaces files another user (root) left there, as long as the folder is ours
(cd deploy_stage/code && find . -type f) | while read -r f; do
  mkdir -p "$DIR/\$(dirname "\$f")"; mv -f "deploy_stage/code/\$f" "$DIR/\$f"
done
(cd deploy_stage/data && find . -type f) | while read -r f; do
  if [ -e "$DIR/\$f" ]; then
    cmp -s "deploy_stage/data/\$f" "$DIR/\$f" || echo "kept the server's \$f (differs from git)"
  else
    mkdir -p "$DIR/\$(dirname "\$f")"; mv "deploy_stage/data/\$f" "$DIR/\$f"; echo "added \$f"
  fi
done
rm -rf deploy_stage
sed -i 's/\r\$//' $DIR/restart.sh
bash -l $DIR/restart.sh 2>&1 | grep -v psconfig
EOF
