#!/bin/sh
# Build one PostgreSQL version into install/pg-<ver> (same flags as the study). Usage: scripts/build_pg.sh 18.7 | 19rc2 | 19.0
# Needs a C compiler, make, bison, flex, perl. (The study box lacked m4/make/bison/flex; they were built from GPG-verified GNU tarballs, see provenance/.)
set -e
V=$1; [ -n "$V" ] || { echo "usage: $0 <version>"; exit 1; }
cd "$(dirname "$0")/.."; ROOT=$PWD; mkdir -p src install; cd src
U=https://ftp.postgresql.org/pub/source/v$V/postgresql-$V.tar.bz2
curl -sfO $U; curl -sfO $U.sha256
sha256sum -c postgresql-$V.tar.bz2.sha256
rm -rf postgresql-$V; tar xjf postgresql-$V.tar.bz2; cd postgresql-$V
./configure --prefix=$ROOT/install/pg-$V --without-readline --without-zlib --without-icu --without-zstd --without-lz4
make -j"$(nproc)" world-bin
make install-world-bin   # includes contrib (amcheck is needed by the upgrade control)
