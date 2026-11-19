"""Cross-database pinning + idle_in_transaction_session_timeout mitigation.
Workload in db 'app' (k=32, 4000 episodes). A second session in db 'other'
holds one of the arms open. Autovacuum on (naptime 1s,
autovacuum_multixact_freeze_max_age=10000 so template0 is also processed).
Then poll 90 s and record whether member space is reclaimed; release; poll 90 s."""
import json, os, random, sys, time, itertools
import psycopg
from lab import Cluster, ROOT
from staircase import setup, episodes

ARMS = ["control", "other_db_locker", "other_db_xid_only", "other_db_rr_snapshot", "other_db_locker_iits10s"]
K, E, WAIT = 32, 4000, 90
OUT = os.path.join(ROOT, "results", "x_crossdb.jsonl")

def poll(cl, cn, secs, step=3):
    t0 = time.time(); tr = []
    while time.time() - t0 < secs:
        s = cl.snapshot(cn); tr.append([round(time.time()-t0,1), s["oldest_multi"], s["members_bytes"]]); time.sleep(step)
    return cl.snapshot(cn), tr

def run_cell(v, arm, rep, order):
    extra = {"autovacuum": "on", "autovacuum_naptime": "'1s'", "autovacuum_multixact_freeze_max_age": 10000}
    if arm.endswith("iits10s"): extra["idle_in_transaction_session_timeout"] = "'10s'"
    cl = Cluster(v, "xd", extra).init().start()
    rec = dict(cell=f"x-{v}-{arm}-r{rep}", v=v, arm=arm, rep=rep, order=order)
    try:
        with cl.conn() as c0:
            c0.execute("create database app"); c0.execute("create database other")
        with cl.conn("other") as co:
            co.execute("create table t(id int primary key)"); co.execute("insert into t values (1)")
        hold = None
        if arm != "control":
            hold = cl.conn("other", autocommit=False)
            if "locker" in arm: hold.execute("select id from t where id = 1 for share")
            elif arm == "other_db_xid_only": hold.execute("select pg_current_xact_id()")
            elif arm == "other_db_rr_snapshot":
                hold.execute("set transaction isolation level repeatable read"); hold.execute("select count(*) from t")
        with cl.conn("app") as cn:
            setup(cn, E + 10)
            rec["workload_secs"] = episodes(cl, K, E, "for_share", db="app")
            peak = cl.snapshot(cn); rec["peak"] = {k: peak[k] for k in ["next_multi","next_offset","oldest_multi","members_bytes"]}
            after, rec["trace"] = poll(cl, cn, WAIT)
            rec["after"] = {k: after[k] for k in ["next_multi","next_offset","oldest_multi","members_bytes","datminmxid"]}
            if hold is not None:
                try: hold.commit(); rec["hold_alive_at_release"] = True
                except psycopg.Error as e: rec["hold_alive_at_release"] = False; rec["hold_error"] = str(e).splitlines()[0]
                hold.close()
                rel, rec["release_trace"] = poll(cl, cn, WAIT)
                rec["released"] = {k: rel[k] for k in ["next_multi","next_offset","oldest_multi","members_bytes","datminmxid"]}
        rec["ts"] = time.time(); return rec
    finally:
        cl.destroy()

if __name__ == "__main__":
    vers = sys.argv[1].split(","); reps = int(sys.argv[2])
    done = {json.loads(l)["cell"] for l in open(OUT)} if os.path.exists(OUT) else set()
    cells = list(itertools.product(vers, ARMS, range(1, reps+1))); random.Random(20261131).shuffle(cells)
    for i, (v, arm, rep) in enumerate(cells):
        if f"x-{v}-{arm}-r{rep}" in done: continue
        r = run_cell(v, arm, rep, i); open(OUT, "a").write(json.dumps(r) + "\n")
        print(r["cell"], "peak", r["peak"]["members_bytes"], "after", r["after"]["members_bytes"], r["after"]["oldest_multi"],
              "released", r.get("released", {}).get("members_bytes"), r.get("hold_alive_at_release"), flush=True)
