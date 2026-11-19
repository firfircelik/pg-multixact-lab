# pg-multixact-lab

Reproducible lab for **PostgreSQL MultiXact member space**: how fast it grows under shared row locks,
what does and does not reclaim it, and what changes between **PostgreSQL 18.7** and **19 RC2**
(64-bit member offsets, `pg_get_multixact_stats()`, `pg_upgrade` rewrite of `pg_multixact`).

Motivation: the [Metronome RCA (May 2025)](https://metronome.com/blog/root-cause-analysis-postgresql-multixact-member-exhaustion-incidents-may-2025),
where an Aurora PostgreSQL 13.18 cluster ran out of member space while far below its MultiXact ID threshold.

All numbers come from one VM. Design, seeds, deviations and the full run log are in [PROTOCOL.md](PROTOCOL.md).

## What it tests

| grid | script | question | runs |
|---|---|---|---|
| G1 | `grid_growth.py g1` | members per episode vs overlapping lockers k (theory: k-1 MultiXacts, k(k+1)/2-1 members) | 72 |
| G2 | `grid_growth.py g2` | members per committed FK insert under pgbench concurrency (clients x hot rows x pause in txn) | 72 |
| R | `grid_reclaim.py` | what frees member space: VACUUM, VACUUM FREEZE, vacuumdb --all --freeze, autovacuum, with/without an idle locker | 42 |
| L | `limit_ff.py` | 18.7 vs 19 RC2 at the 2^32 member-offset stop (fast-forwarded, see below) | 6 |
| X | `grid_crossdb.py` | does a session in ANOTHER database pin reclamation (row lock, xid only, RR snapshot, timeout) | 30 |
| M | `grid_mitig.py` | mitigations: pool cap, no pause, batching, spreading hot keys | 30 |
| U | `upgrade_rewrite.py` | `pg_upgrade --link` 18.7 -> 19 RC2: member compaction and timing | 9 |
| controls | `controls.py` | 18->18 and empty upgrade baselines, amcheck after upgrade, undistorted limit test, fsync-on G2 subset, rerun of a void cell | 21 |

Results summary: [results/summary.md](results/summary.md). Charts: [charts/](charts/).

## Versions and native build

No Docker, no root. PostgreSQL built from official tarballs, sha256 verified against the published `.sha256` files:

| version | tarball sha256 |
|---|---|
| 18.7 | `441dfae4280cae3e0425e5f876f80559fb0731c140f1aae22b3e5a54d0411bfd` |
| 19 RC2 | `23a7bb862e4bac395c047305ed0084117b01e71255ce858b907cdcd78ce40351` |

```sh
scripts/build_pg.sh 18.7      # -> install/pg-18.7
scripts/build_pg.sh 19rc2     # -> install/pg-19rc2
# configure --without-readline --without-zlib --without-icu --without-zstd --without-lz4
# make world-bin && make install-world-bin   (includes contrib/amcheck)
```

The study box had no m4/make/bison/flex; they were built from GNU/flex tarballs with GPG signatures checked
([provenance/](provenance/)). PostgreSQL 19.0 was tagged (`REL_19_0`) but not yet released as a tarball when
these runs were made; `scripts/run_19_0.sh` builds 19.0 and reruns a subset once it is on ftp.postgresql.org.

Python 3 venv in `install/venv`:

```sh
python3 -m venv install/venv
install/venv/bin/pip install -r requirements-freeze.txt   # psycopg 3.2.13, matplotlib 3.11.2, ...
```

## Running

Every script creates and destroys its own clusters under `scratch/`, listening only on a unix socket in
`scratch/sock` (no TCP). Each run appends one JSON line to `results/`; grids skip cells already present,
so they are resumable. Long grids are best started detached (`setsid nohup ... &`).

```sh
P=install/venv/bin/python
$P scripts/smoke.py                              # quick sanity check of the formula on both versions
$P scripts/grid_growth.py g1 18.7,19rc2          # G1   (seed 20261119)
$P scripts/grid_growth.py g2 18.7,19rc2          # G2   (seed 20261121)
$P scripts/grid_reclaim.py 18.7,19rc2            # R    (seed 20261126)
$P scripts/limit_ff.py 18.7,19rc2 3              # L    (fixed order)
$P scripts/grid_crossdb.py 18.7,19rc2 3          # X    (seed 20261131)
$P scripts/grid_mitig.py 18.7,19rc2              # M    (seed 20261141)
$P scripts/upgrade_rewrite.py                    # U    (env: NEWV=19rc2 UOUT=u_upgrade.jsonl USIZES=1000,10000,30000 UREPS=3)
$P scripts/controls.py uc1 uc0 uc2 lc1 g2r g2f   # controls (G2-F seed 20261151; load gate)
$P scripts/analyze.py                            # results/summary.md + charts/
```

`chain2.sh`, `chain3.sh`, `chain_controls.sh` are the detached chains used in the study (they also call `backup.sh`).

## Box and caveats

- **Hardware:** single VM, Linux 6.12.113, 8 vCPU (hypervisor reports "Intel(R) Xeon(R) Processor"), 15 GiB RAM,
  virtio disk (media unknown), gcc 14.2.0, Debian. The box is shared and is paused when idle.
- **fsync caveat:** clusters run with `fsync=off`, `synchronous_commit=off`, `full_page_writes=off` (autovacuum off unless the
  arm enables it). This does not change deterministic counts (G1, R, X, L, U member/MultiXact counts, bytes per member).
  It **does** change concurrency-dependent results (G2/M members per transaction and every rate). The G2-F control
  (fsync on) measured 58.2 vs 60.7 members/tx with a 5 ms pause, but 20.8 vs 9.6 without a pause. Treat G2/M as lab-relative.
- **pg_resetwal fast-forward (L grid):** reaching 2^32 real members needs ~21 GB and hours, so the limit test moves the
  next member offset with `pg_resetwal -O` to 20,897,664 members below the 18.7 stop point. The limit arithmetic is the
  server's real one; the skipped members are not real. In the original L runs the gap made MultiXact 1 look like it had
  ~4.27 billion members, which made VACUUM of that table fail; the `lc1` control avoids this (gap behind a MultiXact on a
  dropped table) and is the evidence for vacuum behaviour under a pin.
- **Load/suspension rule:** applied to the control runs only (load1 < 2 before each run, void + rerun if wall clock and uptime
  differ by > 5%). One earlier G2 cell is void; see `results/void_cells.json`.
- **VM reset:** the box was reset once during the study; `r_reclaim.jsonl` was restored from a checksummed backup taken
  before the reset and one limit attempt was discarded. Timeline in PROTOCOL.md.
- **Redaction:** absolute paths in results are replaced by `$LAB` and the host name by `<host>`. Nothing else was changed.

## Results layout

```
results/
  g1_hotrow.jsonl g2_churn.jsonl r_reclaim.jsonl l_limit.jsonl x_crossdb.jsonl m_mitig.jsonl u_upgrade.jsonl
  controls/       uc0 uc1 uc2 lc1 g2r g2f .jsonl
  discarded/      l_limit_attempt1.jsonl (not used)
  void_cells.json void / flagged cells and invalid fields
  env_g1.json env_g2.json   uname, CPU count, memory at run time
  summary.md      tables generated by analyze.py
charts/           PNGs generated by analyze.py and diagram.py
provenance/       tarball checksums and GPG verification output for build tools
```

Each JSON line holds the cell id, version, factors, seed-ordered position, and snapshots from `pg_controldata`
(after CHECKPOINT), `pg_multixact/` directory sizes, `datminmxid` per database and, on 19, `pg_get_multixact_stats()`.

## License

MIT, see [LICENSE](LICENSE).
