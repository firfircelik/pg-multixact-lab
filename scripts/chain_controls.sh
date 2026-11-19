#!/bin/sh
cd "$(dirname "$0")/.." && mkdir -p logs/controls
echo "$(date -Is) controls start" >> logs/chain_controls.log
install/venv/bin/python scripts/controls.py "$@" >> logs/controls/controls.out 2>&1
echo "$(date -Is) controls exit $?" >> logs/chain_controls.log
scripts/backup.sh >/dev/null; echo "$(date -Is) backup done" >> logs/chain_controls.log
