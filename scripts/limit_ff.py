"""Members-limit experiment with a FAST-FORWARDED offset counter (lab technique).
1. fresh cluster (autovacuum on, defaults), multixact 1 created with an orphaned
   PREPARED transaction as one member (pins OldestMxact at 1 across restarts).
2. clean stop, pg_resetwal -O <segment-aligned offset 20.9M members below 2^32>,
   start. Members SLRU space between is never written (no real data), but the
   server's limit math sees ~4.27e9 members in use.
3. run hot-row episodes (k=64) until ERROR or until CROSS members past 2^32.
4. recovery path (18.x): ROLLBACK PREPARED, vacuumdb --all --freeze, then wait
   for autovacuum; probe writes after each step."""
import json, os, sys, time, subprocess
import psycopg
from lab import Cluster, ROOT, SOCK, sh

SEG = 1636 * 32
O_FF = 81640 * SEG                      # 4,274,017,280
TARGET = 2**32 + 24_000_000 - O_FF      # members to consume on versions without a limit
K, ROWS = 64, 20000
OUT = os.path.join(ROOT, "results", "l_limit.jsonl")

PROBE_ROW = [ROWS]
def probe(cl, k=64):
    """one k=64 episode on a never-touched row (never row 1: multixact 1's member
    length is distorted by the fast-forward gap). Returns None or error text."""
    PROBE_ROW[0] -= 1; row = PROBE_ROW[0]
    cs = [cl.conn(autocommit=False) for _ in range(k)]
    try:
        for c in cs: c.execute("select id from parent where id = %s for share", (row,))
        for c in cs: c.commit()
        return None
    except psycopg.Error as e:
        for c in cs:
            try: c.rollback()
            except Exception: pass
        return str(e).splitlines()[0]
    finally:
        for c in cs: c.close()

def run(v, rep):
    cl = Cluster(v, "lim", {"autovacuum": "on", "max_prepared_transactions": 10}).init().start()
    rec = dict(cell=f"l-{v}-r{rep}", v=v, rep=rep, o_ff=O_FF, k=K, events=[])
    ev = lambda name, **kw: rec["events"].append(dict(t=time.time(), ev=name, **kw)) or print(v, name, kw, flush=True)
    try:
        with cl.conn() as c:
            c.execute("create table parent(id int primary key, v int)")
            c.execute(f"insert into parent select g, 0 from generate_series(1,{ROWS}) g")
            c.execute("vacuum (freeze) parent")
        s1, s2 = cl.conn(autocommit=False), cl.conn(autocommit=False)
        s1.execute("select id from parent where id = 1 for share")
        s2.execute("select id from parent where id = 1 for share")   # creates multixact 1 {s1,s2}
        s2.commit(); s1.execute("prepare transaction 'pin'"); s1.close(); s2.close()
        cl.stop()
        rec["resetwal"] = sh([cl.bin("pg_resetwal"), "-O", str(O_FF), "-D", cl.data])
        cl.start()
        with cl.conn() as c:
            rec["start"] = cl.snapshot(c)
        conns = [cl.conn(autocommit=False) for _ in range(K)]
        consumed0 = rec["start"]["next_offset"]; row = 2; t0 = time.time(); err = None; n = 0
        warned = False
        while True:
            try:
                for cn in conns: cn.execute("select id from parent where id = %s for share", (row,))
                for cn in conns: cn.commit()
            except psycopg.Error as e:
                err = str(e); [cn.rollback() for cn in conns]; break
            if not warned and any(n.severity == "WARNING" for n in []): pass
            n += 1; row = 2 + (n % (ROWS - 2))
            if n % 500 == 0:
                with cl.conn() as c:
                    s = cl.snapshot(c)
                used = s["next_offset"] - consumed0
                if n % 2500 == 0: ev("progress", episodes=n, members=used, next_offset=s["next_offset"], secs=round(time.time()-t0,1))
                if used >= TARGET: break
        for cn in conns: cn.close()
        with cl.conn() as c:
            end = cl.snapshot(c)
        rec["end"] = end; rec["episodes"] = n; rec["secs"] = time.time() - t0
        rec["members_after_ff"] = end["next_offset"] - consumed0
        if err:
            env = dict(os.environ, PGHOST=SOCK, PGPORT=str(cl.port), PGUSER="lab")
            def state(): 
                with cl.conn() as c: s = cl.snapshot(c)
                return dict(oldest_multi=s["oldest_multi"], datminmxid=s["datminmxid"], members_bytes=s["members_bytes"], next_offset=s["next_offset"])
            ev("error", text=err, members_after_ff=rec["members_after_ff"], next_offset=end["next_offset"])
            ev("probe_after_error", result=probe(cl), **state())
            t1 = time.time(); subprocess.run([cl.bin("vacuumdb"), "--all", "--freeze", "-q"], env=env, capture_output=True)
            ev("vacuumdb_all_freeze_while_pinned", secs=round(time.time()-t1,1), probe=probe(cl), **state())
            with cl.conn() as c: c.execute("rollback prepared 'pin'")
            ev("rollback_prepared", probe=probe(cl), **state())
            t1 = time.time(); subprocess.run([cl.bin("vacuumdb"), "--all", "--freeze", "-q"], env=env, capture_output=True)
            ev("vacuumdb_all_freeze_after_unpin", secs=round(time.time()-t1,1), probe=probe(cl), **state())
            t2 = time.time(); r = "not probed"
            while time.time() - t2 < 300:
                st_ = state()
                if st_["oldest_multi"] > 1:
                    r = probe(cl)
                    if r is None: break
                time.sleep(3)
            ev("autovac_wait", secs=round(time.time()-t2,1), probe=r, **state())
        else:
            ev("no_error", members_after_ff=rec["members_after_ff"], next_offset=end["next_offset"], crossed_2_32=end["next_offset"] > 2**32, stats=end.get("stats"))
            ev("probe_end", result=probe(cl))
            with cl.conn() as c: c.execute("rollback prepared 'pin'")
        log = open(cl.log).read().splitlines()
        rec["log_warnings"] = [l for l in log if "WARNING" in l or "ERROR" in l]
        rec["log_warn_count"] = len(rec["log_warnings"]); rec["log_members_warn_count"] = sum("must be vacuumed before" in l for l in rec["log_warnings"]); rec["log_warnings"] = rec["log_warnings"][:3] + rec["log_warnings"][-3:]
        rec["autovac_lines"] = [l for l in log if "automatic" in l][:20]
        return rec
    finally:
        cl.destroy()

if __name__ == "__main__":
    vers = sys.argv[1].split(","); reps = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    for rep in range(1, reps + 1):
        for v in vers:
            r = run(v, rep); open(OUT, "a").write(json.dumps(r, default=str) + "\n")
