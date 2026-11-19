"""Mitigation grid on the worst G2 cell (64 clients, 1 hot parent, 5 ms in-txn pause):
 baseline   : 1 child insert per txn
 batch10    : 10 child inserts per txn (same pause) -> members per child row
 pool16     : only 16 clients (connection-pool cap), 1 insert per txn
 nopause    : 64 clients, no pause (move slow work outside the txn)
 spread16   : hot parent spread over 16 keys
Metric: members per inserted child row, TPS/rows per second. 20 s, 3 reps, shuffled."""
import json, os, random, subprocess, sys, time, itertools
from lab import Cluster, ROOT, SOCK
from staircase import setup

OUT = os.path.join(ROOT, "results", "m_mitig.jsonl")
ARMS = {"baseline": (64, 1, 5, 1), "batch10": (64, 1, 5, 10), "pool16": (16, 1, 5, 1),
        "nopause": (64, 1, 0, 1), "spread16": (64, 16, 5, 1)}

def run(v, arm, rep, order):
    c, hot, think, batch = ARMS[arm]
    cl = Cluster(v, "mi").init().start()
    try:
        with cl.conn() as cn:
            setup(cn, 1000); b = cl.snapshot(cn)
        script = os.path.join(ROOT, "scratch", "m.sql")
        with open(script, "w") as f:
            f.write(f"\\set pid random(1, {hot})\nBEGIN;\n")
            f.write(f"INSERT INTO child(parent_id) SELECT :pid FROM generate_series(1,{batch});\n")
            if think: f.write(f"\\sleep {think} ms\n")
            f.write("COMMIT;\n")
        env = dict(os.environ, PGHOST=SOCK, PGPORT=str(cl.port), PGUSER="lab")
        out = subprocess.run([cl.bin("pgbench"), "-n", "-f", script, "-c", str(c), "-j", "8", "-T", "20", "postgres"],
                             capture_output=True, text=True, env=env).stdout
        tx = int([l for l in out.splitlines() if "actually processed" in l][0].split(":")[1].split("/")[0])
        with cl.conn() as cn:
            a = cl.snapshot(cn); rows = cn.execute("select count(*) from child").fetchone()[0]
        dm = a["next_offset"] - b["next_offset"]
        return dict(cell=f"m-{v}-{arm}-r{rep}", v=v, arm=arm, rep=rep, order=order, clients=c, hot=hot, think_ms=think, batch=batch,
                    tx=tx, rows=rows, d_members=dm, d_multi=a["next_multi"]-b["next_multi"], members_per_row=dm/max(rows,1),
                    rows_per_s=rows/20, members_per_s=dm/20, ts=time.time())
    finally:
        cl.destroy()

if __name__ == "__main__":
    vers = sys.argv[1].split(",")
    done = {json.loads(l)["cell"] for l in open(OUT)} if os.path.exists(OUT) else set()
    cells = list(itertools.product(vers, ARMS, [1,2,3])); random.Random(20261141).shuffle(cells)
    for i, (v, arm, rep) in enumerate(cells):
        if f"m-{v}-{arm}-r{rep}" in done: continue
        r = run(v, arm, rep, i); open(OUT, "a").write(json.dumps(r) + "\n")
        print(r["cell"], r["rows"], r["d_members"], round(r["members_per_row"],2), flush=True)
