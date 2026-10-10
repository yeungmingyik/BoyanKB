#!/bin/sh
set -eu

for file in /etc/nginx/templates/default.conf.template /etc/nginx/api-proxy.conf; do
    sed -i 's/proxy_set_header X-Forwarded-Proto \$scheme;/proxy_set_header X-Forwarded-Proto $boyan_forwarded_proto;/g' "$file"
    sed -i 's/proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;/proxy_set_header X-Forwarded-For $remote_addr;/g' "$file"
done
printf '%s\n' 'set_real_ip_from 10.239.80.2;' 'real_ip_header X-Forwarded-For;' 'real_ip_recursive on;' 'map "$realip_remote_addr:$http_x_forwarded_proto" $boyan_forwarded_proto {' '    default $scheme;' '    "10.239.80.2:http" http;' '    "10.239.80.2:https" https;' '}' > /etc/nginx/conf.d/01-tencent-proxy.conf
exec /docker-entrypoint.sh
