"""Rerun of the two 19.0 reclamation cells whose poll trace had a box-suspension gap
(r-19.0-autovac-r3: 462 s gap; r-19.0-autovac_blocked-r1: 425 s gap). Original lines are kept
in results/r_reclaim.jsonl and flagged; reruns go to results/controls/r19_rerun.jsonl."""
import sys, json, os, time
sys.argv = [sys.argv[0], "19.0"]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grid_reclaim as g
from lab import ROOT
OUT = os.path.join(ROOT, "results", "controls", "r19_rerun.jsonl")
for arm, rep in [("autovac", 3), ("autovac_blocked", 1)]:
    up0 = float(open("/proc/uptime").read().split()[0]); t0 = time.time()
    rec = g.run_cell("19.0", arm, rep, -1)
    up1 = float(open("/proc/uptime").read().split()[0]); wall = time.time() - t0
    rec["cell"] += "-rerun"; rec["wall_secs"] = wall; rec["uptime_secs"] = up1 - up0
    tr = rec.get("trace", []) + rec.get("release_trace", [])
    rec["max_poll_gap"] = max([b["dt"] - a["dt"] for a, b in zip(tr, tr[1:])] or [0])
    open(OUT, "a").write(json.dumps(rec) + "\n")
    print(rec["cell"], rec["after"]["oldest_multi"], rec["after"]["members_bytes"], rec.get("released", {}).get("members_bytes"), round(rec.get("wait_secs", 0), 1), round(rec.get("release_wait_secs", 0) or 0, 1), "maxgap", rec["max_poll_gap"], flush=True)
