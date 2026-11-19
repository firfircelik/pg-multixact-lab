"""Hot-row episodes. In one episode, k sessions take a row lock on the SAME
parent row one after another while all their transactions stay open, then all
commit. Each new locker forces a new MultiXact containing every live locker.
Theory (no cache reuse because every episode has fresh xids):
  multixacts per episode = k-1, members per episode = k(k+1)/2 - 1."""
import time

def setup(c, rows):
    c.execute("create table parent(id int primary key, v int)")
    c.execute("create table child(id bigserial primary key, parent_id int not null references parent(id))")
    c.execute(f"insert into parent select g, 0 from generate_series(1,{rows}) g")
    c.execute("vacuum (freeze, analyze) parent")

def episodes(cl, k, n_episodes, mode, db="postgres", first_row=1):
    conns = [cl.conn(db=db, autocommit=False) for _ in range(k)]
    if mode == "fk_insert":
        sql = "insert into child(parent_id) values (%s)"   # FK check = FOR KEY SHARE on parent row
    else:
        sql = {"for_share": "select id from parent where id = %s for share",
               "for_key_share": "select id from parent where id = %s for key share"}[mode]
    t0 = time.perf_counter()
    for e in range(n_episodes):
        row = first_row + e
        for cn in conns: cn.execute(sql, (row,))
        for cn in conns: cn.commit()
    dt = time.perf_counter() - t0
    for cn in conns: cn.close()
    return dt
