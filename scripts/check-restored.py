import argparse
import json
import re
import runpy
import subprocess
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = runpy.run_path(str(ROOT / "scripts/acceptance-prod.py"))


def docker_json(arguments):
    result = subprocess.run(["docker", *arguments], capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("DOCKER_CHECK_FAILED")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--restore-report", required=True)
    parser.add_argument("--ca-file", required=True)
    parser.add_argument("--base-url", default="https://localhost:9443")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists() or not re.fullmatch(r"boyankb-restore-[a-z0-9-]+", args.project):
        raise RuntimeError("NEW_REPORT_AND_RESTORE_PROJECT_REQUIRED")
    if API["origin"](args.base_url) != "https://localhost:9443":
        raise RuntimeError("ISOLATED_RESTORE_ORIGIN_REQUIRED")
    fixtures = json.loads(Path(args.fixtures).read_text(encoding="utf-8"))
    restore = json.loads(Path(args.restore_report).read_text(encoding="utf-8"))
    if fixtures.get("schema") != 1 or restore.get("project") != args.project:
        raise RuntimeError("REPORT_SCOPE_MISMATCH")
    uploaded = fixtures.get("spaces", {}).get("a", {})
    if not uploaded.get("file_knowledge_id") or not uploaded.get("file_marker"):
        raise RuntimeError("UPLOADED_FILE_FIXTURE_REQUIRED")
    checks = []

    def check(name, passed):
        checks.append({"name": name, "status": "PASS" if passed else "FAIL"})
        print(checks[-1]["status"], name, flush=True)
        if not passed:
            raise RuntimeError("RESTORED_CHECK_FAILED")

    result = subprocess.run(["docker", "network", "ls", "--filter", "label=com.docker.compose.project=" + args.project, "--format", "{{.ID}}"], capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError("NETWORK_LIST_FAILED")
    networks = result.stdout.decode().split()
    inspected = docker_json(["network", "inspect", *networks]) if networks else []
    check("restore.all_networks_internal", len(inspected) == 4 and all(n.get("Internal") for n in inspected))
    network_ids = {network["Id"] for network in inspected}
    result = subprocess.run(["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=" + args.project], capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError("CONTAINER_LIST_FAILED")
    ids = result.stdout.decode().split()
    containers = docker_json(["inspect", *ids]) if ids else []
    services = {container["Config"]["Labels"].get("com.docker.compose.service") for container in containers}
    check("restore.seven_services", len(containers) == 7 and services == {"app", "frontend", "caddy", "postgres", "redis", "ollama", "docreader"})
    for container in containers:
        service = container["Config"]["Labels"]["com.docker.compose.service"]
        attached = list(container["NetworkSettings"].get("Networks", {}).values())
        check("restore." + service + ".network_binding", bool(attached) and all(n["NetworkID"] in network_ids for n in attached))
        ports = container["HostConfig"].get("PortBindings") or {}
        expected = {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9089"}], "443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9443"}]} if service == "caddy" else {}
        check("restore." + service + ".port_binding", ports == expected)
    check("restore.environment_exact_match", restore.get("environment_exact_match") is True)
    check("restore.caddy_and_redis_verified", restore.get("caddy_volumes_verified") is True and restore.get("redis_rdb_verified") is True)
    check("restore.nonempty_object_backup", restore.get("object_files", 0) > 0)
    env = API["read_environment"](args.env_file)
    client = API["Client"](args.base_url, args.ca_file)
    status, body = client.request("/api/v1/auth/login", "POST", {"email": env["ACCEPTANCE_ADMIN_EMAIL"], "password": env["ACCEPTANCE_ADMIN_PASSWORD"]})
    check("restore.admin_login", status == 200 and bool(body.get("token")))
    admin = {"token": body["token"]}
    status, body = client.request("/api/v1/auth/me", principal=admin)
    check("restore.admin_role", status == 200 and (API["data"](body) or {}).get("user", {}).get("is_system_admin") is True)
    for label in ("a", "b"):
        item = fixtures["spaces"][label]
        principal = dict(admin, tenant_id=item["tenant_id"])
        for field, prefix in (("kb_id", "/api/v1/knowledge-bases/"), ("knowledge_id", "/api/v1/knowledge/")):
            status, body = client.request(prefix + API["identifier"](item[field]), principal=principal)
            check("restore." + label + "." + field, status == 200 and str((API["data"](body) or {}).get("id")) == item[field])
        status, body = client.request("/api/v1/chunks/" + item["knowledge_id"] + "?page_size=100", principal=principal)
        check("restore." + label + ".chunk_content", status == 200 and any(item["canary"] in row.get("content", "") for row in API["rows"](body)))
        for mode in ("keyword", "vector"):
            payload = {"query_text": item["canary"], "match_count": 10, "keyword_threshold": 0, "vector_threshold": 0, "disable_vector_match": mode == "keyword", "disable_keywords_match": mode == "vector"}
            status, body = client.request("/api/v1/knowledge-bases/" + item["kb_id"] + "/hybrid-search", "POST", payload, principal)
            check("restore." + label + "." + mode + "_retrieval", status == 200 and any(row.get("knowledge_id") == item["knowledge_id"] and item["canary"] in row.get("content", "") for row in API["rows"](body)))
        if item.get("file_knowledge_id"):
            request = urllib.request.Request(args.base_url + "/api/v1/knowledge/" + API["identifier"](item["file_knowledge_id"]) + "/download", headers={"Authorization": "Bearer " + admin["token"], "X-Tenant-ID": str(item["tenant_id"])})
            with client.opener.open(request, timeout=60) as response:
                content = response.read(1024 * 1024)
                check("restore." + label + ".source_file_download", response.status == 200 and item["file_marker"].encode() in content)
    result = {"status": "PASS", "project": args.project, "checks": checks, "llm_called": False, "external_sync_called": False, "scope": "restored_admin_login_synthetic_records_files_keyword_vector_retrieval"}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as destination:
        json.dump(result, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else "RESTORED_CHECK_INCOMPLETE")
        sys.exit(1)
