#!/bin/sh
set -eu
test "$(id -u)" = 0
. /etc/os-release
test "$ID" = ubuntu
test "$VERSION_ID" = 24.04
test "$(uname -m)" = x86_64
if test -e /opt/boyankb; then
    test -f /opt/boyankb/.deployment-target.json
    python3 -c 'import json; assert json.load(open("/opt/boyankb/.deployment-target.json")) == {"schema_version":1,"project":"boyankb-tencent","public_ip":"193.112.17.218"}'
else
    install -d -m 700 /opt/boyankb
    printf '%s\n' '{"schema_version":1,"project":"boyankb-tencent","public_ip":"193.112.17.218"}' > /opt/boyankb/.deployment-target.json
    chmod 600 /opt/boyankb/.deployment-target.json
fi
install -d -m 700 /opt/boyankb/backups /opt/boyankb/incoming
if ! command -v docker >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends ca-certificates curl gnupg
    install -d -m 755 /etc/apt/keyrings
    test ! -e /etc/apt/keyrings/boyankb-docker.asc
    curl -fsSL --connect-timeout 15 --max-time 120 --retry 2 https://mirrors.cloud.tencent.com/docker-ce/linux/ubuntu/gpg -o /etc/apt/keyrings/boyankb-docker.asc
    fingerprint=$(gpg --show-keys --with-colons /etc/apt/keyrings/boyankb-docker.asc 2>/dev/null | awk -F: '$1 == "fpr" {print $10; exit}')
    test "$fingerprint" = 9DC858229FC7DD38854AE2D88D81803C0EBFCD88
    chmod 644 /etc/apt/keyrings/boyankb-docker.asc
    test ! -e /etc/apt/sources.list.d/boyankb-docker.sources
    printf '%s\n' 'Types: deb' 'URIs: https://mirrors.cloud.tencent.com/docker-ce/linux/ubuntu' 'Suites: noble' 'Components: stable' 'Architectures: amd64' 'Signed-By: /etc/apt/keyrings/boyankb-docker.asc' > /etc/apt/sources.list.d/boyankb-docker.sources
    apt-get update
    docker_version=$(apt-cache policy docker-ce | awk '/Candidate:/ {print $2}')
    cli_version=$(apt-cache policy docker-ce-cli | awk '/Candidate:/ {print $2}')
    compose_version=$(apt-cache policy docker-compose-plugin | awk '/Candidate:/ {print $2}')
    containerd_version=$(apt-cache policy containerd.io | awk '/Candidate:/ {print $2}')
    apt-get install -y --no-install-recommends "docker-ce=$docker_version" "docker-ce-cli=$cli_version" "containerd.io=$containerd_version" "docker-compose-plugin=$compose_version"
    systemctl enable --now docker
fi
docker version --format '{{.Server.Version}}'
docker compose version
dpkg-query -W docker-ce docker-ce-cli containerd.io docker-compose-plugin > /opt/boyankb/host-packages.lock
chmod 600 /opt/boyankb/host-packages.lock
ip -4 route
