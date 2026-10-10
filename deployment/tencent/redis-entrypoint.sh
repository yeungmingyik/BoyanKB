#!/bin/sh
set -eu

test -n "${REDIS_PASSWORD:-}"
test "$(printf '%s' "$REDIS_PASSWORD" | tr -d '\r\n')" = "$REDIS_PASSWORD"
redis_password_escaped=$(printf '%s' "$REDIS_PASSWORD" | sed 's/\\/\\\\/g;s/"/\\"/g')
umask 077
printf 'bind 0.0.0.0\nprotected-mode yes\nport 6379\nappendonly yes\ndir /data\nmaxmemory 128mb\nmaxmemory-policy noeviction\nrequirepass "%s"\n' "$redis_password_escaped" > /run/boyankb-redis.conf
chown redis:redis /run/boyankb-redis.conf
exec gosu redis /usr/local/bin/docker-entrypoint.sh redis-server /run/boyankb-redis.conf
