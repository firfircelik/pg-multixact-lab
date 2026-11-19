"""Growth grids.
G1 hot-row episodes (deterministic): version x k x mode x rep, fresh cluster per cell.
G2 concurrent FK churn (pgbench): version x clients x hot_rows x think_ms x rep.
Randomized run order (seed recorded); resumable via cell ids in results JSONL."""
import json, os, random, subprocess, sys, time, itertools
from lab import Cluster, ROOT, SOCK, env_info
from staircase import setup, episodes

SEED = 20261119
VERS = sys.argv[2].split(",") if len(sys.argv) > 2 else ["18.7", "19rc2"]
OUT = os.path.join(ROOT, "results")

def done_ids(path):
    if not os.path.exists(path): return set()
    return {json.loads(l)["cell"] for l in open(path) if l.strip()}

def diff(a, b):
    d = {"d_multi": a["next_multi"] - b["next_multi"], "d_members": a["next_offset"] - b["next_offset"],
         "members_bytes": a["members_bytes"], "offsets_bytes": a["offsets_bytes"],
         "members_files": a["members_files"], "offsets_files": a["offsets_files"]}
    if "stats" in a: d["stats"] = a["stats"]
    return d

def g1():
    path = os.path.join(OUT, "g1_hotrow.jsonl"); done = done_ids(path)
    cells = [dict(v=v, k=k, mode=m, rep=r) for v, k, m, r in itertools.product(VERS, [2,4,8,16,32,64], ["for_share","fk_insert"], [1,2,3])]
    random.Random(SEED).shuffle(cells)
    E = 1000
    for i, c in enumerate(cells):
        cid = f"g1-{c['v']}-k{c['k']}-{c['mode']}-r{c['rep']}"
        if cid in done: continue
        cl = Cluster(c["v"], "g1").init().start()
        try:
            with cl.conn() as cn:
                setup(cn, E); b = cl.snapshot(cn)
                dt = episodes(cl, c["k"], E, c["mode"]); a = cl.snapshot(cn)
            rec = dict(cell=cid, order=i, episodes=E, secs=dt, **c, **diff(a, b),
                       expect_multi=E*(c["k"]-1), expect_members=E*(c["k"]*(c["k"]+1)//2-1), ts=time.time())
            open(path, "a").write(json.dumps(rec) + "\n"); print(cid, rec["d_multi"], rec["d_members"], round(dt,2), flush=True)
        finally:
            cl.destroy()

def g2():
    path = os.path.join(OUT, "g2_churn.jsonl"); done = done_ids(path)
    cells = [dict(v=v, clients=cc, hot=h, think_ms=t, rep=r) for v, cc, h, t, r in
             itertools.product(VERS, [8,32,64], [1,16], [0,5], [1,2,3])]
    random.Random(SEED + 2).shuffle(cells)
    DUR = 20
    for i, c in enumerate(cells):
        cid = f"g2-{c['v']}-c{c['clients']}-h{c['hot']}-t{c['think_ms']}-r{c['rep']}"
        if cid in done: continue
        cl = Cluster(c["v"], "g2").init().start()
        try:
            with cl.conn() as cn:
                setup(cn, 1000); b = cl.snapshot(cn)
            script = os.path.join(ROOT, "scratch", "g2.sql")
            with open(script, "w") as f:
                f.write(f"\\set pid random(1, {c['hot']})\nBEGIN;\nINSERT INTO child(parent_id) VALUES (:pid);\n")
                if c["think_ms"]: f.write(f"\\sleep {c['think_ms']} ms\n")
                f.write("COMMIT;\n")
            env = dict(os.environ, PGHOST=SOCK, PGPORT=str(cl.port), PGUSER="lab")
            p = subprocess.run([cl.bin("pgbench"), "-n", "-f", script, "-c", str(c["clients"]), "-j", "8",
                                "-T", str(DUR), "postgres"], capture_output=True, text=True, env=env)
            out = p.stdout + p.stderr
            tx = int([l for l in out.splitlines() if "number of transactions actually processed" in l][0].split(":")[1].split("/")[0])
            failed = [l for l in out.splitlines() if "failed" in l]
            with cl.conn() as cn:
                a = cl.snapshot(cn)
            rec = dict(cell=cid, order=i, duration=DUR, tx=tx, failed=failed, **c, **diff(a, b), ts=time.time())
            rec["members_per_tx"] = rec["d_members"] / max(tx, 1)
            open(path, "a").write(json.dumps(rec) + "\n"); print(cid, tx, rec["d_multi"], rec["d_members"], round(rec["members_per_tx"],2), flush=True)
        finally:
            cl.destroy()

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    json.dump(env_info(), open(os.path.join(OUT, f"env_{sys.argv[1]}.json"), "w"), indent=1)
    {"g1": g1, "g2": g2}[sys.argv[1]]()
