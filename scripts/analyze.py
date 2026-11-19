"""Summaries + charts from results/*.jsonl -> results/summary.md, charts/*.png"""
import json, os, collections, statistics as st
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
R = "results"; C = "charts"; os.makedirs(C, exist_ok=True)
L = lambda f: [json.loads(l) for l in open(os.path.join(R, f))] if os.path.exists(os.path.join(R, f)) else []
out = []
g1 = L("g1_hotrow.jsonl")
if g1:
    out.append("## G1 hot-row episodes (1000 episodes/cell, 3 reps)\n| v | mode | k | multis | members (measured) | members (theory) | members bytes | offsets bytes |\n|---|---|---|---|---|---|---|---|")
    g = collections.defaultdict(list)
    for r in g1: g[(r["v"], r["mode"], r["k"])].append(r)
    for k in sorted(g, key=lambda x: (x[0], x[1], x[2])):
        rs = g[k]; out.append(f"| {k[0]} | {k[1]} | {k[2]} | {sorted({r['d_multi'] for r in rs})} | {sorted({r['d_members'] for r in rs})} | {rs[0]['expect_members']} | {rs[0]['members_bytes']} | {rs[0]['offsets_bytes']} |")
    fig, ax = plt.subplots(figsize=(7,4.2))
    ks = [2,4,8,16,32,64]
    for v, m in [(v, "fk_insert") for v in ["18.7","19rc2","19.0"] if (v,"fk_insert",2) in g]:
        ax.plot(ks, [st.median(r["d_members"]/r["episodes"] for r in g[(v,m,k)]) for k in ks], "o-", label=f"{v} measured (FK insert)")
    ax.plot(ks, [k*(k+1)/2-1 for k in ks], "k--", label="k(k+1)/2 - 1")
    ax.set_xscale("log", base=2); ax.set_yscale("log"); ax.set_xlabel("overlapping lockers on one row (k)"); ax.set_ylabel("members per episode")
    ax.set_title("Member cost is quadratic in overlapping lockers"); ax.legend(); fig.tight_layout(); fig.savefig(f"{C}/g1_quadratic.png", dpi=150)
g2 = L("g2_churn.jsonl")
if g2:
    out.append("\n## G2 pgbench FK churn (20 s, 3 reps; medians)\n| clients | hot rows | pause in txn ms | v | TPS | members/tx [min-max] | members/s |\n|---|---|---|---|---|---|---|")
    g = collections.defaultdict(list)
    for r in g2: g[(r["clients"], r["hot"], r["think_ms"], r["v"])].append(r)
    for k in sorted(g):
        rs = g[k]; mpt = [r["members_per_tx"] for r in rs]
        out.append(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {round(st.median(r['tx']/r['duration'] for r in rs))} | {st.median(mpt):.2f} [{min(mpt):.2f}-{max(mpt):.2f}] | {round(st.median(r['d_members']/r['duration'] for r in rs))} |")
    fig, ax = plt.subplots(figsize=(7,4.2))
    for hot, think, mk in [(1,5,"o-"),(1,0,"s-"),(16,5,"^-"),(16,0,"v-")]:
        cl = [8,32,64]; ax.plot(cl, [st.median(r["members_per_tx"] for r in g[(c,hot,think,"18.7")]) for c in cl], mk, label=f"hot rows={hot}, pause={think} ms")
    ax.set_xlabel("concurrent clients"); ax.set_ylabel("members per committed txn (18.7)"); ax.set_title("A 5 ms pause inside the txn multiplies member burn (fsync off, lab-relative)")
    ax.legend(); fig.tight_layout(); fig.savefig(f"{C}/g2_members_per_tx.png", dpi=150)
rr = L("r_reclaim.jsonl")
if rr:
    out.append("\n## R reclamation (k=32 x 4000 episodes; 3 reps; members dir KiB and oldestMultiXid)\n| arm | v | peak KiB | after KiB | oldest after | template0 datminmxid after | released KiB | oldest released |\n|---|---|---|---|---|---|---|---|")
    g = collections.defaultdict(list)
    for r in rr: g[(r["arm"], r["v"])].append(r)
    for k in sorted(g):
        rs = g[k]; f = lambda s, key: sorted({(r[s][key]//1024 if key=="members_bytes" else r[s][key]) for r in rs if s in r})
        out.append(f"| {k[0]} | {k[1]} | {f('peak','members_bytes')} | {f('after','members_bytes')} | {f('after','oldest_multi')} | {sorted({r['after']['datminmxid']['template0'] for r in rs})} | {f('released','members_bytes')} | {f('released','oldest_multi')} |")
ll = L("l_limit.jsonl")
if ll:
    out.append("\n## L members limit (fast-forward to 4,274,017,280; k=64)\n| cell | outcome | members after ff | next offset | members-warnings | writes back after unpin (s) |\n|---|---|---|---|---|---|")
    for r in ll:
        evs = {e["ev"]: e for e in r["events"]}
        oc = "ERROR members limit" if "error" in evs else ("no error" if "no_error" in evs else "?")
        wb = evs["autovac_wait"]["secs"] if "autovac_wait" in evs else ""
        out.append(f"| {r['cell']} | {oc} | {r['members_after_ff']} | {r['end']['next_offset']} | {r['log_members_warn_count']} | {wb} |")
for uf, label in [("u_upgrade.jsonl", "18.7 -> 19rc2"), ("u_upgrade_19_0.jsonl", "18.7 -> 19.0")]:
    uu = L(uf)
    if uu:
        out.append(f"\n## U pg_upgrade {label} (k=64)\n| cell | rc | secs | members before | members after | members bytes before | after | offsets bytes before | after | rows |\n|---|---|---|---|---|---|---|---|---|---|")
        for r in uu:
            o, n = r.get("old", {}), r.get("new", {})
            out.append(f"| {r['cell']} | {r['upgrade_rc']} | {r['upgrade_secs']:.2f} | {o.get('next_offset',0)-1} | {n.get('next_offset',0)-1} | {o.get('members_bytes')} | {n.get('members_bytes')} | {o.get('offsets_bytes')} | {n.get('offsets_bytes')} | {r.get('rows')} |")
for name in ["x_crossdb.jsonl", "m_mitig.jsonl"]:
    rows = L(name)
    if rows: out.append(f"\n## {name}: {len(rows)} records (see analyze section)")
open(os.path.join(R, "summary.md"), "w").write("\n".join(out) + "\n"); print("\n".join(out))

# ---- extra charts ----
import numpy as np
rr = L("r_reclaim.jsonl")
if rr:
    arms = ["none","vacuum_plain_app","vacuum_freeze_app","vacuumdb_all_freeze","vacuumdb_all_freeze_blocked","autovac","autovac_blocked"]
    labels = ["nothing","VACUUM","VACUUM (FREEZE)","vacuumdb --all\n--freeze","vacuumdb + idle\nlocker","autovacuum","autovacuum +\nidle locker"]
    VS = [v for v in ["18.7","19rc2","19.0"] if any(r["v"]==v for r in rr)]
    fig, ax = plt.subplots(figsize=(9,4.2)); w = 0.8/len(VS)
    for i, v in enumerate(VS):
        vals = [st.median(r["after"]["members_bytes"]/1e6 for r in rr if r["arm"]==a and r["v"]==v) for a in arms]
        ax.bar(np.arange(len(arms)) + (i-(len(VS)-1)/2)*w, vals, w, label=v)
    ax.set_xticks(range(len(arms))); ax.set_xticklabels(labels, fontsize=8); ax.set_ylabel("pg_multixact/members after (MB, 10^6 B)")
    ax.set_title("Only autovacuum kept member space small; nothing freed it while a locker was idle"); ax.set_ylim(0, 13); ax.legend(loc="upper center", ncol=len(VS)); fig.tight_layout(); fig.savefig(f"{C}/r_reclaim.png", dpi=150)
xd = L("x_crossdb.jsonl")
if xd:
    arms = ["control","other_db_locker","other_db_xid_only","other_db_rr_snapshot","other_db_locker_iits10s"]
    labels = ["no other\nsession","row lock, idle\n(other DB)","open xid\n(other DB)","RR snapshot\n(other DB)","row lock +\nidle timeout 10s"]
    fig, ax = plt.subplots(figsize=(8,4)); w = 0.38
    for i, v in enumerate(["18.7","19rc2"]):
        vals = [st.median(r["after"]["members_bytes"]/1e6 for r in xd if r["arm"]==a and r["v"]==v) for a in arms]
        ax.bar(np.arange(len(arms)) + (i-0.5)*w, vals, w, label=v)
    ax.set_xticks(range(len(arms))); ax.set_xticklabels(labels, fontsize=8); ax.set_ylabel("members dir after 90 s (MB, 10^6 B)")
    ax.set_title("A row lock taken in ANOTHER database before the workload pinned reclamation"); ax.legend(); fig.tight_layout(); fig.savefig(f"{C}/x_crossdb.png", dpi=150)
mm = L("m_mitig.jsonl")
if mm:
    arms = ["baseline","pool16","nopause","batch10","spread16"]
    labels = ["baseline\n64 clients, 1 hot key,\n5 ms pause","pool cap 16","no pause\nin txn","10 rows\nper txn","16 hot keys"]
    fig, ax = plt.subplots(figsize=(8,4)); w = 0.38
    for i, v in enumerate(["18.7","19rc2"]):
        vals = [st.median(r["members_per_row"] for r in mm if r["arm"]==a and r["v"]==v) for a in arms]
        ax.bar(np.arange(len(arms)) + (i-0.5)*w, vals, w, label=v)
    ax.set_xticks(range(len(arms))); ax.set_xticklabels(labels, fontsize=8); ax.set_ylabel("members per inserted child row")
    ax.set_title("Mitigations: members per row"); ax.legend(); fig.tight_layout(); fig.savefig(f"{C}/m_mitig.png", dpi=150)
    out2 = ["\n## M mitigations (medians, 3 reps)\n| arm | v | members/row [min-max] | rows/s | members/s |\n|---|---|---|---|---|"]
    for a in arms:
        for v in ["18.7","19rc2"]:
            rs = [r for r in mm if r["arm"]==a and r["v"]==v]; m = [r["members_per_row"] for r in rs]
            out2.append(f"| {a} | {v} | {st.median(m):.2f} [{min(m):.2f}-{max(m):.2f}] | {round(st.median(r['rows_per_s'] for r in rs))} | {round(st.median(r['members_per_s'] for r in rs))} |")
    open(os.path.join(R, "summary.md"), "a").write("\n".join(out2) + "\n")
rr19 = L("controls/r19_rerun.jsonl")
if rr19:
    n = ["\n## Notes on 19.0 R cells",
         "- r-19.0-autovac-r3 and r-19.0-autovac_blocked-r1: poll trace has a 462 s / 425 s gap (box suspended mid-poll). Kept in r_reclaim.jsonl, flagged; end states match siblings.",
         "- Reruns (results/controls/r19_rerun.jsonl, max poll gap 2.1 s):"]
    for r in rr19:
        n.append(f"  - {r['cell']}: after members_bytes {r['after']['members_bytes']}, oldestMultiXid {r['after']['oldest_multi']}, released {r.get('released', {}).get('members_bytes', '-')}, wait {r.get('wait_secs', 0):.1f} s")
    open(os.path.join(R, "summary.md"), "a").write("\n".join(n) + "\n")
