"""Reclamation grid: version x arm x rep, fresh cluster per cell.
Workload per cell: hot-row episodes k=32, E=4000 in database 'app'
(theory: 124,000 multixacts, 2,108,000 members)."""
import json, os, random, subprocess, sys, time, itertools
from lab import Cluster, ROOT, SOCK, env_info
from staircase import setup, episodes

SEED = 20261119 + 7
VERS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["18.7", "19rc2"]
ARMS = ["none", "vacuum_plain_app", "vacuum_freeze_app", "vacuumdb_all_freeze",
        "vacuumdb_all_freeze_blocked", "autovac", "autovac_blocked"]
K, E = 32, 4000
OUT = os.path.join(ROOT, "results", "r_reclaim.jsonl")

def slim(s):
    return {k: s[k] for k in ["next_multi","next_offset","oldest_multi","oldest_multi_db","members_bytes","members_files",
                              "offsets_bytes","offsets_files","datminmxid","t"] + (["stats"] if "stats" in s else [])}

def poll(cl, cn, until, timeout, step=2):
    t0 = time.time(); trace = []
    while True:
        s = cl.snapshot(cn); trace.append({"dt": round(time.time()-t0,1), "oldest_multi": s["oldest_multi"], "members_bytes": s["members_bytes"]})
        if until(s) or time.time() - t0 > timeout: return s, time.time() - t0, trace
        time.sleep(step)

def run_cell(v, arm, rep, order):
    auto = arm.startswith("autovac")
    extra = {"autovacuum": "on", "autovacuum_naptime": "'1s'", "autovacuum_multixact_freeze_max_age": 10000} if auto else {}
    cl = Cluster(v, "rc", extra).init().start()
    rec = dict(cell=f"r-{v}-{arm}-r{rep}", v=v, arm=arm, rep=rep, order=order, k=K, episodes=E)
    try:
        with cl.conn() as c0:
            c0.execute("create database app")
        with cl.conn("app") as cn:
            setup(cn, E + 10)
            base = cl.snapshot(cn); rec["base"] = slim(base)
            blocker = None
            if arm.endswith("_blocked"):
                blocker = cl.conn("app", autocommit=False)
                blocker.execute("select id from parent where id = %s for share", (E + 5,))   # idle-in-transaction locker
            rec["workload_secs"] = episodes(cl, K, E, "for_share", db="app")
            peak = cl.snapshot(cn); rec["peak"] = slim(peak)
            t0 = time.time()
            env = dict(os.environ, PGHOST=SOCK, PGPORT=str(cl.port), PGUSER="lab")
            if arm == "vacuum_plain_app": cn.execute("vacuum parent")
            elif arm == "vacuum_freeze_app": cn.execute("vacuum (freeze) parent")
            elif arm.startswith("vacuumdb_all_freeze"):
                subprocess.run([cl.bin("vacuumdb"), "--all", "--freeze", "-q"], check=True, env=env, capture_output=True)
            rec["action_secs"] = time.time() - t0
            if auto:
                after, waited, trace = poll(cl, cn, lambda s: s["oldest_multi"] >= peak["next_multi"], 180 if arm == "autovac" else 120)
                rec["wait_secs"], rec["trace"] = waited, trace
            else:
                after = cl.snapshot(cn)
            rec["after"] = slim(after)
            if blocker is not None:
                blocker.commit(); blocker.close()
                if auto:
                    rel, waited, trace = poll(cl, cn, lambda s: s["oldest_multi"] >= peak["next_multi"], 180)
                    rec["release_wait_secs"], rec["release_trace"] = waited, trace
                else:
                    subprocess.run([cl.bin("vacuumdb"), "--all", "--freeze", "-q"], check=True, env=env, capture_output=True)
                    rel = cl.snapshot(cn)
                rec["released"] = slim(rel)
        # count autovacuum lines in server log
        log = open(cl.log).read()
        rec["autovac_log_lines"] = sum(1 for l in log.splitlines() if "automatic" in l and "vacuum" in l)
        rec["autovac_template0"] = sum(1 for l in log.splitlines() if "automatic" in l and "template0" in l)
        rec["warnings"] = [l for l in log.splitlines() if "WARNING" in l][:5]
        rec["ts"] = time.time()
        return rec
    finally:
        cl.destroy()

if __name__ == "__main__":
    done = {json.loads(l)["cell"] for l in open(OUT)} if os.path.exists(OUT) else set()
    cells = list(itertools.product(VERS, ARMS, [1, 2, 3])); random.Random(SEED).shuffle(cells)
    for i, (v, arm, rep) in enumerate(cells):
        if f"r-{v}-{arm}-r{rep}" in done: continue
        rec = run_cell(v, arm, rep, i)
        open(OUT, "a").write(json.dumps(rec) + "\n")
        a = rec["after"]; p = rec["peak"]
        print(rec["cell"], "peak_mem_bytes", p["members_bytes"], "after_mem_bytes", a["members_bytes"], "oldest", p["oldest_multi"], "->", a["oldest_multi"],
              "released" if "released" in rec else "", rec.get("released", {}).get("members_bytes", ""), round(rec.get("wait_secs", rec["action_secs"]),1), flush=True)
