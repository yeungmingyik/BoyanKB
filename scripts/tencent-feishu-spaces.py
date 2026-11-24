"""Plan, provision and verify the three approved Feishu spaces in the existing cloud KB."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse


ENTRIES = {
    "7610776951925509072": "YCMAwDKPVijzdokgI5tcldTUnkd",
    "7610738738074766275": "Mo1zwVSDAiFwOZkYRgocMeGhnAg",
    "7610774192485370818": "HKq2wFHZxi9vx0klh3xcbrJWnPh",
}
FIRST = next(iter(ENTRIES))
EXISTING = "c32b43f4-41b3-4060-9820-9371bdafed8b"
SCHEDULE = "0 */30 * * * *"


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_space(wiki, client, space):
    # The legacy inventory uses a module-scoped guard. This CLI processes spaces serially.
    wiki.SPACE_ID = space
    nodes = wiki.inventory(client, report=False)
    if ENTRIES[space] not in nodes:
        raise ValueError("requested_entry_missing_from_root_inventory")
    info = client.request("/open-apis/wiki/v2/spaces/" + space)["data"]["space"]
    if info.get("space_id") != space or not info.get("name"):
        raise ValueError("unverified_space_metadata")
    unsupported = {}
    for token, item in nodes.items():
        kind = item["object_type"]
        if kind != "slides" and kind not in wiki.SUPPORTED_TYPES:
            # The native connector skips this object but still visits its children.
            unsupported[token] = kind
            continue
        row = client.request("/open-apis/wiki/v2/spaces/get_node?token=" + token)["data"]["node"]
        if (row.get("node_token"), row.get("space_id"), row.get("obj_type"), row.get("node_type")) != (token, space, kind, "origin") or row.get("origin_space_id") not in (None, "", space):
            raise ValueError("node_scope_or_type_changed")
        object_id = wiki.identifier(row.get("obj_token"))
        # The permission API accepts "export" for files too. The actual
        # Drive download is authorized again by Feishu during synchronization.
        for action in ("view", "export"):
            query = urllib.parse.urlencode({"type": kind, "action": action})
            data = client.request("/open-apis/drive/v1/permissions/" + object_id + "/members/auth?" + query)["data"]
            if data.get("auth_result") is not True:
                raise ValueError(action + "_denied:" + token)
            time.sleep(0.7)  # Keep per-document permission requests below the API quota.
    return {"name": info["name"], "nodes": sorted(nodes.keys() - unsupported.keys()), "unsupported": unsupported}


def remote(plan):
    import fcntl
    import os
    ops = load(Path("/opt/boyankb/scripts/tencent-accounts.py"))
    ops.host_guard()
    # Lock the existing directory inode so concurrent CLI runs cannot create duplicate sources.
    lock_fd = os.open(ops.ROOT, os.O_RDONLY)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    containers, address = ops.containers()
    client = ops.Client(address)
    client.token = client.login(ops.NEW_EMAILS[0], ops.credentials(ops.ROOT / ".env")["ADMIN_PASSWORD"])
    base = "/api/v1/datasource"
    fail = False
    for space, inventory in plan["spaces"].items():
        unsupported = inventory["unsupported"]
        if unsupported:
            print(json.dumps({"space": space, "unsupported_nodes": unsupported}), flush=True)
            fail = True
        if not inventory["nodes"]:
            continue
        sources = client.request("GET", base + "?kb_id=" + ops.KB)
        sources = [s for s in sources if s["type"] == "feishu" and (
            space in [r.split(":", 1)[0] for r in s["config"]["resource_ids"]]
            or s["config"].get("settings", {}).get("allowed_space_id") == space)]
        if len(sources) > 1:
            raise ValueError("overlapping_sources:" + space)
        source = sources[0] if sources else None
        if space == FIRST and (source or {}).get("id") != EXISTING:
            raise ValueError("original_source_missing_or_replaced")
        if source is None and not plan["apply"]:
            print(json.dumps({"space": space, "status": "would_create", "nodes": len(inventory["nodes"])}), flush=True)
            fail = True
            continue
        if source is None:
            payload = {"name": inventory["name"], "type": "feishu", "knowledge_base_id": ops.KB,
                       "config": {"type": "feishu", "resource_ids": [space], "settings": {"allowed_space_id": space, "timezone": "Asia/Shanghai"},
                                  "credentials": plan["credentials"]},
                       "status": "active", "sync_mode": "incremental", "sync_schedule": SCHEDULE,
                       "conflict_strategy": "overwrite", "sync_deletions": False, "sync_log_retention_days": 30}
            source = client.request("POST", base, payload, accepted=(201,))
        endpoint = base + "/" + source["id"]
        source = client.request("GET", endpoint)
        if (source["tenant_id"], source["knowledge_base_id"], source["config"]["resource_ids"],
            source["config"]["settings"]["allowed_space_id"], source["sync_mode"], source["sync_schedule"],
            source["conflict_strategy"], source["sync_deletions"]) != (ops.HQ, ops.KB, [space], space, "incremental", SCHEDULE, "overwrite", False):
            raise ValueError("existing_source_configuration_differs:" + space)
        if not source.get("credentials", {}).get("credentials", {}).get("configured") or source["status"] not in ("active", "error"):
            raise ValueError("source_paused_or_credentials_missing:" + space)
        expected = set(inventory["nodes"])
        query = """SELECT metadata->>'external_id' AS ext, metadata->>'space_id' AS space,
            metadata->>'source_resource_id' AS resource, parse_status AS status
            FROM knowledges WHERE deleted_at IS NULL AND knowledge_base_id={} AND metadata->>'datasource_id'={}"""
        query = ops.rows_sql(query.format(ops.sql_string(ops.KB), ops.sql_string(source["id"])))
        logs = client.request("GET", endpoint + "/logs?limit=1")
        latest = (logs or [{}])[0]
        deadline = time.monotonic() + plan["wait"]
        triggered = False
        while True:
            rows = ops.database(containers["postgres"], "SELECT " + query + ";")
            if any(r["space"] != space or r["resource"] != space or not r["ext"] for r in rows):
                raise ValueError("document_provenance_mismatch:" + space)
            ids = [r["ext"] for r in rows]
            if len(set(ids)) != len(ids):
                raise ValueError("duplicate_external_id:" + space)
            primary = {ext for ext in ids if "#" not in ext}
            missing = sorted(expected - primary)
            retained = sorted({ext.split("#", 1)[0] for ext in ids} - expected)
            pending = sum(r["status"] != "completed" for r in rows)
            parse_errors = sorted(r["ext"] for r in rows if r["status"] in ("failed", "cancelled"))
            verified = not missing and not pending and not retained and latest.get("status") == "success"
            if verified:
                source = client.request("GET", endpoint)
                verified = source["status"] == "active"
            needs_reparse = not missing and parse_errors and latest.get("status") == "success"
            if verified or needs_reparse or not plan["apply"] or space == FIRST or time.monotonic() >= deadline:
                break
            retryable_log = latest.get("status") in ("failed", "partial", "canceled")
            needs_sync = missing or not logs or source["status"] == "error" or retryable_log
            if not triggered and needs_sync and latest.get("status") != "running":
                latest = client.request("POST", endpoint + "/sync")
                triggered = True
            elif retryable_log:
                break
            time.sleep(5)
            latest = (client.request("GET", endpoint + "/logs?limit=1") or [{}])[0]
        print(json.dumps({"space": space, "id": source["id"], "source_status": source["status"],
                          "sync": latest.get("status"), "expected": len(expected), "documents": len(primary),
                          "missing": missing, "retained": retained, "incomplete_rows": pending, "parse_errors": parse_errors,
                          "supported_verified": verified, "verified": verified and not unsupported}), flush=True)
        fail |= not verified
    return int(fail)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-root", type=Path, required=True, help="existing local checkout containing .env and temp/tencent-cloud")
    parser.add_argument("--space", choices=list(ENTRIES), action="append")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=900)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    wiki = load(here / "feishu-space-sync.py")
    wiki.REPO_ROOT = args.credentials_root.resolve()
    settings = wiki.load_settings()
    client = wiki.FeishuClient(settings)
    plan = {"spaces": {}, "apply": args.apply, "wait": max(0, args.wait_seconds)}
    blocked = False
    for space in dict.fromkeys(args.space or ENTRIES):
        try:
            plan["spaces"][space] = inspect_space(wiki, client, space)
        except (wiki.SafeError, ValueError) as exc:
            print(json.dumps({"space": space, "status": "blocked", "reason": str(exc)}), flush=True)
            blocked = True
    if plan["spaces"]:
        if args.apply and any(s != FIRST for s in plan["spaces"]):
            plan["credentials"] = {"app_id": wiki.required(settings, "FEISHU_APP_ID"), "app_secret": wiki.required(settings, "FEISHU_APP_SECRET")}
        ssh = load(here / "tencent-ssh.py")
        ssh.ACCESS = wiki.REPO_ROOT / "temp/tencent-cloud/.env"
        ssh.KNOWN = ssh.ACCESS.parent / "known_hosts"
        # Data travels through stdin of host-key-pinned SSH, never in argv, files, or logs.
        wire = json.dumps(plan).encode() + b"\n" + Path(__file__).read_bytes()
        command = "python3 -B -c 'import json,sys; cloud_request=json.loads(sys.stdin.buffer.readline()); exec(sys.stdin.buffer.read())'"
        status = subprocess.run(ssh.ssh_argv(ssh.settings()) + [command], input=wire).returncode
        if status:
            return status
    return 1 if blocked else 0


if __name__ == "__main__":
    if "cloud_request" in globals():
        try:
            sys.exit(remote(cloud_request))
        except Exception as exc:
            print(type(exc).__name__, str(exc) if isinstance(exc, ValueError) or type(exc).__name__ == "HandoverError" else "cloud_check_failed")
            sys.exit(1)
    sys.exit(main())
