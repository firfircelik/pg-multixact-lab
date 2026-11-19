"""Auditor control tests (AUDIT_v2 section 4). Each cell is gated:
load1 < 2.0 before start (wait up to 120 s, else record gate_fail), free disk > 2 GB,
and wall-vs-uptime drift < 5% (suspension check); a drifting cell is voided and rerun once.
Study ports/sockets/scratch only. Server logs copied to logs/controls/. Exit codes recorded."""
import json, os, sys, time, shutil, subprocess
import psycopg
import lab
from lab import Cluster, ROOT, SOCK, sh
from staircase import setup, episodes

OUT = os.path.join(ROOT, "results", "controls")
LOGD = os.path.join(ROOT, "logs", "controls")
ENV = lambda cl: dict(os.environ, PGHOST=SOCK, PGPORT=str(cl.port), PGUSER="lab")

def uptime(): return float(open("/proc/uptime").read().split()[0])
def load1(): return os.getloadavg()[0]
def gate():
    t0 = time.time()
    while load1() >= 2.0 and time.time() - t0 < 120: time.sleep(5)
    free = shutil.disk_usage(ROOT).free
    return {"load1_start": round(load1(), 2), "gate_wait_s": round(time.time() - t0, 1), "gate_ok": load1() < 2.0 and free > 2e9, "free_gb": round(free / 1e9, 1)}

def run_gated(name, fn, *a):
    for attempt in (1, 2):
        g = gate(); w0, u0 = time.time(), uptime()
        rec = fn(*a)
        w, u = time.time() - w0, uptime() - u0
        drift = abs(w - u) / max(w, 1e-6)
        rec.update(g, wall_s=round(w, 2), uptime_s=round(u, 2), drift=round(drift, 4), load1_end=round(load1(), 2), attempt=attempt)
        rec["void"] = drift > 0.05 or not g["gate_ok"]
        open(os.path.join(OUT, name + ".jsonl"), "a").write(json.dumps(rec, default=str) + "\n")
        print(name, rec.get("cell"), "void" if rec["void"] else "ok", {k: rec.get(k) for k in ("summary",)}, flush=True)
        if not rec["void"]: return rec
    return rec

def keeplog(cl, tag):
    if os.path.exists(cl.log): shutil.copy(cl.log, os.path.join(LOGD, f"{tag}.log"))

def amcheck(cl, db="postgres"):
    with cl.conn(db) as c:
        c.execute("create extension if not exists amcheck")
        heap = c.execute("select count(*) from verify_heapam('parent', check_toast => true)").fetchone()[0]
        heap_rows = c.execute("select blkno, offnum, msg from verify_heapam('parent') limit 5").fetchall()
        c.execute("select bt_index_check('parent_pkey', true)")
        return {"verify_heapam_rows": heap, "verify_heapam_sample": heap_rows, "bt_index_check": "ok"}

# ---------- upgrade controls ----------
def upgrade_cell(oldv, newv, E, tag, do_amcheck=False):
    old = Cluster(oldv, f"{tag}o").init().start()
    new = Cluster(newv, f"{tag}n").init()
    rec = dict(cell=tag, oldv=oldv, newv=newv, episodes=E, k=64)
    try:
        with old.conn() as c: setup(c, E + 10)
        if E: episodes(old, 64, E, "for_share")
        with old.conn() as c:
            s = old.snapshot(c); rec["old"] = {k: s[k] for k in ("next_multi","next_offset","oldest_multi","members_bytes","offsets_bytes")}
            rec["old_rows"] = list(c.execute("select count(*), coalesce(sum(v),0), coalesce(sum(id),0) from parent").fetchone())
        if do_amcheck: rec["amcheck_old"] = amcheck(old)
        old.stop(); keeplog(old, f"{tag}-old")
        wd = os.path.join(ROOT, "scratch", f"wd-{tag}"); shutil.rmtree(wd, ignore_errors=True); os.makedirs(wd)
        t0 = time.time()
        p = subprocess.run([new.bin("pg_upgrade"), "-b", os.path.dirname(old.bin("postgres")), "-B", os.path.dirname(new.bin("postgres")),
                            "-d", old.data, "-D", new.data, "-U", "lab", "-p", "55438", "-P", "55439", "-s", SOCK, "--link"],
                           cwd=wd, capture_output=True, text=True)
        rec["upgrade_secs"] = round(time.time() - t0, 3); rec["upgrade_rc"] = p.returncode
        rec["upgrade_out_tail"] = p.stdout.splitlines()[-12:] + p.stderr.splitlines()[-5:]
        if p.returncode == 0:
            new.start()
            with new.conn() as c:
                s = new.snapshot(c); rec["new"] = {k: s.get(k) for k in ("next_multi","next_offset","oldest_multi","members_bytes","offsets_bytes","stats")}
                rec["new_rows"] = list(c.execute("select count(*), coalesce(sum(v),0), coalesce(sum(id),0) from parent").fetchone())
            if do_amcheck:
                rec["amcheck_new"] = amcheck(new)
                # lock EVERY row with 4 sessions: each tuple's old multixact must be readable
                t1 = time.time(); cs = [new.conn(autocommit=False) for _ in range(4)]
                try:
                    for cn in cs: rec.setdefault("relock_counts", []).append(cn.execute("select count(*) from (select id from parent for share) s").fetchone()[0])
                    for cn in cs: cn.commit()
                    rec["relock_all_ok"] = True
                except psycopg.Error as e:
                    rec["relock_all_ok"] = False; rec["relock_error"] = str(e).splitlines()[0]
                finally:
                    for cn in cs: cn.close()
                rec["relock_secs"] = round(time.time() - t1, 2)
                rec["amcheck_new_after_relock"] = amcheck(new)
            keeplog(new, f"{tag}-new")
        rec["summary"] = (rec["upgrade_rc"], rec["upgrade_secs"], rec["old"]["next_offset"], (rec.get("new") or {}).get("next_offset"), (rec.get("new") or {}).get("members_bytes"))
        return rec
    finally:
        new.destroy(); old.destroy(); shutil.rmtree(os.path.join(ROOT, "scratch", f"wd-{tag}"), ignore_errors=True)

# ---------- L-C1: limit test without a distorted pinned MultiXact ----------
def lc1(v="18.7"):
    SEG = 1636 * 32; O_FF = 81640 * SEG; K = 64; ROWS = 20000
    cl = Cluster(v, "lc1", {"autovacuum": "on", "max_prepared_transactions": 10}).init().start()
    rec = dict(cell=f"lc1-{v}", v=v, o_ff=O_FF, events=[])
    def ev(name, **kw):
        rec["events"].append(dict(t=round(time.time(), 1), ev=name, **kw)); print("  ", name, {k: kw[k] for k in list(kw)[:4]}, flush=True)
    def state():
        with cl.conn() as c: s = cl.snapshot(c)
        return dict(oldest_multi=s["oldest_multi"], next_multi=s["next_multi"], next_offset=s["next_offset"], datminmxid=s["datminmxid"], members_bytes=s["members_bytes"])
    prow = [ROWS]
    def probe():
        prow[0] -= 1; cs = [cl.conn(autocommit=False) for _ in range(K)]
        try:
            for c in cs: c.execute("select id from parent where id = %s for share", (prow[0],))
            for c in cs: c.commit()
            return None
        except psycopg.Error as e:
            for c in cs:
                try: c.rollback()
                except Exception: pass
            return str(e).splitlines()[0]
        finally:
            for c in cs: c.close()
    def vacuumdb(label):
        t = time.time(); p = subprocess.run([cl.bin("vacuumdb"), "--all", "--freeze"], env=ENV(cl), capture_output=True, text=True)
        ev(label, rc=p.returncode, secs=round(time.time() - t, 2), stderr=p.stderr.strip().splitlines()[-3:], probe=probe(), **state())
    try:
        with cl.conn() as c:
            c.execute("create table parent(id int primary key, v int)")
            c.execute(f"insert into parent select g, 0 from generate_series(1,{ROWS}) g")
            c.execute("create table victim(id int primary key)"); c.execute("insert into victim values (1)")
            c.execute("vacuum (freeze) parent"); c.execute("vacuum (freeze) victim")
        # MultiXact 1 = {pin (prepared), s2} on parent row 1
        s1, s2 = cl.conn(autocommit=False), cl.conn(autocommit=False)
        s1.execute("select id from parent where id = 1 for share"); s2.execute("select id from parent where id = 1 for share")
        s2.commit()
        # MultiXact 2 = victim, on a table we drop: the fast-forward gap is attributed to MultiXact 2, which nothing references
        a, b = cl.conn(autocommit=False), cl.conn(autocommit=False)
        a.execute("select id from victim where id = 1 for share"); b.execute("select id from victim where id = 1 for share"); a.commit(); b.commit(); a.close(); b.close()
        s1.execute("prepare transaction 'pin'"); s1.close(); s2.close()
        with cl.conn() as c:
            c.execute("drop table victim"); rec["pre_ff"] = cl.snapshot(c)
        cl.stop()
        rec["resetwal"] = sh([cl.bin("pg_resetwal"), "-O", str(O_FF), "-D", cl.data]).strip()
        cl.start(); ev("start", **state())
        conns = [cl.conn(autocommit=False) for _ in range(K)]; row = 2; n = 0; base = state()["next_offset"]; t0 = time.time(); err = None
        while True:
            try:
                for c in conns: c.execute("select id from parent where id = %s for share", (row,))
                for c in conns: c.commit()
            except psycopg.Error as e:
                err = str(e); [c.rollback() for c in conns]; break
            n += 1; row = 2 + (n % (ROWS - 100))
            if time.time() - t0 > 400: break
        for c in conns: c.close()
        st = state(); ev("error" if err else "timeout", text=err, episodes=n, members_after_ff=st["next_offset"] - base, secs=round(time.time() - t0, 1), **st)
        ev("probe_after_error", result=probe(), **state())
        vacuumdb("vacuumdb_while_pinned")
        with cl.conn() as c: c.execute("rollback prepared 'pin'")
        ev("rollback_prepared", probe=probe(), **state())
        vacuumdb("vacuumdb_after_unpin")
        t2 = time.time(); r = "not probed"
        while time.time() - t2 < 240:
            s = state()
            if s["oldest_multi"] > 2:
                r = probe()
                if r is None: break
            time.sleep(3)
        ev("autovac_wait", secs=round(time.time() - t2, 1), probe=r, **state())
        log = open(cl.log).read().splitlines()
        rec["log_artifact_errors"] = [l for l in log if "invalid memory alloc" in l or "too many members" in l]
        rec["log_error_lines"] = [l for l in log if " ERROR: " in l][:10]
        rec["log_cutoff_warnings"] = sum("cutoff for freezing multixacts is far in the past" in l for l in log)
        rec["log_member_warnings"] = sum("must be vacuumed before" in l for l in log)
        keeplog(cl, f"lc1-{v}")
        ev_ = {e["ev"]: e for e in rec["events"]}
        rec["summary"] = (ev_.get("error", ev_.get("timeout"))["members_after_ff"], ev_["vacuumdb_while_pinned"]["rc"], ev_["vacuumdb_while_pinned"]["probe"], len(rec["log_artifact_errors"]), ev_["autovac_wait"]["probe"])
        return rec
    finally:
        cl.destroy()

# ---------- G2-F / G2-R ----------
def g2cell(v, clients, hot, think, rep, durable, tag):
    extra = {"fsync": "on", "synchronous_commit": "on", "full_page_writes": "on"} if durable else {}
    cl = Cluster(v, tag, extra).init().start()
    try:
        with cl.conn() as c:
            setup(c, 1000); b = cl.snapshot(c)
            rec_settings = {k: c.execute(f"show {k}").fetchone()[0] for k in ("fsync", "synchronous_commit", "full_page_writes")}
        script = os.path.join(ROOT, "scratch", f"{tag}.sql")
        with open(script, "w") as f:
            f.write(f"\\set pid random(1, {hot})\nBEGIN;\nINSERT INTO child(parent_id) VALUES (:pid);\n")
            if think: f.write(f"\\sleep {think} ms\n")
            f.write("COMMIT;\n")
        p = subprocess.run([cl.bin("pgbench"), "-n", "-f", script, "-c", str(clients), "-j", "8", "-T", "20", "--random-seed", str(20261119 + rep), "postgres"],
                           capture_output=True, text=True, env=ENV(cl))
        tx = int([l for l in p.stdout.splitlines() if "actually processed" in l][0].split(":")[1].split("/")[0])
        with cl.conn() as c: a = cl.snapshot(c)
        dm = a["next_offset"] - b["next_offset"]
        return dict(cell=f"{tag}-{v}-c{clients}-h{hot}-t{think}-r{rep}", v=v, clients=clients, hot=hot, think_ms=think, rep=rep, durable=durable,
                    settings=rec_settings, tx=tx, d_members=dm, d_multi=a["next_multi"] - b["next_multi"], members_per_tx=dm / max(tx, 1),
                    tps=tx / 20, summary=(tx, round(dm / max(tx, 1), 2)))
    finally:
        cl.destroy()

if __name__ == "__main__":
    which = sys.argv[1:] or ["uc1", "uc0", "uc2", "lc1", "g2r", "g2f"]
    for w in which:
        print("==", w, time.strftime("%H:%M:%S"), flush=True)
        if w == "uc1":
            for r in (1, 2, 3): run_gated("uc1", upgrade_cell, "18.7", "18.7", 1000, f"uc1-r{r}")
        elif w == "uc0":
            for r in (1, 2, 3): run_gated("uc0", upgrade_cell, "18.7", "19rc2", 0, f"uc0-r{r}")
        elif w == "uc2":
            run_gated("uc2", upgrade_cell, "18.7", "19rc2", 10000, "uc2-r1", True)
        elif w == "lc1":
            run_gated("lc1", lc1, "18.7")
        elif w == "g2r":
            run_gated("g2r", g2cell, "18.7", 64, 16, 5, 1, False, "g2r")
        elif w == "g2f":
            import random, itertools
            cells = list(itertools.product(["18.7", "19rc2"], [(64, 1, 0), (64, 1, 5)], [1, 2, 3])); random.Random(20261151).shuffle(cells)
            for v, (c, h, t), r in cells: run_gated("g2f", g2cell, v, c, h, t, r, True, "g2f")
    print("== controls done", time.strftime("%H:%M:%S"), flush=True)
