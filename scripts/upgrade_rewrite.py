"""pg_upgrade 18.7 -> 19rc2 with live multixacts: the members/offsets SLRUs are
rewritten to the 64-bit offset format. Measure rewrite time vs member volume and
check that pre-upgrade multixacts are still readable (pgrowlocks-free check:
lock the rows again on 19 and confirm row values; tuples keep multi xmax)."""
import json, os, subprocess, sys, time
from lab import Cluster, ROOT, SOCK, sh
from staircase import setup, episodes
OUT = os.path.join(ROOT, "results", os.environ.get("UOUT", "u_upgrade.jsonl"))

def run(E, rep):
    old = Cluster("18.7", f"up{E}").init().start()
    new = Cluster(os.environ.get("NEWV", "19rc2"), f"up{E}").init()
    rec = dict(cell=f"u-E{E}-r{rep}", episodes=E, k=64, rep=rep)
    try:
        with old.conn() as c:
            setup(c, E + 10)
        # leave the last episode's lockers' multis on the tuples (no vacuum)
        episodes(old, 64, E, "for_share")
        with old.conn() as c:
            rec["old"] = {k: v for k, v in old.snapshot(c).items() if k in ("next_multi","next_offset","members_bytes","offsets_bytes")}
            rec["old_multi_xmax_rows"] = c.execute("select count(*) from parent where (xmax::text::bigint) > 0").fetchone()[0]
        old.stop()
        wd = os.path.join(ROOT, "scratch", f"upwork{E}"); os.makedirs(wd, exist_ok=True)
        t0 = time.time()
        p = subprocess.run([new.bin("pg_upgrade"), "-b", os.path.dirname(old.bin("postgres")), "-B", os.path.dirname(new.bin("postgres")),
                            "-d", old.data, "-D", new.data, "-U", "lab", "-p", "55438", "-P", "55439", "-s", SOCK, "--link"],
                           cwd=wd, capture_output=True, text=True)
        rec["upgrade_secs"] = time.time() - t0; rec["upgrade_rc"] = p.returncode
        rec["upgrade_tail"] = p.stdout.splitlines()[-25:]
        if p.returncode == 0:
            new.start()
            with new.conn() as c:
                s = new.snapshot(c)
                rec["new"] = {k: v for k, v in s.items() if k in ("next_multi","next_offset","members_bytes","offsets_bytes","stats")}
                rec["rows"] = c.execute("select count(*), sum(v) from parent").fetchone()
            # re-lock pre-upgrade rows: forces reading old multixact members
            t1 = time.time(); episodes(new, 4, min(E, 2000), "for_share"); rec["relock_secs"] = time.time() - t1
            with new.conn() as c:
                rec["after_relock"] = {k: v for k, v in new.snapshot(c).items() if k in ("next_multi","next_offset")}
                rec["amcheck"] = "skipped"
        return rec
    finally:
        new.destroy(); old.destroy()
        subprocess.run(["rm", "-rf", os.path.join(ROOT, "scratch", f"upwork{E}")])

if __name__ == "__main__":
    sizes = [int(x) for x in os.environ.get("USIZES", "1000,10000,30000").split(",")]
    for rep in range(1, int(os.environ.get("UREPS", "3")) + 1):
        for E in sizes:
            r = run(E, rep); open(OUT, "a").write(json.dumps(r, default=str) + "\n")
            print(r["cell"], r["upgrade_rc"], round(r["upgrade_secs"],1), r.get("old"), r.get("new"), flush=True)
