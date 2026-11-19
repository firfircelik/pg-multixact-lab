#!/bin/sh
# PG 19.0 GA subset. Falls back to Python bz2 extraction when the bzip2 binary is missing.
# Idempotent: download+verify+build once, grids skip finished cells.
cd "$(dirname "$0")/.." && mkdir -p logs/controls && LABROOT=$PWD
st(){ echo "$(date -Is) $*" >> "$LABROOT/logs/run_19_0.log"; }
export PATH=$PWD/install/tools/bin:$PATH
if [ ! -x install/pg-19.0/bin/postgres ]; then
  cd src
  curl -sf https://ftp.postgresql.org/pub/source/v19.0/postgresql-19.0.tar.bz2.sha256 -o postgresql-19.0.tar.bz2.sha256 || { st "19.0 not released yet"; exit 2; }
  curl -sf https://ftp.postgresql.org/pub/source/v19.0/postgresql-19.0.tar.bz2 -o postgresql-19.0.tar.bz2 && sha256sum -c postgresql-19.0.tar.bz2.sha256 >> ../logs/checksums.txt 2>&1 || { st "download/verify failed"; exit 3; }
  rm -rf postgresql-19.0 && { if command -v bzip2 >/dev/null; then tar xjf postgresql-19.0.tar.bz2; else python3 -c "import tarfile; tarfile.open('postgresql-19.0.tar.bz2','r:bz2').extractall()"; fi; } && cd postgresql-19.0 && \
  ./configure --prefix=$PWD/../../install/pg-19.0 --without-readline --without-zlib --without-icu --without-zstd --without-lz4 > ../../logs/configure-19.0.log 2>&1 && \
  make -j6 world-bin > ../../logs/build-19.0.log 2>&1 && make install-world-bin > ../../logs/install-19.0.log 2>&1 || { st "build failed"; exit 4; }
  cd ../..; st "19.0 built"
fi
P=install/venv/bin/python
st "g1 19.0"; $P scripts/grid_growth.py g1 19.0 >> logs/g1_19_0.log 2>&1; scripts/backup.sh >/dev/null
st "reclaim 19.0"; $P scripts/grid_reclaim.py 19.0 >> logs/reclaim_19_0.log 2>&1; scripts/backup.sh >/dev/null
[ -f results/.limit19_done ] || { st "limit 19.0"; $P scripts/limit_ff.py 19.0 3 >> logs/limit_19_0.log 2>&1 && touch results/.limit19_done; scripts/backup.sh >/dev/null; }
[ -f results/.up19_done ] || { st "upgrade 18.7->19.0"; NEWV=19.0 UOUT=u_upgrade_19_0.jsonl USIZES=10000 UREPS=3 $P scripts/upgrade_rewrite.py >> logs/upgrade_19_0.log 2>&1 && touch results/.up19_done; scripts/backup.sh >/dev/null; }
st "19.0 subset done"
