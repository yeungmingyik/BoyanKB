#!/bin/sh
set -eu

case "${REDIS_PASSWORD:-}" in
  ''|*[!a-zA-Z0-9_-]*) exit 2 ;;
esac
umask 077
printf 'bind 0.0.0.0\nprotected-mode yes\nport 6379\nappendonly yes\ndir /data\nmaxmemory 192mb\nmaxmemory-policy noeviction\nrequirepass %s\n' "$REDIS_PASSWORD" > /run/boyankb-redis.conf
chown redis:redis /run/boyankb-redis.conf
exec /usr/local/bin/docker-entrypoint.sh redis-server /run/boyankb-redis.conf
