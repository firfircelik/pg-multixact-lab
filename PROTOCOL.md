> Public copy of the study protocol. Paths and the host name are redacted in results ($LAB = study root).

# MultiXact members study v2 (from scratch, restarted 2026-11-16, resumed 2026-11-19)

## Question
How fast does MultiXact *member* space grow under row-lock sharing, what does and does not
reclaim it, and what changes between PostgreSQL 18.7 and 19 (RC2; 19.0 if released on GA day)?
Motivation: Metronome RCA, May 2025 (member-space exhaustion, not MultiXactId wraparound).

## Builds (native, no Docker, no root)
- Sources: ftp.postgresql.org tarballs, sha256 verified against the published .sha256 files (logs/checksums.txt).
- Build tools m4 1.4.19, make 4.4.1, bison 3.8.2, flex 2.6.4 built from GNU/flex tarballs, GPG signatures checked (logs/gpg-*.txt).
- configure --without-readline --without-zlib --without-icu --without-zstd --without-lz4 (headers not on box; irrelevant to MultiXact code).
- gcc 14.2.0, Debian. Hardware: VM ("container-other" per systemd-detect-virt), Linux 6.12.113+,
  8 vCPU reported as "Intel(R) Xeon(R) Processor" (model hidden by hypervisor), 15 GiB RAM,
  virtio disk /dev/vdb 128 GB (reported rotational=1 by the guest; real media unknown). Shared box: other
  processes may run; see the load rule below.

## Safety rules (lab isolation)
- Every cluster: data dir under scratch/, listen_addresses='' (unix socket only, scratch/sock), study ports 55418 (18.7), 55419 (19rc2), 55490 (19.0).
- Harness creates and destroys its own clusters only. No other PostgreSQL on the box is touched
  (an unrelated pre-existing postgres on the box was left alone).
- No sudo, no system packages, no Docker. Backups: local backups/ plus an off-box copy ("store" below, a separate persistent mount).

## Common lab settings (deviations, documented)
fsync=off, synchronous_commit=off, full_page_writes=off, shared_buffers=256MB, max_connections=200,
autovacuum=off unless the arm says otherwise.
Scope of these settings (corrected 2026-11-19, AUDIT_v2 B3): they do not affect deterministic counts
(G1, R, X, L, U member/multi counts, bytes per member). They CAN affect concurrency-dependent results
(G2 and M members per transaction, throughput), because commit latency changes how long lockers overlap.
Control G2-F (fsync/synchronous_commit/full_page_writes on) measures this; see Controls below.
All G2/M rates are therefore lab-relative upper-bound style numbers, not production projections.
Counts come from pg_controldata (NextMultiXactId, NextMultiOffset, oldestMultiXid) after CHECKPOINT,
on-disk bytes of pg_multixact/{members,offsets}, and on 19 pg_get_multixact_stats().

## Grids
- G1 hot-row episodes (deterministic): version {18.7, 19rc2} x lockers k {2,4,8,16,32,64} x mode {FOR SHARE, FK insert}
  x 3 reps, 1000 episodes/cell, fresh cluster per cell, shuffled order (seed 20261119).
  Episode = k sessions lock the same row in turn with all transactions open, then all commit.
  Theory: k-1 multixacts and k(k+1)/2-1 members per episode.
- G2 concurrent FK churn (pgbench, 20 s): version x clients {8,32,64} x hot parent rows {1,16} x think-in-txn {0,5 ms} x 3 reps, shuffled (seed 20261121).
- R reclamation: version x arm {none, VACUUM, VACUUM (FREEZE), vacuumdb --all --freeze, same + idle-in-transaction locker,
  autovacuum (multixact_freeze_max_age=10000, naptime 1s), same + locker} x 3 reps; workload k=32, 4000 episodes
  (124,000 multixacts / 2,108,000 members) in database app; shuffled (seed 20261126).
- L members limit (fast-forward): pg_resetwal -O to a segment-aligned offset 20.9M members below 2^32 while an orphaned
  prepared transaction pins multixact 1; then consume. This is a lab shortcut: the skipped member space holds no data,
  but the server's limit arithmetic is the real one. Reported as such.
- X cross-database pins (ADDED AFTER pre-registration, 2026-11-19 ~09:20): version x holder
  {row lock in another DB, xid-only txn, REPEATABLE READ snapshot, row lock + idle_in_transaction_session_timeout=10s,
  none} x 3 reps, autovacuum on, shuffled (seed 20261131).
- M mitigations (ADDED AFTER pre-registration, ~10:00): version x arm {baseline 64 clients/1 hot row/5 ms,
  pool16, nopause, batch10, spread16} x 3 reps, shuffled (seed 20261141).
- U pg_upgrade 18.7 -> 19rc2 (ADDED AFTER pre-registration, ~10:45): episodes {1000, 10000, 30000} x k=64 x 3 reps,
  fixed order (no shuffle). L also ran in fixed order.
- Seeds summary: G1 20261119, G2 20261121, R 20261126, X 20261131, M 20261141, G2-F 20261151;
  pgbench --random-seed 20261119+rep in G2-F/G2-R controls (earlier G2 runs used pgbench's default seeding).

## Load / suspension rule (declared 2026-11-19 11:30, AUDIT_v2 B6)
- Applied to the control cells only (controls.py). NOT applied to G1/G2/R/L/X/M/U, which ran before it existed;
  this is a deviation and is disclosed in the article.
- Gate before each cell: 1-minute load average < 2.0 (wait up to 120 s, then run and flag), free disk > 2 GB.
- Suspension check: wall-clock duration vs /proc/uptime delta; if they differ by > 5% the cell is void and rerun once.
  All 21 control cells passed (max drift 0.34%). G2-F cells started at load1 1.85-1.99 after the gate wait (pgbench's own load decays slowly).
- Known bad cells in the earlier grids (kept in the jsonl, excluded or flagged):
  - g2-18.7-c64-h16-t5-r1: 115,457 tx after a 286 s gap before the cell (likely box suspension). VOID; replaced by control
    G2-R (242,906 tx, 4.53 members/tx, in line with r2/r3).
  - g2-19rc2-c64-h1-t5-r2: about 10% below its siblings; kept, flagged; medians reported.

## Controls (auditor-requested, 2026-11-19 11:29-11:50, controls.py, results/controls/)
- U-C1: 18.7 -> 18.7 pg_upgrade, 1000 episodes k=64, 3 reps (attributes compaction to the 18->19 rewrite).
- U-C0: 18.7 -> 19rc2 with 0 episodes, 3 reps (pg_upgrade baseline time).
- U-C2: 18.7 -> 19rc2, 10000 episodes, amcheck verify_heapam + bt_index_check before, after, and after relocking all rows
  with 4 sessions (FOR SHARE).
- L-C1: 18.7 limit test with an UNDISTORTED gap: victim MultiXact 2 lives on a dropped table and absorbs the
  fast-forward gap, probes use fresh rows; records vacuumdb exit codes while pinned and after unpin.
- G2-R: rerun of the void G2 cell. G2-F: fsync/synchronous_commit/full_page_writes ON, 64 clients, 1 hot row,
  think {0, 5 ms}, both versions, 3 reps, shuffled (seed 20261151).

## Box reset timeline (resolved 2026-11-19, AUDIT_v2 B6)
- r_reclaim cells order 36-41 finished 08:33:58-08:37:24 TR (jsonl ts field). The chained backup wrote store backup
  pg-multixact-v2-20261119-0837.tgz at 08:37:26 (store mtime), containing all 42 cells.
  sha256 27509c2b2cdad78bcc5ffa50044eaf17cbef964a809947f8c233875353ca8255.
- The box then came back with an older disk state: results/r_reclaim.jsonl had 36 lines (last cell finished
  08:33:58), local backups/ had only 0728 and 0755 (no local 0837). That disk state is consistent with a snapshot
  taken between 08:33:58 and 08:37:24, while the old instance kept running long enough to write the store backup
  at 08:37:26 (the store is a separate mount and was not rolled back). Local 0755 was re-copied at 08:40:00 (mtime).
- Conflict: /proc/uptime implies a boot at ~08:32:34, which is before data the old instance wrote at 08:37:24.
  I cannot fully reconcile the two clocks. Likely explanations: the restored guest's uptime does not count time
  the VM is paused (the box is paused when no tool call is active), or the uptime origin belongs to the snapshot.
  I therefore do not use uptime to date the reset; only wall-clock file stamps are used.
- Local backup 0853 still had the 36-line file; results/r_reclaim.jsonl was then replaced with the 42-line file
  from store backup 0837 (6 extra cells, orders 36-41). The first limit_ff attempt was lost and the second partial
  attempt discarded (results/discarded/); the venv was rebuilt.
- Data note: r_reclaim.jsonl fields autovac_log_lines / autovac_template0 / warnings are INVALID (server log file was appended across cells; fixed in lab.py on 2026-11-19). Use datminmxid and oldestMultiXid instead.
- Box was reset ~08:32-08:40 TR on 2026-11-19; r_reclaim.jsonl (42 cells) restored from store backup 0837. First limit_ff attempt lost; rerun.

## Control results summary (2026-11-19 11:50)
- G2-F medians (members/tx, fsync on vs off): 64/1/5 ms 18.7 58.2 vs 60.7, 19rc2 58.3 vs 60.6; 64/1/0 ms 18.7 20.8 vs 9.6, 19rc2 21.4 vs 9.5.
- U-C1 no compaction 18->18; U-C0 baseline 1.58-1.62 s; U-C2 amcheck clean; L-C1 vacuumdb rc 0 while pinned and after unpin, writes back 6.2 s after autovacuum processed template0; G2-R 4.53 members/tx.

## 19.0 GA rerun (2026-11-19, 17:36-18:41 TR)
- 19.0 tarball from ftp.postgresql.org, sha256 a89fd70efbad6a08c7dbed8d4872e3af30170d9e5ca9dc7906523be95b4e692d (matches the published .sha256). Same configure flags; port 55490.
- First attempt (17:35) failed before configure: the box had no `bzip2` binary after the morning reset, so `tar xjf` could not run. run_19_0.sh now falls back to Python bz2 extraction. No measurement was affected.
- Subset rerun with the unchanged scripts: G1 (36 cells, seed 20261119), R (21 cells, seed 20261126), L (3 reps, fixed order), U 18.7 -> 19.0 (10,000 episodes x 3 reps -> results/u_upgrade_19_0.jsonl). G2, X, M and the fsync-on control were not rerun (no change in multixact.c / ri_triggers.c between RC2 and 19.0).
- Not load-gated (same as the original grids). Two R cells (r-19.0-autovac-r3, r-19.0-autovac_blocked-r1) show a 462 s / 425 s gap inside their poll trace: the VM was paused while the agent was idle (logind watchdog aborts in dmesg at each resume; uptime advanced with wall clock, so the uptime-vs-wall rule cannot catch this kind of pause). Both kept and flagged; rerun as results/controls/r19_rerun.jsonl (scripts/rerun_r19.py, max poll gap 2.1 s), same outcomes.
