from lab import Cluster
from staircase import setup, episodes
for v in ["18.7", "19rc2"]:
    cl = Cluster(v, "smoke").init().start()
    try:
        with cl.conn() as c:
            setup(c, 5000); b = cl.snapshot(c)
            for mode, k in [("for_share",4), ("fk_insert",4), ("for_share",32)]:
                t = episodes(cl, k, 200, mode); a = cl.snapshot(c)
                print(v, mode, k, "dmulti", a["next_multi"]-b["next_multi"], "doffset", a["next_offset"]-b["next_offset"], "expect", 200*(k-1), 200*(k*(k+1)//2-1), "membytes", a["members_bytes"], "offbytes", a["offsets_bytes"], a.get("stats"), round(t,2)); b = a
    finally:
        cl.destroy()
