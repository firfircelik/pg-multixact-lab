-- MultiXact member-space monitoring queries from the article
-- "PostgreSQL 19 removes the MultiXact members wall. Reclamation still waits for template0."
-- Not benchmarked. Check privileges on managed platforms; AWS documents rds_tools.pg_ls_multixactdir() for RDS
-- and aurora_stat_utils for Aurora.

-- Which database holds the MultiXact floor (template0 included)
SELECT datname, datallowconn, datminmxid, mxid_age(datminmxid) AS mxid_age
FROM pg_database ORDER BY mxid_age(datminmxid) DESC;

-- Oldest tables in the current database (run in each connectable database)
SELECT c.oid::regclass AS rel, c.relminmxid, mxid_age(c.relminmxid) AS mxid_age
FROM pg_class c WHERE c.relkind IN ('r', 'm', 't')
ORDER BY mxid_age(c.relminmxid) DESC LIMIT 20;

-- Member consumption as of the last checkpoint (graph the rate of next_multi_offset)
SELECT next_multixact_id, next_multi_offset, oldest_multi_xid FROM pg_control_checkpoint();

-- Members directory size: superuser, or EXECUTE granted on pg_ls_dir and pg_stat_file
SELECT count(*) AS segments,
       sum((pg_stat_file('pg_multixact/members/' || f)).size) AS bytes
FROM pg_ls_dir('pg_multixact/members') AS f;

-- PostgreSQL 19+: returns NULLs unless the role has pg_read_all_stats
SELECT num_mxids, num_members, members_size, oldest_multixact FROM pg_get_multixact_stats();

-- Pinning candidates, cluster-wide (pg_stat_activity cannot show who holds a row lock)
SELECT pid, datname, usename, state, now() - xact_start AS xact_age, backend_xid, left(query, 80)
FROM pg_stat_activity WHERE state LIKE 'idle in transaction%' ORDER BY xact_start;
SELECT gid, database, owner, prepared FROM pg_prepared_xacts ORDER BY prepared;
