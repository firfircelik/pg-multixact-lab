#!/bin/sh
# Tar scripts, results, charts and PROTOCOL.md into backups/. Optional second copy: BACKUP_DST=/some/dir
cd "$(dirname "$0")/.." || exit 1
ts=$(date +%Y%m%d-%H%M); mkdir -p backups
tar czf backups/pg-multixact-lab-$ts.tgz scripts results charts PROTOCOL.md 2>/dev/null
[ -n "$BACKUP_DST" ] && mkdir -p "$BACKUP_DST" && cp backups/pg-multixact-lab-$ts.tgz "$BACKUP_DST"/
echo "backup $ts"
