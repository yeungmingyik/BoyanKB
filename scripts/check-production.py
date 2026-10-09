import argparse
import hashlib
import json
import re
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run(command):
    process = subprocess.run(command, capture_output=True, cwd=ROOT)
    if process.returncode:
        raise RuntimeError("COMMAND_FAILED")
    return process.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--compose", action="append", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ca-file")
    parser.add_argument("--http-url")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise RuntimeError("REPORT_ALREADY_EXISTS")
    origin = urllib.parse.urlsplit(args.base_url)
    if origin.scheme != "https" or origin.username or origin.password or origin.query or origin.fragment:
        raise RuntimeError("HTTPS_ORIGIN_REQUIRED")
    compose = ["docker", "compose", "--env-file", str(Path(args.env_file).resolve()), "-p", args.project]
    for file in args.compose:
        compose += ["-f", file]
    config = json.loads(run(compose + ["config", "--format", "json"]))
    lock = json.loads((ROOT / "deployment/production/images.lock.json").read_text())
    checks = []

    def check(name, passed):
        checks.append({"name": name, "status": "PASS" if passed else "FAIL"})
        print(checks[-1]["status"], name)

    check("project", config.get("name") == args.project)
    check("services.locked", set(config["services"]) == set(lock["images"]))
    for name, service in config["services"].items():
        check(name + ".image", service.get("image") == lock["images"].get(name))
        check(name + ".no_build", "build" not in service)
        check(name + ".no_host_runtime", not service.get("privileged") and service.get("network_mode") != "host" and not service.get("devices") and all("docker.sock" not in str(v) for v in service.get("volumes", [])))
        ports = service.get("ports", [])
        check(name + ".published_ports", all(p["target"] in (80, 443) for p in ports) if name == "caddy" else not ports)
        if name in ("postgres", "redis", "docreader", "frontend"):
            check(name + ".private_networks", all(config["networks"][network].get("internal") for network in service.get("networks", {})))
        check(name + ".readonly_configuration", all(m.get("read_only") for m in service.get("volumes", []) if m["type"] == "bind"))
        ids = run(compose + ["ps", "-q", name]).decode().split()
        check(name + ".single_running_container", len(ids) == 1)
        if len(ids) != 1:
            continue
        container = json.loads(run(["docker", "inspect", ids[0]]))[0]
        state = container["State"]
        host = container["HostConfig"]
        check(name + ".running", state["Running"] and state.get("Health", {}).get("Status", "healthy") == "healthy")
        actual_image = json.loads(run(["docker", "image", "inspect", service["image"]]))[0]["Id"]
        check(name + ".image_running", container["Image"] == actual_image)
        check(name + ".limits", host.get("Memory", 0) > 0 and host.get("NanoCpus", 0) > 0 and 0 < host.get("PidsLimit", 0) <= 256)
        check(name + ".no_new_privileges", "no-new-privileges:true" in host.get("SecurityOpt", []))
        check(name + ".capabilities", "ALL" in host.get("CapDrop", []) and not ({"SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "ALL"} & set(host.get("CapAdd") or [])))
        check(name + ".port_bindings", not host.get("PortBindings") if name != "caddy" else set(host.get("PortBindings", {})) <= {"80/tcp", "443/tcp"})
        if name == "app":
            environment = dict(item.split("=", 1) for item in container["Config"]["Env"] if "=" in item)
            expected = {"DISABLE_REGISTRATION": "true", "OIDC_AUTH_ENABLE": "false", "WEKNORA_TENANT_ENABLE_RBAC": "true", "WEKNORA_TENANT_ENABLE_CROSS_TENANT_ACCESS": "false", "WEKNORA_TENANT_SELF_SERVICE_CREATION_ENABLED": "false", "WEKNORA_SANDBOX_DOCKER_ENABLED": "false", "AUTO_MIGRATE": "false", "AUTO_RECOVER_DIRTY": "false", "LLM_DEBUG_LOG": "false", "SSRF_DNS_WHITELIST_ONLY": "true", "DOCKER_HOST": "", "BROWSERSKILL_BINARY": ""}
            for key, value in expected.items():
                check("app.environment." + key, environment.get(key) == value)
            check("app.master_key_length", len(environment.get("SYSTEM_AES_KEY", "").encode()) == 32)
            check("app.jwt_secret_length", len(environment.get("JWT_SECRET", "")) >= 32)
            status = run(["docker", "exec", ids[0], "cat", "/proc/1/status"]).decode()
            uid = re.search(r"^Uid:\s+(\d+)", status, re.MULTILINE)
            check("app.nonroot_process", bool(uid and uid[1] != "0"))
        if name == "ollama":
            digest = run(["docker", "exec", ids[0], "sha256sum", lock["embedding"]["manifest"]]).decode().split()[0]
            check("embedding.pinned_manifest", digest == lock["embedding"]["sha256"])
    context = ssl.create_default_context(cafile=args.ca_file)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), NoRedirect())

    def request(path, method="GET", data=None):
        req = urllib.request.Request(args.base_url.rstrip("/") + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            response = opener.open(req, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    code, headers, body = request("/")
    check("tls.validated_homepage", code == 200 and b"BoyanKB" in body)
    check("tls.hsts", "max-age=" in headers.get("Strict-Transport-Security", ""))
    check("tls.nosniff", headers.get("X-Content-Type-Options") == "nosniff")
    code, _, body = request("/favicon.ico")
    check("branding.favicon", code == 200 and hashlib.sha256(body).digest() == hashlib.sha256((ROOT / "branding/favicon.ico").read_bytes()).digest())
    code, _, body = request("/licenses/LICENSE")
    check("upstream.license", code == 200 and body == (ROOT / "LICENSE").read_bytes())
    code, _, _ = request("/api/v1/auth/register", "POST", b"{}")
    check("edge.registration_blocked", code == 403)
    for path in ["/swagger/index.html", "/metrics", "/api/v1/local-browser/internal", "/api/v1/auth/oidc/login"]:
        check("edge.blocked." + path, request(path)[0] == 404)
    check("edge.sandbox_blocked", request("/api/v1/sessions/production-probe/sandbox/terminal")[0] == 403)
    check("edge.backend_creation_blocked", request("/api/v1/sandbox-configs", "POST", b"{}")[0] == 403)
    if args.http_url:
        try:
            response = opener.open(args.http_url, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            check("http.redirect_https", response.status in (301, 302, 307, 308) and response.headers.get("Location", "").startswith(args.base_url))
    result = {"status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL", "project": args.project, "checks": checks, "manual": ["需要人工验证：阿里云安全组、ECS 主机防火墙、公共 DNS 与公网可信证书。", "需要人工验证：生产并发容量、故障告警、跨主机故障及恢复时间目标。"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else "PRODUCTION_CHECK_INCOMPLETE")
        raise SystemExit(2)
