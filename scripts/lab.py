"""Isolated native PostgreSQL lab helpers for the MultiXact study (v2).
Safety: every cluster lives under scratch/, listens only on a private unix
socket (listen_addresses=''), uses study-only ports 55418/55419/55490, and is
stopped + removed by this harness. Nothing else on the box is touched."""
import os, subprocess, time, shutil, json, re, psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOCK = os.path.join(ROOT, "scratch", "sock")
VERSIONS = {"18.7": 55418, "19rc2": 55419, "19.0": 55490}

def bindir(v): return os.path.join(ROOT, "install", f"pg-{v}", "bin")
def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout

class Cluster:
    def __init__(self, version, tag, extra_conf=None):
        self.v, self.port = version, VERSIONS[version]
        self.data = os.path.join(ROOT, "scratch", f"data-{version}-{tag}")
        self.log = os.path.join(ROOT, "scratch", f"pg-{version}-{tag}.log")
        self.extra = extra_conf or {}
    def bin(self, b): return os.path.join(bindir(self.v), b)
    def init(self):
        if os.path.exists(self.data): shutil.rmtree(self.data)
        if os.path.exists(self.log): os.remove(self.log)   # fresh server log per cell
        sh([self.bin("initdb"), "-D", self.data, "-U", "lab", "-A", "trust", "--no-sync", "-E", "UTF8", "--locale=C"])
        conf = {"listen_addresses": "''", "port": self.port, "unix_socket_directories": f"'{SOCK}'",
                "max_connections": 200, "shared_buffers": "'256MB'", "fsync": "off",
                "synchronous_commit": "off", "full_page_writes": "off", "autovacuum": "off",
                "max_wal_size": "'4GB'", "checkpoint_timeout": "'30min'", "log_autovacuum_min_duration": 0,
                "log_checkpoints": "on", "log_line_prefix": "'%m [%p] '"}
        conf.update(self.extra)
        with open(os.path.join(self.data, "postgresql.conf"), "a") as f:
            for k, val in conf.items(): f.write(f"{k} = {val}\n")
        return self
    def start(self):
        sh([self.bin("pg_ctl"), "-D", self.data, "-l", self.log, "-w", "-t", "120", "start"]); return self
    def stop(self, mode="fast"):
        if os.path.exists(os.path.join(self.data, "postmaster.pid")):
            sh([self.bin("pg_ctl"), "-D", self.data, "-m", mode, "-w", "-t", "300", "stop"])
    def destroy(self):
        self.stop("immediate") if False else None
        try: self.stop()
        except Exception: pass
        shutil.rmtree(self.data, ignore_errors=True)
    def conn(self, db="postgres", autocommit=True):
        return psycopg.connect(host=SOCK, port=self.port, user="lab", dbname=db, autocommit=autocommit)
    def controldata(self):
        out = sh([self.bin("pg_controldata"), "-D", self.data])
        d = {}
        for line in out.splitlines():
            if ":" in line:
                k, val = line.split(":", 1); d[k.strip()] = val.strip()
        return {"next_multi": int(d["Latest checkpoint's NextMultiXactId"]),
                "next_offset": int(d["Latest checkpoint's NextMultiOffset"]),
                "oldest_multi": int(d["Latest checkpoint's oldestMultiXid"]),
                "oldest_multi_db": int(d["Latest checkpoint's oldestMulti's DB"])}
    def du(self, sub):
        p = os.path.join(self.data, sub); tot = 0; n = 0
        for f in os.listdir(p):
            tot += os.path.getsize(os.path.join(p, f)); n += 1
        return tot, n
    def snapshot(self, c):
        c.execute("CHECKPOINT")
        s = self.controldata()
        s["members_bytes"], s["members_files"] = self.du("pg_multixact/members")
        s["offsets_bytes"], s["offsets_files"] = self.du("pg_multixact/offsets")
        s["datminmxid"] = {r[0]: int(r[1]) for r in c.execute("select datname, datminmxid::text from pg_database").fetchall()}
        if self.v.startswith("19"):
            r = c.execute("select num_mxids, num_members, members_size, oldest_multixact::text from pg_get_multixact_stats()").fetchone()
            s["stats"] = {"num_mxids": r[0], "num_members": r[1], "members_size": r[2], "oldest_multixact": int(r[3])}
        s["t"] = time.time()
        return s

def env_info():
    return {"uname": sh(["uname", "-a"]).strip(), "nproc": os.cpu_count(),
            "mem": sh(["free", "-b"]).splitlines()[1], "date": sh(["date", "-Is"]).strip()}
