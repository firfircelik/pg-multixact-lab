#!/bin/sh
cd "$(dirname "$0")/.." && mkdir -p logs/controls
st(){ echo "$(date -Is) $*" >> logs/chain3.log; }
while pgrep -f "scripts/chain2.sh" >/dev/null; do sleep 5; done
st "upgrade start"; install/venv/bin/python scripts/upgrade_rewrite.py >> logs/upgrade.log 2>&1; st "upgrade exit $?"; scripts/backup.sh >/dev/null; st "chain3 done"
