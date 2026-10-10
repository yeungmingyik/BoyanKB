import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "weknora-local"
HQ = 10000
SOURCE_KB = "d9285ed1-0760-474b-ba82-a13347598176"
TENANTS = {10000, 10001, 10002, 10003, 10004}
SERVICES = {"app", "frontend", "postgres", "redis", "docreader", "ollama", "document-renderer"}
PARTNER_KEYS = {"boyan-headquarters", "boyan-presets-demo-a", "boyan-presets-demo-b"}
BASELINE = ROOT / "temp/partner-delivery/audit-headquarters-ready.json"
ENV_FILE = ROOT / "temp/enterprise-trial/.env"
POLICIES = {"auth.registration_mode": "invite_only", "auth.default_tenant_mode": "tenantless", "tenant.self_service_creation_enabled": False}
SOFT_TABLES = {
    "users": "users", "members": "tenant_members", "agents": "custom_agents",
    "knowledge_bases": "knowledge_bases", "knowledges": "knowledges", "tenants": "tenants",
    "organizations": "organizations", "kb_shares": "kb_shares", "agent_shares": "agent_shares",
    "embed_channels": "embed_channels", "skill_catalog": "tenant_skill_catalog",
    "sandbox_policies": "tenant_sandbox_configs", "storage_backends": "storage_backends",
}


class TrialError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise TrialError(code)


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


AUDIT = module("trial_delivery_audit", "audit-delivery.py")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def file_digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_json(path, expected_hash=None):
    raw = Path(path).read_bytes()
    if expected_hash is not None:
        require(isinstance(expected_hash, str) and re.fullmatch(r"[0-9a-f]{64}", expected_hash) is not None and digest(raw) == expected_hash, "file_digest_mismatch")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise TrialError("invalid_json_file") from None


def report_path(path):
    path = Path(path).resolve()
    require(path.is_relative_to(ROOT / "temp/enterprise-trial") and path.suffix == ".json", "report_path_outside_trial")
    require(not path.exists(), "report_already_exists")
    return path


def write_json(path, value):
    path = report_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def credentials(path):
    require(Path(path).resolve() == ENV_FILE.resolve(), "trial_env_path_required")
    values = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key not in {"TRIAL_ADMIN_EMAIL", "TRIAL_ADMIN_NAME", "TRIAL_ADMIN_PASSWORD"}:
            continue
        require(key not in values, "duplicate_trial_credential")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    require(set(values) == {"TRIAL_ADMIN_EMAIL", "TRIAL_ADMIN_NAME", "TRIAL_ADMIN_PASSWORD"}, "trial_credentials_missing")
    require(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", values["TRIAL_ADMIN_EMAIL"]) is not None, "trial_email_invalid")
    require(2 <= len(values["TRIAL_ADMIN_NAME"]) <= 50, "trial_name_invalid")
    password = values["TRIAL_ADMIN_PASSWORD"]
    require(12 <= len(password) <= 32 and re.search("[a-z]", password) and re.search("[A-Z]", password) and re.search("[0-9]", password) and re.search(r"[^A-Za-z0-9\s]", password), "strong_trial_password_required")
    return values


def identity_hash(values):
    return digest(canonical({"email": values["TRIAL_ADMIN_EMAIL"].lower(), "name": values["TRIAL_ADMIN_NAME"]}))


def checked_run(command, input=None, timeout=120):
    try:
        completed = subprocess.run(command, input=input, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise TrialError("local_command_failed_outcome_unknown") from None
    require(completed.returncode == 0, "local_command_failed_outcome_unknown")
    return completed.stdout


def local_containers():
    host = os.environ.get("DOCKER_HOST", "")
    require(not host, "explicit_docker_host_forbidden")
    context = checked_run(["docker", "context", "show"]).strip()
    endpoint = json.loads(checked_run(["docker", "context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"] ))
    require(isinstance(endpoint, str) and endpoint.startswith(("npipe://", "unix://")), "remote_docker_forbidden")
    os.environ["DOCKER_CONTEXT"] = context
    ids = checked_run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT]).split()
    require(len(ids) == len(SERVICES), "exact_seven_local_containers_required")
    found = {}
    for container_id in ids:
        parts = checked_run(["docker", "inspect", container_id, "--format", '{{json .Id}}\n{{json .Config.Labels}}\n{{json .State.Status}}\n{{json .NetworkSettings.Ports}}']).splitlines()
        require(len(parts) == 4, "invalid_container_metadata")
        metadata, labels, status, ports = [json.loads(part) for part in parts]
        service = labels.get("com.docker.compose.service")
        require(labels.get("com.docker.compose.project") == PROJECT and service in SERVICES and service not in found and status == "running", "container_scope_or_state_mismatch")
        require(re.fullmatch(r"[0-9a-f]{64}", metadata) is not None, "container_id_invalid")
        if service == "app":
            require(any(p.get("HostPort") == "8080" for p in (ports.get("8080/tcp") or [])), "local_app_port_mismatch")
        found[service] = metadata
    require(set(found) == SERVICES, "local_services_mismatch")
    return found


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, token, apply=False, base="http://127.0.0.1:8080"):
        require(base == "http://127.0.0.1:8080", "fixed_local_api_required")
        require(isinstance(token, str) and len(token) > 20 and "\n" not in token, "operator_token_required")
        self.token, self.apply, self.base = token, apply, base
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, payload=None, token=None, accepted=(200,)):
        require(path.startswith("/api/v1/") and not any(c in path for c in "\r\n"), "invalid_api_path")
        require(method == "GET" or self.apply, "apply_required")
        request = urllib.request.Request(self.base + path, data=canonical(payload) if payload is not None else None, method=method, headers={"Authorization": "Bearer " + (self.token if token is None else token), "X-Tenant-ID": str(HQ), "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=60) as response:
                require(response.status in accepted, "unexpected_api_status")
                value = json.loads(response.read(4 * 1024 * 1024))
        except urllib.error.HTTPError as error:
            if error.code in accepted:
                return {"http_status": error.code}
            raise TrialError("api_http_" + str(error.code)) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise TrialError("api_network_failed_outcome_unknown") from None
        except (ValueError, UnicodeError):
            raise TrialError("api_response_invalid") from None
        require(not isinstance(value, dict) or value.get("success") is not False, "api_failed")
        return value


def owner_identity(client, token=None, expected_id=None):
    body = client.request("GET", "/api/v1/auth/me", token=token)
    data = body.get("data", {})
    user = data.get("user", {})
    require(user.get("is_active") is True and user.get("is_system_admin") is True and not user.get("can_access_all_tenants"), "active_restricted_system_admin_required")
    require(not expected_id or user.get("id") == expected_id, "new_admin_identity_mismatch")
    require(data.get("tenant", {}).get("id") == HQ, "headquarters_context_required")
    require(any(m.get("tenant_id") == HQ and m.get("role") == "owner" for m in data.get("memberships", [])), "headquarters_owner_required")
    client.request("GET", "/api/v1/system/admin/list", token=token)
    return str(uuid.UUID(user["id"]))


def policies(client):
    for key, value in POLICIES.items():
        result = client.request("GET", "/api/v1/system/admin/settings/" + key)
        require(result.get("value") == value and type(result.get("value")) is type(value), "trial_policy_required_" + key)


def row_ids(rows, key="id"):
    return sorted(row[key] for row in rows)


def validate_baseline(baseline):
    require(baseline.get("project") == PROJECT and baseline.get("read_only") is True, "baseline_scope_invalid")
    data = baseline.get("data", {})
    require(set(data) == set(AUDIT.QUERIES), "baseline_shape_invalid")
    require(set(row_ids(data["tenants"])) == TENANTS, "unexpected_tenant_ids")
    require(len(data["users"]) == 3 and len(data["agents"]) == 11 and len(data["knowledge_bases"]) == 5 and len(data["document_jobs"]) == 50, "approved_inventory_size_mismatch")
    require(set(row_ids(data["partner_jobs"], "partner_key")) == PARTNER_KEYS, "unexpected_provision_jobs")
    require(all(row["tenant_id"] in TENANTS for key in ("agents", "knowledge_bases", "knowledges", "document_jobs", "storage_backends", "models") for row in data[key]), "inventory_outside_approved_tenants")
    source = [row for row in data["knowledge_bases"] if row["id"] == SOURCE_KB and row["tenant_id"] == HQ and not row["deleted_at"]]
    documents = [row for row in data["knowledges"] if row["knowledge_base_id"] == SOURCE_KB and not row["deleted_at"]]
    require(len(source) == 1 and len(documents) == 19 and all(row["parse_status"] == "completed" and row["tenant_id"] == HQ for row in documents), "source_nineteen_completed_required")
    require(len(data["data_sources"]) == 1 and data["data_sources"][0]["knowledge_base_id"] == SOURCE_KB and data["data_sources"][0]["tenant_id"] == HQ, "source_datasource_mismatch")
    require(all(row["tenant_id"] == HQ for row in data["models"]), "model_outside_headquarters")
    require(all(row["total"] == 0 for row in data["other_counts"] if row["resource"] in {"api_keys", "native_skills", "skill_snapshots", "memory_items", "im_channels", "organization_join_requests", "message_artifacts"}), "unapproved_independent_resource")
    require(not data["invitations"] and not data["agent_shares"], "unapproved_invitation_or_agent_share")
    return data


def retirement(data):
    result = {key: row_ids(data[key]) for key in SOFT_TABLES}
    result["tenants"] = sorted(TENANTS - {HQ})
    result["knowledge_bases"] = [value for value in result["knowledge_bases"] if value != SOURCE_KB]
    result["knowledges"] = row_ids([r for r in data["knowledges"] if r["knowledge_base_id"] != SOURCE_KB])
    for key in ("sandbox_policies", "storage_backends"):
        result[key] = row_ids([r for r in data[key] if r["tenant_id"] != HQ])
    result["organization_members"] = row_ids(data["organization_members"])
    result["partner_keys"] = row_ids(data["partner_jobs"], "partner_key")
    result["document_jobs"] = row_ids(data["document_jobs"])
    return result


def compare_inventory(current, baseline, new_user_id=None):
    filtered = dict(current)
    if new_user_id:
        filtered["users"] = [r for r in current["users"] if r["id"] != new_user_id]
        filtered["members"] = [r for r in current["members"] if r["user_id"] != new_user_id]
        new_members = [r for r in current["members"] if r["user_id"] == new_user_id]
        require(all(r["tenant_id"] == HQ and r["role"] == "owner" and r["status"] == "active" and not r["deleted_at"] for r in new_members), "unexpected_new_admin_membership")
    require(filtered == baseline, "inventory_changed_requires_review")


def make_plan(data, source_ids, baseline_hash, admin_hash, operator_id):
    require(operator_id in row_ids(data["users"]), "operator_outside_baseline")
    return {"format": "boyankb-trial-admin-plan-v1", "project": PROJECT, "source_ids": source_ids,
            "baseline_sha256": baseline_hash, "admin_identity_sha256": admin_hash, "operator_user_id": operator_id,
            "keep_tenant_id": HQ, "keep_knowledge_base_id": SOURCE_KB,
            "keep_knowledge_ids": row_ids([r for r in data["knowledges"] if r["knowledge_base_id"] == SOURCE_KB]),
            "retire": retirement(data), "policies": POLICIES,
            "retained_history_counts": {key: len(data[key]) for key in ("sessions", "resources", "resource_bindings")},
            "retained_history": ["messages", "sessions", "physical_files", "retired_knowledge_chunks", "retired_knowledge_embeddings"],
            "requires_verified_backup_and_restore": True, "reinstall_presets": False}


def recent(value, now):
    try:
        timestamp = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return timestamp.tzinfo is not None and 0 <= (now - timestamp).total_seconds() <= 86400
    except (ValueError, TypeError, AttributeError):
        return False


def backup_gate(receipt, restored, archive, source_ids, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    require(receipt.get("format") == "boyankb-trial-backup-receipt-v1" and receipt.get("status") == "verified" and receipt.get("profile") == "local-renderer-v1", "verified_trial_backup_required")
    require(receipt.get("source_project") == PROJECT and receipt.get("consistency") == "application-quiesced" and receipt.get("writers_resumed") is True, "backup_consistency_required")
    require(receipt.get("source_ids") == source_ids and set(receipt.get("service_names", [])) == SERVICES, "backup_source_ids_mismatch")
    require(recent(receipt.get("created_at"), now) and recent(restored.get("created_at"), now), "backup_or_restore_expired")
    require(restored.get("format") == "boyankb-trial-restore-report-v1" and restored.get("status") == "verified" and restored.get("source_project") == PROJECT and str(restored.get("target_project", "")).startswith("boyankb-restore-trial-"), "verified_isolated_restore_required")
    require(all(restored.get(key) is True for key in ("database_verified", "files_verified", "redis_verified", "environment_verified", "runtime_assets_verified")) and restored.get("application_started") is False, "restore_components_not_verified")
    require(re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("database_fingerprint", ""))) is not None, "backup_database_fingerprint_required")
    size = Path(archive).stat().st_size
    checksum = file_digest(archive)
    require(size > 0 and receipt.get("archive_size") == size == restored.get("archive_size") and receipt.get("archive_sha256") == checksum == restored.get("archive_sha256"), "backup_archive_mismatch")
    return checksum


def sql_literal(value):
    require("\x00" not in str(value), "invalid_sql_literal")
    return "'" + str(value).replace("'", "''") + "'"


def sql_ids(values):
    return ",".join(sql_literal(value) for value in values) or "NULL"


def inventory_sql(new_user_id):
    parts = []
    for key, query in AUDIT.QUERIES.items():
        condition = ""
        if key in ("users", "members"):
            condition = " WHERE q." + ("id" if key == "users" else "user_id") + " <> " + sql_literal(new_user_id)
        parts.append(sql_literal(key) + ",(SELECT coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) FROM (" + query + ") q" + condition + ")")
    return "jsonb_build_object(" + ",".join(parts) + ")"


def protected_sql():
    kb = sql_literal(SOURCE_KB)
    queries = {
        "tenant": ("tenants", "id=10000", "to_jsonb(q)"),
        "kb": ("knowledge_bases", "id=" + kb, "to_jsonb(q)-'creator_id'-'updated_at'"),
        "knowledges": ("knowledges", "knowledge_base_id=" + kb, "to_jsonb(q)"),
        "chunks": ("chunks", "knowledge_base_id=" + kb, "to_jsonb(q)"),
        "embeddings": ("embeddings", "knowledge_base_id=" + kb, "to_jsonb(q)"),
        "data_sources": ("data_sources", "knowledge_base_id=" + kb, "to_jsonb(q)"),
        "models": ("models", "tenant_id=10000", "to_jsonb(q)"),
        "storage": ("storage_backends", "tenant_id=10000", "to_jsonb(q)"),
        "resources": ("resources", "TRUE", "to_jsonb(q)"),
        "bindings": ("resource_bindings", "TRUE", "to_jsonb(q)"),
    }
    return "jsonb_build_object(" + ",".join(sql_literal(key) + ",(SELECT coalesce(jsonb_agg(" + expression + " ORDER BY q.id),'[]'::jsonb) FROM " + table + " q WHERE " + condition + ")" for key, (table, condition, expression) in queries.items()) + ")"


def retirement_sql(data, new_user_id):
    new_id = str(uuid.UUID(new_user_id))
    require(new_id not in row_ids(data["users"]), "cannot_retire_new_admin")
    require("$trial$" not in canonical(data).decode("utf-8"), "unsupported_inventory_delimiter")
    scope = retirement(data)
    old = sql_ids(scope["users"])
    actor = sql_literal(new_id)
    tables = set(SOFT_TABLES.values()) | {"auth_tokens", "organization_tenant_members", "partner_provisioning_jobs", "boyan_document_jobs", "models", "data_sources", "chunks", "embeddings", "resources", "resource_bindings", "messages", "sessions", "tenant_api_keys", "tenant_skills", "tenant_skill_snapshots", "memory_items", "im_channels", "organization_join_requests", "message_artifacts", "tenant_invitations", "system_settings"}
    statements = ["BEGIN;", "SET LOCAL standard_conforming_strings=on;", "SET LOCAL lock_timeout='10s';", "SET LOCAL statement_timeout='120s';", "LOCK TABLE " + ",".join(sorted(tables)) + " IN SHARE ROW EXCLUSIVE MODE;", "DO $trial$ BEGIN IF " + inventory_sql(new_id) + " <> " + sql_literal(canonical(data).decode("utf-8")) + "::jsonb THEN RAISE EXCEPTION 'inventory_changed'; END IF; END $trial$;", "DO $trial$ BEGIN IF NOT EXISTS (SELECT 1 FROM users u JOIN tenant_members m ON m.user_id=u.id WHERE u.id=" + actor + " AND u.is_active AND u.is_system_admin AND NOT u.can_access_all_tenants AND u.deleted_at IS NULL AND m.tenant_id=10000 AND m.role='owner' AND m.status='active' AND m.deleted_at IS NULL) THEN RAISE EXCEPTION 'new_admin_not_verified'; END IF; END $trial$;"]
    for key, value in POLICIES.items():
        statements.append("DO $trial$ BEGIN IF NOT EXISTS (SELECT 1 FROM system_settings WHERE key=" + sql_literal(key) + " AND value=" + sql_literal(json.dumps(value)) + "::jsonb) THEN RAISE EXCEPTION 'policy_changed'; END IF; END $trial$;")
    statements.append("CREATE TEMP TABLE trial_preserved ON COMMIT DROP AS SELECT " + protected_sql() + " AS body;")
    statements.extend([
        "UPDATE knowledge_bases SET creator_id=" + actor + ",updated_at=now() WHERE id=" + sql_literal(SOURCE_KB) + " AND tenant_id=10000 AND deleted_at IS NULL;",
        "UPDATE users SET tenant_id=10000,can_access_all_tenants=false,updated_at=now() WHERE id=" + actor + ";",
        "UPDATE auth_tokens SET is_revoked=true WHERE user_id IN (" + old + ");",
        "DELETE FROM boyan_document_jobs WHERE id IN (" + sql_ids(scope["document_jobs"]) + ");",
        "DELETE FROM partner_provisioning_jobs WHERE partner_key IN (" + sql_ids(scope["partner_keys"]) + ");",
        "DELETE FROM organization_tenant_members WHERE id IN (" + sql_ids(scope["organization_members"]) + ");",
    ])
    for key, table in SOFT_TABLES.items():
        if not scope[key]:
            continue
        extra = {"users": ",is_active=false,is_system_admin=false,can_access_all_tenants=false", "members": ",status='suspended'", "embed_channels": ",enabled=false"}.get(key, "")
        statements.append("UPDATE " + table + " SET deleted_at=now(),updated_at=now()" + extra + " WHERE id IN (" + sql_ids(scope[key]) + ");")
    statements.extend([
        "DO $trial$ BEGIN IF (SELECT body FROM trial_preserved) <> " + protected_sql() + " THEN RAISE EXCEPTION 'protected_source_changed'; END IF; IF EXISTS (SELECT 1 FROM users WHERE id IN (" + old + ") AND (is_active OR is_system_admin OR can_access_all_tenants OR deleted_at IS NULL)) OR EXISTS (SELECT 1 FROM auth_tokens WHERE user_id IN (" + old + ") AND NOT is_revoked) THEN RAISE EXCEPTION 'retirement_incomplete'; END IF; END $trial$;",
        "COMMIT;", "SELECT json_build_object('committed',true,'source_preserved',true,'old_tokens_revoked',true);",
    ])
    return "\n".join(statements)


def run_retirement(container, sql):
    result = checked_run(["docker", "exec", "-i", container, "sh", "-c", 'exec psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], input=sql)
    require(json.loads(result) == {"committed": True, "source_preserved": True, "old_tokens_revoked": True}, "transaction_result_unknown")


def create_and_verify_admin(client, values, old_ids, progress):
    body = client.request("POST", "/api/v1/system/admin/users/create", {"username": values["TRIAL_ADMIN_NAME"], "email": values["TRIAL_ADMIN_EMAIL"], "password": values["TRIAL_ADMIN_PASSWORD"]}, accepted=(200, 201))
    user = body.get("user", {})
    new_id = str(uuid.UUID(user.get("id", "")))
    require(new_id not in old_ids and user.get("email", "").lower() == values["TRIAL_ADMIN_EMAIL"].lower() and user.get("username") == values["TRIAL_ADMIN_NAME"] and not user.get("can_access_all_tenants"), "created_admin_identity_invalid")
    progress("created", new_id)
    client.request("POST", "/api/v1/system/admin/promote", {"user_id": new_id})
    members = client.request("GET", "/api/v1/tenants/10000/members?page_size=100").get("data", {}).get("members", [])
    matched = [member for member in members if member.get("user_id") == new_id]
    if not matched:
        client.request("POST", "/api/v1/tenants/10000/members", {"email": values["TRIAL_ADMIN_EMAIL"], "role": "owner"}, accepted=(200, 201))
    elif matched[0].get("role") != "owner" or matched[0].get("status") != "active":
        client.request("PUT", "/api/v1/tenants/10000/members/" + new_id, {"role": "owner"})
    login = client.request("POST", "/api/v1/auth/login", {"email": values["TRIAL_ADMIN_EMAIL"], "password": values["TRIAL_ADMIN_PASSWORD"]}, token="")
    token = login.get("token")
    require(isinstance(token, str) and len(token) > 20, "new_admin_login_failed")
    owner_identity(client, token, new_id)
    progress("new_admin_verified", new_id)
    return new_id, token


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--operator-token-stdin", action="store_true", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--baseline-sha256", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--backup-receipt", type=Path)
    parser.add_argument("--backup-receipt-sha256")
    parser.add_argument("--restore-report", type=Path)
    parser.add_argument("--restore-report-sha256")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--backup-identity", type=Path)
    parser.add_argument("--age-binary", default="age")
    parser.add_argument("--resume-new-user-id")
    args = parser.parse_args(argv)
    phase = "preflight"
    try:
        values = credentials(args.env_file)
        token = sys.stdin.readline().strip()
        client = Client(token, args.apply, args.base_url)
        baseline = validate_baseline(load_json(args.baseline, args.baseline_sha256))
        source_ids = local_containers()
        current = AUDIT.read_database(PROJECT, source_ids["postgres"])
        resume_id = str(uuid.UUID(args.resume_new_user_id)) if args.resume_new_user_id else None
        require(not resume_id or resume_id not in row_ids(baseline["users"]), "resume_id_is_old_user")
        compare_inventory(current, baseline, resume_id)
        operator = owner_identity(client)
        policies(client)
        plan = make_plan(baseline, source_ids, args.baseline_sha256, identity_hash(values), operator)
        if not args.apply:
            require(not args.plan.exists(), "plan_already_exists")
            write_json(args.plan, plan)
            print(json.dumps({"status": "DRY_RUN", "plan_sha256": file_digest(args.plan), "retire_counts": {key: len(rows) for key, rows in plan["retire"].items()}, "preserved_documents": 19, "runtime_changed": False}))
            return 0
        require(args.plan_sha256 and args.report and args.backup_receipt and args.backup_receipt_sha256 and args.restore_report and args.restore_report_sha256 and args.archive and args.backup_identity, "apply_backup_and_plan_arguments_required")
        report_path(args.report)
        for stage in ("created", "new_admin_verified"):
            report_path(args.report.with_name(args.report.stem + "." + stage + ".json"))
        require(load_json(args.plan, args.plan_sha256) == plan, "reviewed_plan_mismatch")
        receipt = load_json(args.backup_receipt, args.backup_receipt_sha256)
        restored = load_json(args.restore_report, args.restore_report_sha256)
        archive_hash = backup_gate(receipt, restored, args.archive, source_ids)
        verifier = ROOT / "scripts/verify-trial.py"
        require(verifier.is_file(), "backup_verifier_missing")
        verification = json.loads(checked_run([sys.executable, "-B", str(verifier), "--archive", str(args.archive), "--receipt", str(args.backup_receipt), "--restore-report", str(args.restore_report), "--identity", str(args.backup_identity), "--age-binary", args.age_binary], timeout=1800))
        require(verification.get("event") == "trial_evidence_verified" and verification.get("archive_sha256") == archive_hash and verification.get("source_ids") == source_ids and verification.get("database_fingerprint") == receipt.get("database_fingerprint"), "backup_content_verification_failed")
        require(local_containers() == source_ids, "container_changed_before_handover")
        def progress(stage, new_id):
            if resume_id:
                require(new_id == resume_id, "resume_identity_mismatch")
            destination = args.report.with_name(args.report.stem + "." + stage + ".json")
            write_json(destination, {"format": "boyankb-trial-admin-progress-v1", "stage": stage, "new_user_id": new_id, "plan_sha256": args.plan_sha256, "archive_sha256": archive_hash, "old_users_retired": False})
        phase = "creating_and_verifying_new_admin"
        new_id, new_token = create_and_verify_admin(client, values, row_ids(baseline["users"]), progress)
        compare_inventory(AUDIT.read_database(PROJECT, source_ids["postgres"]), baseline, new_id)
        require(local_containers() == source_ids, "container_changed_before_retirement")
        policies(client)
        phase = "retirement_transaction_outcome_unknown"
        run_retirement(source_ids["postgres"], retirement_sql(baseline, new_id))
        phase = "committed_post_verification"
        owner_identity(client, new_token, new_id)
        client.request("GET", "/api/v1/auth/me", accepted=(401,))
        client.request("GET", "/api/v1/knowledge-bases/" + SOURCE_KB, token=new_token)
        report = {"format": "boyankb-trial-admin-result-v1", "status": "verified", "project": PROJECT, "new_user_id": new_id, "tenant_id": HQ, "knowledge_base_id": SOURCE_KB, "source_preserved": True, "old_tokens_revoked": True, "old_operator_denied": True, "plan_sha256": args.plan_sha256, "archive_sha256": archive_hash, "retained_history_counts": plan["retained_history_counts"], "presets_reinstalled": False}
        write_json(args.report, report)
        print(json.dumps({"status": "VERIFIED", "new_user_id": new_id, "source_preserved": True, "old_tokens_revoked": True}))
        return 0
    except (TrialError, AUDIT.AuditError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "ERROR", "phase": phase, "code": str(error) if isinstance(error, TrialError) else "input_or_local_operation_failed"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
