#!/bin/bash
set -euo pipefail
test -n "${BOYANKB_MONGO_KEY:-}"
umask 077
printf '%s' "$BOYANKB_MONGO_KEY" > /data/configdb/replica.key
chown mongodb:mongodb /data/configdb/replica.key
chmod 600 /data/configdb/replica.key
exec /usr/local/bin/docker-entrypoint.sh mongod --replSet boyankb --keyFile /data/configdb/replica.key --bind_ip_all
