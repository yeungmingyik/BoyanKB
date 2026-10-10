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
    environment = {key: value for key, value in os.environ.items() if key not in variables and not key.startswith("COMPOSE_")}
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(example), "-f", str(root / "docker-compose.tencent.yml"), "config", "--format", "json"],
        cwd=root,
        env=environment,
        capture_output=True,
        check=False,
    )
    require(result.returncode == 0, "COMPOSE_CONFIG_FAILED")
    configuration = json.loads(result.stdout)
    services = configuration["services"]
    expected = {"gateway", "frontend", "app", "docreader", "postgres", "redis", "ollama", "document-renderer", "neo4j"}
    require(set(services) == expected, "SERVICE_SET_MISMATCH")
    lock = json.loads((root / "deployment/tencent/images.lock.json").read_text())
    references = {item["tag"] + "@" + item["id"] for item in lock["images"]}
    require({service["image"] for service in services.values()} == references, "IMAGE_LOCK_MISMATCH")
    ports = [(name, port) for name, service in services.items() for port in service.get("ports", [])]
    require(len(ports) == 1, "PUBLISHED_PORT_COUNT_INVALID")
    name, port = ports[0]
    require(name == "gateway" and port["host_ip"] == "127.0.0.1" and str(port["published"]) == "18080" and port["target"] == 8090, "LOOPBACK_GATEWAY_REQUIRED")
    require(all(not service.get("build") and not service.get("privileged") and not service.get("env_file") for service in services.values()), "UNSAFE_SERVICE_CONFIG")
    require(all(service["pull_policy"] == "never" and service["platform"] == "linux/amd64" for service in services.values()), "IMAGE_EXECUTION_POLICY_INVALID")
    require(all("no-new-privileges:true" in service["security_opt"] for service in services.values()), "NO_NEW_PRIVILEGES_REQUIRED")
    require(all(int(service.get("mem_limit", 0)) > 0 and float(service.get("cpus", 0)) > 0 and int(service.get("pids_limit", 0)) > 0 for service in services.values()), "RESOURCE_LIMIT_REQUIRED")
    memory = sum(int(service["mem_limit"]) for service in services.values())
    require(memory <= 7 * 1024**3, "MEMORY_BUDGET_EXCEEDED")
    for network in ("web", "backend", "graph", "render"):
        require(configuration["networks"][network]["internal"], "INTERNAL_NETWORK_REQUIRED")
    require(set(services["neo4j"]["networks"]) == {"graph"}, "GRAPH_NETWORK_ISOLATION_REQUIRED")
    require(set(services["document-renderer"]["networks"]) == {"render"}, "RENDER_NETWORK_ISOLATION_REQUIRED")
    require(all(set(services[name]["networks"]) == {"backend"} for name in ("postgres", "redis", "ollama", "docreader")), "BACKEND_NETWORK_ISOLATION_REQUIRED")
    app = services["app"]["environment"]
    for key, value in {"DISABLE_REGISTRATION": "true", "AUTO_MIGRATE": "false", "AUTO_RECOVER_DIRTY": "false", "WEKNORA_SANDBOX_DOCKER_ENABLED": "false", "NEO4J_ENABLE": "true", "WEKNORA_TRUSTED_PROXIES": "10.239.80.3/32", "BOYAN_DOCUMENT_RENDERER_URL": "http://document-renderer:8091"}.items():
        require(app[key] == value, "APP_POLICY_MISMATCH_" + key)
    for service in services.values():
        for mount in service.get("volumes", []):
            require("docker.sock" not in mount["source"] and "docker.sock" not in mount["target"], "DOCKER_SOCKET_FORBIDDEN")
            require(mount["type"] != "bind" or mount.get("read_only"), "WRITABLE_HOST_BIND_FORBIDDEN")
    caddy = (root / "deployment/tencent/Caddyfile").read_text()
    require("{http.request.header.Cf-Connecting-Ip}" not in caddy and "header_up X-Forwarded-For {remote_host}" in caddy, "UNTRUSTED_CLIENT_IP_HEADER")
    require("^/api/v1/skills/catalog(/[a-zA-Z0-9_-]+(/files(/content)?)?)?$" in caddy, "SKILL_CATALOG_ROUTE_REQUIRED")
    require("respond @registration 403" in caddy and "respond @skillExecution 404" in caddy and "respond @sandbox 403" in caddy, "GATEWAY_BOUNDARY_MISMATCH")
    print(json.dumps({"status": "passed", "services": len(services), "locked_images": len(references), "memory_limit_mib": memory // 1024**2, "entrypoint": "127.0.0.1:18080", "business_services_started": False, "real_credentials_read": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
