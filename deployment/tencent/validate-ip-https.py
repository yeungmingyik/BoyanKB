import ipaddress
import json
import os
import re
import subprocess
from pathlib import Path


def require(condition, code):
    if not condition:
        raise SystemExit(code)


def main():
    root = Path(__file__).resolve().parents[2]
    example = root / "deployment/tencent/env.example"
    variables = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", example.read_text(), re.M))
    variables.update({"PUBLIC_ORIGIN", "PUBLIC_HOST", "WEB_ALLOWED_CIDRS"})
    environment = {key: value for key, value in os.environ.items() if key not in variables and not key.startswith("COMPOSE_")}
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(example), "-f", str(root / "docker-compose.tencent.yml"), "-f", str(root / "docker-compose.tencent-ip-https.yml"), "--profile", "ip-https-init", "config", "--format", "json"],
        cwd=root,
        env=environment,
        capture_output=True,
        check=False,
    )
    require(result.returncode == 0, "COMPOSE_CONFIG_FAILED")
    configuration = json.loads(result.stdout)
    services = configuration["services"]
    require(len(services) == 10, "SERVICE_SET_MISMATCH")
    gateway = services["gateway"]
    expected_ports = {("127.0.0.1", "18080", 8090, "tcp"), ("0.0.0.0", "443", 443, "tcp")}
    ports = {(port["host_ip"], str(port["published"]), port["target"], port["protocol"]) for port in gateway["ports"]}
    require(ports == expected_ports, "GATEWAY_PORTS_MISMATCH")
    require(all(not service.get("ports") for name, service in services.items() if name != "gateway"), "NON_GATEWAY_PORT_EXPOSED")
    require(gateway["user"] == "65532:65532" and gateway["read_only"], "GATEWAY_USER_POLICY_MISMATCH")
    require(gateway["environment"]["PUBLIC_HOST"] == "193.112.17.218", "PUBLIC_HOST_MISMATCH")
    origin = "https://193.112.17.218"
    require(gateway["environment"]["PUBLIC_ORIGIN"] == origin, "PUBLIC_ORIGIN_MISMATCH")
    for key in ("APP_EXTERNAL_URL", "FRONTEND_BASE_URL"):
        require(services["app"]["environment"][key] == origin, "APP_ORIGIN_MISMATCH")
    ranges = gateway["environment"]["WEB_ALLOWED_CIDRS"].split()
    require(ranges == ["218.104.190.149/32"], "DEFAULT_WEB_ALLOWLIST_MISMATCH")
    require(all(not ipaddress.ip_network(value).is_unspecified for value in ranges), "UNRESTRICTED_WEB_ALLOWLIST")
    mounts = {mount["target"]: mount for mount in gateway["volumes"]}
    require(set(mounts) == {"/etc/caddy/Caddyfile", "/data", "/config"}, "GATEWAY_MOUNT_MISMATCH")
    require(mounts["/etc/caddy/Caddyfile"]["source"].replace("\\", "/").endswith("/deployment/tencent/Caddyfile.ip-https") and mounts["/etc/caddy/Caddyfile"]["read_only"], "HTTPS_CONFIG_MOUNT_MISMATCH")
    require(len(gateway["tmpfs"]) == 1 and gateway["tmpfs"][0].startswith("/tmp:"), "TLS_STORAGE_MUST_NOT_BE_TMPFS")
    for target, source in {"/data": "gateway-tls-data", "/config": "gateway-tls-config"}.items():
        require(mounts[target]["type"] == "volume" and mounts[target]["source"] == source and mounts[target]["volume"]["nocopy"], "TLS_STORAGE_MISMATCH")
    initializer = services["gateway-volume-init"]
    require(initializer["user"] == "0:0" and initializer["network_mode"] == "none" and initializer["read_only"] and initializer["profiles"] == ["ip-https-init"], "VOLUME_INITIALIZER_POLICY_MISMATCH")
    require(initializer["image"] == gateway["image"] and initializer["pull_policy"] == "never", "VOLUME_INITIALIZER_IMAGE_MISMATCH")
    require({mount["target"]: mount["source"] for mount in initializer["volumes"]} == {"/data": "gateway-tls-data", "/config": "gateway-tls-config"}, "VOLUME_INITIALIZER_SCOPE_MISMATCH")
    require(initializer["command"] == ["chown 65532:65532 /data /config && chmod 0700 /data /config"], "VOLUME_INITIALIZER_COMMAND_MISMATCH")
    base = (root / "deployment/tencent/Caddyfile").read_text()
    secured = (root / "deployment/tencent/Caddyfile.ip-https").read_text()
    body = base.split(":8090 {", 1)[1].rsplit("}", 1)[0]
    shared = secured.split("(boyankb_routes) {", 1)[1].split("\n}\n\n:8090", 1)[0]
    require(re.sub(r"\s+", " ", body).strip() == re.sub(r"\s+", " ", shared).strip(), "EXISTING_ROUTES_CHANGED")
    require("default_sni 193.112.17.218" in secured and "auto_https disable_redirects" in secured, "IP_TLS_POLICY_MISMATCH")
    require("profile shortlived" in secured and "disable_http_challenge" in secured and "issuer internal" not in secured and "tls internal" not in secured, "ACME_POLICY_MISMATCH")
    require("@denied not remote_ip {$WEB_ALLOWED_CIDRS:218.104.190.149/32}" in secured and "route {\n\t\trespond @denied 403\n\t\timport boyankb_routes" in secured, "WEB_ALLOWLIST_ORDER_MISMATCH")
    print(json.dumps({"status": "passed", "business_services": 9, "manual_volume_initializer": True, "public_port": "443/tcp", "diagnostic_port": "127.0.0.1:18080", "tls_storage_persistent": True, "existing_routes_preserved": True, "real_credentials_read": False, "business_services_started": False, "certificate_requested": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
