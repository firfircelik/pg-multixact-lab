#!/bin/sh
# Detached chain: danger zone (limit_ff) -> cross-DB -> mitigations; backup after each step. Resumable (cells skipped if done) except limit_ff, which skips finished versions/reps via marker.
cd "$(dirname "$0")/.." && mkdir -p logs/controls
P=install/venv/bin/python
st(){ echo "$(date -Is) $*" >> logs/chain2.log; }
[ -f results/.limit_done ] || { st "limit_ff start"; $P scripts/limit_ff.py 18.7,19rc2 3 >> logs/limit_ff.log 2>&1 && touch results/.limit_done; st "limit_ff exit $?"; scripts/backup.sh >/dev/null; }
st "crossdb start"; $P scripts/grid_crossdb.py 18.7,19rc2 3 >> logs/grid_crossdb.log 2>&1; st "crossdb exit $?"; scripts/backup.sh >/dev/null
st "mitig start"; $P scripts/grid_mitig.py 18.7,19rc2 >> logs/grid_mitig.log 2>&1; st "mitig exit $?"; scripts/backup.sh >/dev/null
st "chain2 done"
