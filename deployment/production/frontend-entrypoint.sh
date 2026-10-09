#!/bin/sh
set -eu

case "${PROD_PROXY_IP:-}" in
  ''|*[!0-9.]*) exit 2 ;;
esac
for file in /etc/nginx/templates/default.conf.template /etc/nginx/api-proxy.conf; do
  sed -i 's/proxy_set_header X-Forwarded-Proto \$scheme;/proxy_set_header X-Forwarded-Proto https;/g' "$file"
done
printf 'set_real_ip_from %s;\nreal_ip_header X-Forwarded-For;\nreal_ip_recursive on;\n' "$PROD_PROXY_IP" > /etc/nginx/conf.d/01-prod-realip.conf
exec /docker-entrypoint.sh
