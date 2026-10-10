import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/api/v1/system/admin/partner-provisioning"
SKILLS = {
    "boyan-courseware", "boyan-event-plan", "boyan-local-promotion-plan",
    "boyan-cooperation-proposal", "boyan-faq-guide", "boyan-docx",
    "boyan-pptx", "boyan-xlsx", "boyan-md",
}


class OnboardError(Exception):
    pass


class HTTPFailure(OnboardError):
    def __init__(self, status, operation):
        self.status = status
        super().__init__(f"{operation}:http_{status}")


def require(condition, code):
    if not condition:
        raise OnboardError(code)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def identifier(value):
    value = str(value)
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value)), "invalid_identifier")
    return value


def local_path(base, value):
    require(isinstance(value, str) and value != "", "invalid_preset_path")
    path = (base / value).resolve()
    require(path.is_relative_to(ROOT), "preset_outside_repository")
    require(path.is_file() or path.is_dir(), "preset_missing")
    require(not any(part.startswith(".") for part in path.relative_to(ROOT).parts), "hidden_preset_path")
    return path


def deterministic_archive(directory):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        files = sorted(p for p in directory.rglob("*") if p.is_file())
        require(files and (directory / "SKILL.md").is_file(), "skill_missing")
        total = 0
        for path in files:
            require(not path.is_symlink() and path.resolve().is_relative_to(directory.resolve()), "unsafe_skill_path")
            rel = path.relative_to(directory)
            require(not any(part.startswith(".") for part in rel.parts), "hidden_skill_file")
            content = path.read_bytes()
            total += len(content)
            require(total <= 16 * 1024 * 1024, "skill_bundle_too_large")
            info = zipfile.ZipInfo(rel.as_posix(), (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return output.getvalue()


def load_plan(path):
    manifest_path = Path(path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    allowed = {"schema_version", "partner_key", "name", "description", "hq_tenant_id", "target_tenant_id", "hq_knowledge_base_ids", "preset", "models", "members", "contributor_email", "knowledge_mode", "team_manager_email"}
    require(isinstance(manifest, dict) and set(manifest) <= allowed, "invalid_manifest_fields")
    require(manifest.get("schema_version") == 1, "manifest_version")
    require(bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", manifest.get("partner_key", ""))), "partner_key")
    require(0 < len(manifest.get("name", "").strip()) <= 120, "partner_name")
    require(str(manifest.get("hq_tenant_id", "")).isdigit() and int(manifest["hq_tenant_id"]) > 0, "hq_tenant_id")
    target = manifest.get("target_tenant_id")
    if "target_tenant_id" in manifest:
        require(type(target) is int and target > 0, "target_tenant_id")
    headquarters = target == int(manifest["hq_tenant_id"])
    if "knowledge_mode" in manifest:
        require(manifest["knowledge_mode"] == "shared_enterprise", "knowledge_mode")
    shared_enterprise = manifest.get("knowledge_mode") == "shared_enterprise"
    ids = manifest.get("hq_knowledge_base_ids")
    require(isinstance(ids, list) and 1 <= len(ids) <= 32 and len(set(ids)) == len(ids), "hq_knowledge_base_ids")
    require(not shared_enterprise or len(ids) == 1, "single_enterprise_knowledge_base_required")
    for item in ids:
        try:
            uuid.UUID(item)
        except (ValueError, TypeError, AttributeError):
            raise OnboardError("invalid_hq_knowledge_base_id") from None
    models = manifest.get("models", {})
    require(isinstance(models, dict) and set(models) <= {"chat", "embedding", "rerank"}, "model_fields")
    for key in ("chat", "embedding"):
        identifier(models.get(key, ""))
    if models.get("rerank"):
        identifier(models["rerank"])
    members = manifest.get("members", [])
    require(isinstance(members, list) and (len(members) == 0 if headquarters else 1 <= len(members) <= 100), "headquarters_members_unchanged" if headquarters else "members_required")
    emails = set()
    for member in members:
        require(isinstance(member, dict) and set(member) == {"email", "role"}, "member_fields")
        email = member["email"].strip().lower()
        require(bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)) and email not in emails, "member_email")
        require(member["role"] in ("viewer", "contributor"), "partner_member_role")
        member["email"] = email
        emails.add(email)
    contributor = manifest.get("contributor_email", "").strip().lower()
    if shared_enterprise:
        require(not contributor, "shared_enterprise_contributor_not_required")
    else:
        require(not contributor if headquarters else any(m["email"] == contributor and m["role"] == "contributor" for m in members), "headquarters_contributor_not_required" if headquarters else "contributor_required")
    manifest["members"] = members
    manifest["contributor_email"] = contributor
    if "team_manager_email" in manifest:
        manager = manifest["team_manager_email"]
        require(isinstance(manager, str) and manager.strip() != "" and shared_enterprise and not headquarters, "team_manager_requires_shared_partner")
        manager = manager.strip().lower()
        require(any(member["email"] == manager and member["role"] == "viewer" for member in members), "team_manager_must_be_viewer_member")
        manifest["team_manager_email"] = manager
    preset_path = local_path(ROOT, manifest.get("preset", "presets/boyan/v1/manifest.json"))
    preset = json.loads(preset_path.read_text(encoding="utf-8-sig"))
    require(preset.get("schema_version") == 1 and preset.get("preset_version") == "boyan/v1", "preset_version")
    agents = {}
    files = {preset_path.relative_to(ROOT).as_posix(): digest(preset_path.read_bytes())}
    template_path = local_path(preset_path.parent, preset["template_manifest"])
    templates = json.loads(template_path.read_text(encoding="utf-8-sig"))
    require(templates.get("version") == preset.get("template_version"), "template_version_mismatch")
    files[template_path.relative_to(ROOT).as_posix()] = digest(template_path.read_bytes())
    for item in templates["templates"]:
        template_file = local_path(template_path.parent, item["file"])
        require(digest(template_file.read_bytes()) == item["sha256"], "template_digest_mismatch")
        files[template_file.relative_to(ROOT).as_posix()] = item["sha256"]
    for spec in preset["agents"]:
        key = spec["key"]
        require(key in ("mentor", "planner", "reviewer") and key not in agents, "agent_keys")
        agent_path = local_path(preset_path.parent, spec["path"])
        agent = json.loads(agent_path.read_text(encoding="utf-8-sig"))
        files[agent_path.relative_to(ROOT).as_posix()] = digest(agent_path.read_bytes())
        prompts = []
        for value in spec.get("prompt_files", []):
            prompt_path = local_path(preset_path.parent, value)
            prompts.append(prompt_path.read_text(encoding="utf-8-sig").strip())
            files[prompt_path.relative_to(ROOT).as_posix()] = digest(prompt_path.read_bytes())
        require(prompts, "agent_prompts_required")
        agent["config"]["system_prompt"] = "\n\n".join(prompts)
        require(agent["config"].get("sandbox_config_id", "") == "" and agent["config"].get("skills_selection_mode") == "none" and agent["config"].get("mcp_selection_mode") == "none", "restricted_runtime_required")
        require(agent["config"].get("boyan_hybrid_search_without_rerank") is True, "managed_hybrid_search_required")
        allowed_tools = {"search_knowledge", "read_document", "list_documents"}
        if key == "planner":
            allowed_tools.add("boyan_document")
        require(set(agent["config"].get("allowed_tools", [])) <= allowed_tools, "unsafe_agent_tools")
        agents[key] = agent
    require(set(agents) == {"mentor", "planner", "reviewer"}, "three_agents_required")
    skills = {}
    for spec in preset["skills"]:
        directory = local_path(preset_path.parent, spec["path"])
        content = (directory / "SKILL.md").read_bytes()
        match = re.search(rb"(?m)^name:\s*[\"']?([a-z0-9-]+)[\"']?\s*$", content)
        require(match is not None, "skill_name_missing")
        name = match.group(1).decode("ascii")
        require(name in SKILLS and name not in skills, "skill_not_allowed")
        require(spec.get("name") == name and spec.get("version") == "boyan/v1" and spec.get("sha256") == digest(content), "skill_manifest_mismatch")
        archive = deterministic_archive(directory)
        skills[name] = {"sha256": digest(content), "archive": archive, "bundle_sha256": digest(archive)}
        files[directory.relative_to(ROOT).as_posix()] = digest(archive)
    require(set(skills) == SKILLS, "nine_skills_required")
    normalized = copy.deepcopy(manifest)
    normalized["hq_tenant_id"] = int(normalized["hq_tenant_id"])
    normalized["hq_knowledge_base_ids"] = sorted(normalized["hq_knowledge_base_ids"])
    normalized["members"] = sorted(normalized["members"], key=lambda m: m["email"])
    normalized["name"] = normalized["name"].strip()
    normalized["description"] = normalized.get("description", "").strip()
    return {"manifest": normalized, "agents": agents, "skills": skills, "spec_hash": digest(canonical({"manifest": normalized, "files": files}))}


def read_environment(path):
    values = {}
    allowed = {"ONBOARD_ADMIN_EMAIL", "ONBOARD_ADMIN_PASSWORD", "ONBOARD_ADMIN_TOKEN"}
    if path:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.removeprefix("export ").partition("=")
            if not sep or key.strip() not in allowed:
                continue
            value = value.strip()
            if value[:1] in ("'", '"'):
                require(len(value) >= 2 and value[-1] == value[0], "invalid_env_quoting")
                value = value[1:-1]
            values[key.strip()] = value
    values.update({k: os.environ[k] for k in allowed if k in os.environ})
    return values


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, ca_file, credentials, apply=False):
        parsed = urllib.parse.urlsplit(base_url)
        secure_origin = parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname == "127.0.0.1")
        require(secure_origin and parsed.hostname and not parsed.username and not parsed.password and parsed.path in ("", "/") and not parsed.query and not parsed.fragment and not any(c.isspace() for c in base_url), "https_or_ipv4_loopback_origin_required")
        self.base = base_url.rstrip("/")
        self.apply = apply
        self.token = credentials.get("ONBOARD_ADMIN_TOKEN", "")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))
        if not self.token:
            require(credentials.get("ONBOARD_ADMIN_EMAIL") and credentials.get("ONBOARD_ADMIN_PASSWORD"), "admin_credentials_missing")
            body = self.request("POST", "/api/v1/auth/login", {"email": credentials["ONBOARD_ADMIN_EMAIL"], "password": credentials["ONBOARD_ADMIN_PASSWORD"]}, operation="login")
            self.token = body["token"]
            require(isinstance(self.token, str) and bool(self.token), "login_token_missing")

    def request(self, method, path, payload=None, tenant=None, operation="api", raw=None, content_type=None):
        require(path.startswith("/api/v1/") and not path.startswith("//"), "invalid_api_path")
        require(self.apply or method == "GET" or path == "/api/v1/auth/login", "apply_required")
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if tenant is not None:
            headers["X-Tenant-ID"] = str(tenant)
        if raw is None and payload is not None:
            raw = canonical(payload)
        if raw is not None:
            headers["Content-Type"] = content_type or "application/json"
        request = urllib.request.Request(self.base + path, data=raw, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=120) as response:
                body = json.loads(response.read(16 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise HTTPFailure(exc.code, operation) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise OnboardError(operation + ":network_error_outcome_unknown") from None
        except (ValueError, UnicodeError):
            raise OnboardError(operation + ":invalid_response") from None
        require(not isinstance(body, dict) or body.get("success") is not False, operation + ":api_failed")
        return body


def rows(body):
    value = body.get("data", body)
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("members", "organizations", "agents", "knowledge_bases", "items", "models"):
            if isinstance(value.get(key), list):
                return value[key]
    raise OnboardError("unexpected_list_response")


def preset_config_matches(saved, wanted):
    if isinstance(wanted, dict):
        return isinstance(saved, dict) and all(preset_config_matches(saved.get(key), value) for key, value in wanted.items())
    if saved is None and wanted in ("", False, 0, [], None):
        return True
    return saved == wanted


class Onboarder:
    def __init__(self, client, plan, timeout=300):
        self.client, self.plan, self.timeout = client, plan, timeout
        self.manifest = plan["manifest"]
        self.hq = self.manifest["hq_tenant_id"]
        self.key = self.manifest["partner_key"]
        self.path = PREFIX + "/" + self.key
        self.holder = str(uuid.uuid4())
        self.job = None
        self.last_renew = 0
        self.actor_id = None
        self.headquarters = self.manifest.get("target_tenant_id") == self.hq
        self.shared_enterprise = self.manifest.get("knowledge_mode") == "shared_enterprise"

    def request(self, method, path, payload=None, tenant=None, operation="api", **kwargs):
        if self.job and time.monotonic() - self.last_renew >= 60 and path != self.path + "/lease":
            self.lease()
        return self.client.request(method, path, payload, tenant=tenant, operation=operation, **kwargs)

    def lease(self, release=False):
        fresh = self.client.request("POST", self.path + "/lease", {"lease_holder": self.holder, "release": release}, tenant=self.hq, operation="lease")["data"]
        if self.job is None:
            self.job = fresh
        else:
            require(self.job["revision"] == fresh["revision"], "concurrent_job_update")
            self.job["lease_until"] = fresh.get("lease_until")
        self.last_renew = time.monotonic()

    def checkpoint(self, status="provisioning"):
        state = copy.deepcopy(self.job["state"])
        state.pop("restricted_skills", None)
        self.job = self.request("PATCH", self.path, {"lease_holder": self.holder, "revision": self.job["revision"], "state": state, "status": status}, tenant=self.hq, operation="checkpoint")["data"]

    def action(self, suffix, extra=None):
        payload = {"lease_holder": self.holder, "revision": self.job["revision"]}
        payload.update(extra or {})
        self.job = self.request("POST", self.path + suffix, payload, tenant=self.hq, operation=suffix.strip("/"))["data"]

    def preflight(self):
        me = self.request("GET", "/api/v1/auth/me", tenant=self.hq, operation="identity")["data"]["user"]
        require(me.get("is_system_admin") is True, "system_admin_required")
        require(me.get("can_access_all_tenants") is not True, "broad_operator_flag_not_supported")
        member_query = urllib.parse.urlencode({"q": me.get("email", ""), "page_size": 100})
        members = rows(self.request("GET", f"/api/v1/tenants/{self.hq}/members?" + member_query, tenant=self.hq))
        require(any(str(m.get("user_id")) == str(me["id"]) and m.get("role") == "owner" for m in members), "hq_owner_required")
        self.actor_id = identifier(me["id"])
        target = self.manifest.get("target_tenant_id")
        if target:
            target_members = rows(self.request("GET", f"/api/v1/tenants/{target}/members?" + member_query, tenant=target))
            require(any(str(m.get("user_id")) == self.actor_id and m.get("role") == "owner" and m.get("status") == "active" for m in target_members), "target_owner_required")
            self.validate_models(target)
            self.require_disabled_sandbox(target)
        self.validate_models(self.hq)
        for kb_id in self.manifest["hq_knowledge_base_ids"]:
            kb = self.request("GET", "/api/v1/knowledge-bases/" + identifier(kb_id), tenant=self.hq)["data"]
            require(str(kb.get("tenant_id")) == str(self.hq), "hq_kb_not_owned")
            require(kb.get("embedding_model_id") == self.manifest["models"]["embedding"], "embedding_binding_mismatch")
        return me

    def run(self):
        self.preflight()
        payload = {k: self.manifest[k] for k in ("partner_key", "name", "description", "hq_tenant_id", "hq_knowledge_base_ids")}
        payload["spec_hash"] = self.plan["spec_hash"]
        if self.manifest.get("target_tenant_id"):
            payload["target_tenant_id"] = self.manifest["target_tenant_id"]
        if self.shared_enterprise:
            payload["knowledge_mode"] = "shared_enterprise"
        self.job = self.request("POST", PREFIX, payload, tenant=self.hq, operation="provision")["data"]
        require(self.job["state"].get("knowledge_mode", "") == self.manifest.get("knowledge_mode", ""), "knowledge_mode_mismatch")
        self.last_renew = time.monotonic()
        self.lease()
        try:
            self.checkpoint()
            self.validate_models(self.job["tenant_id"])
            self.sandbox_policy()
            contributor = self.members()
            self.team_manager()
            self.organization()
            self.knowledge(contributor)
            self.skills()
            self.agents()
            self.verify_resources()
            self.wait_readiness()
            self.checkpoint("ready")
            return self.report()
        except Exception:
            try:
                self.job = self.client.request("GET", self.path, tenant=self.hq, operation="recovery")["data"]
                self.checkpoint("blocked")
            except Exception:
                pass
            raise
        finally:
            try:
                self.lease(release=True)
            except Exception:
                pass

    def sandbox_policy(self):
        tenant = self.job["tenant_id"]
        if not self.manifest.get("target_tenant_id"):
            self.request("PUT", "/api/v1/sandbox-configs/workspace-policy", {"scripts_disabled": True}, tenant=tenant)
        self.require_disabled_sandbox(tenant)

    def require_disabled_sandbox(self, tenant):
        body = self.request("GET", "/api/v1/sandbox-configs", tenant=tenant)
        require(body.get("workspace_scripts_disabled") is True and rows(body) == [], "sandbox_policy_failed")

    def validate_models(self, tenant):
        for key, kind in (("chat", "KnowledgeQA"), ("embedding", "Embedding"), ("rerank", "Rerank")):
            model_id = self.manifest["models"].get(key)
            if not model_id:
                continue
            model = self.request("GET", "/api/v1/models/" + identifier(model_id), tenant=tenant, operation="model_binding")["data"]
            require(model.get("id") == model_id and model.get("type") == kind and model.get("status") == "active", "model_binding_unavailable")

    def members(self):
        if self.headquarters:
            require(self.actor_id == self.job["owner_user_id"], "headquarters_owner_mismatch")
            return self.actor_id
        tenant = self.job["tenant_id"]
        path = f"/api/v1/tenants/{tenant}/members"
        contributor = None
        for member in self.manifest["members"]:
            query = urllib.parse.urlencode({"q": member["email"], "page_size": 100})
            existing = [m for m in rows(self.request("GET", path + "?" + query, tenant=tenant)) if m.get("email", "").lower() == member["email"]]
            require(len(existing) <= 1, "ambiguous_member")
            if not existing:
                self.request("POST", path, member, tenant=tenant, operation="member_add_existing_account")
                existing = [m for m in rows(self.request("GET", path + "?" + query, tenant=tenant)) if m.get("email", "").lower() == member["email"]]
            require(len(existing) == 1 and existing[0].get("role") == member["role"] and existing[0].get("status") == "active", "member_role_mismatch")
            if member["email"] == self.manifest["contributor_email"]:
                contributor = identifier(existing[0]["user_id"])
        require(self.shared_enterprise or contributor, "contributor_missing")
        return contributor

    def organization(self):
        if self.headquarters:
            require(not self.job["state"].get("organization_id"), "headquarters_organization_not_allowed")
            return
        state = self.job["state"]
        name = "boyan-partner-" + self.key
        if not state.get("organization_id"):
            existing = [o for o in rows(self.request("GET", "/api/v1/organizations", tenant=self.hq)) if o.get("name") == name and str(o.get("owner_tenant_id")) == str(self.hq)]
            require(len(existing) <= 1, "ambiguous_organization")
            if existing:
                org = existing[0]
            else:
                org = self.request("POST", "/api/v1/organizations", {"name": name, "description": self.manifest["name"], "member_limit": 2, "invite_code_validity_days": 1}, tenant=self.hq, operation="organization_create")["data"]
            state["organization_id"] = identifier(org["id"])
            self.checkpoint()
        org_id = self.job["state"]["organization_id"]
        path = "/api/v1/organizations/" + identifier(org_id)
        self.request("PUT", path, {"require_approval": True, "searchable": False, "member_limit": 2}, tenant=self.hq)
        members = rows(self.request("GET", path + "/members", tenant=self.hq))
        allowed = {str(self.hq), str(self.job["tenant_id"])}
        require(all(str(m.get("tenant_id")) in allowed for m in members), "organization_has_unexpected_tenant")
        partner = [m for m in members if str(m.get("tenant_id")) == str(self.job["tenant_id"])]
        if not partner:
            self.request("POST", path + "/invite", {"tenant_id": self.job["tenant_id"], "role": "viewer"}, tenant=self.hq)
        else:
            require(len(partner) == 1 and partner[0].get("role") == "viewer", "organization_partner_role_mismatch")
        for kb in self.manifest["hq_knowledge_base_ids"]:
            self.request("POST", "/api/v1/knowledge-bases/" + identifier(kb) + "/shares", {"organization_id": org_id, "permission": "viewer"}, tenant=self.hq)

    def team_manager(self):
        email = self.manifest.get("team_manager_email")
        if not email:
            require(not self.job["state"].get("team_manager_user_id"), "unexpected_team_manager_binding")
            return
        tenant = self.job["tenant_id"]
        query = urllib.parse.urlencode({"q": email, "page_size": 100})
        matched = [member for member in rows(self.request("GET", f"/api/v1/tenants/{tenant}/members?" + query, tenant=tenant)) if member.get("email", "").lower() == email]
        require(len(matched) == 1 and matched[0].get("role") == "viewer" and matched[0].get("status") == "active", "active_viewer_team_manager_required")
        user_id = identifier(matched[0]["user_id"])
        require(self.job["state"].get("team_manager_user_id") in (None, "", user_id), "team_manager_binding_mismatch")
        self.job["state"]["team_manager_user_id"] = user_id
        self.checkpoint()

    def knowledge(self, contributor):
        state, tenant = self.job["state"], self.job["tenant_id"]
        if self.shared_enterprise:
            require(not state.get("knowledge_base_id") and not state.get("contributor_user_id"), "shared_enterprise_private_knowledge_not_allowed")
            return
        if not state.get("knowledge_base_id"):
            kb_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "boyankb:partner:" + self.key + ":knowledge"))
            try:
                kb = self.request("GET", "/api/v1/knowledge-bases/" + kb_id, tenant=tenant)["data"]
            except HTTPFailure as exc:
                if exc.status != 404:
                    raise
                kb = self.request("POST", "/api/v1/knowledge-bases", {"id": kb_id, "name": self.manifest["name"] + "资料", "type": "document", "description": "合作伙伴工作资料", "embedding_model_id": self.manifest["models"]["embedding"], "summary_model_id": "", "chunking_config": {"chunk_size": 512, "chunk_overlap": 50}, "question_generation_config": {"enabled": False}, "indexing_strategy": {"vector_enabled": True, "keyword_enabled": True, "wiki_enabled": False, "graph_enabled": False}}, tenant=tenant, operation="partner_kb_create")["data"]
            require(str(kb.get("tenant_id")) == str(tenant), "partner_kb_wrong_tenant")
            state["knowledge_base_id"] = identifier(kb["id"])
            self.checkpoint()
        self.action("/knowledge-creator", {"user_id": contributor})

    def skills(self):
        tenant = self.job["tenant_id"]
        state = self.job["state"]
        catalog_ids = state.setdefault("skill_catalog_ids", {})
        catalog = {row["name"]: row for row in rows(self.request("GET", "/api/v1/skills/catalog", tenant=tenant))}
        for name, skill in sorted(self.plan["skills"].items()):
            row = catalog.get(name)
            if row is None or row.get("bundle_sha256") != skill["bundle_sha256"]:
                boundary = "boyan" + uuid.uuid4().hex
                raw = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}.zip\"\r\nContent-Type: application/zip\r\n\r\n".encode("ascii") + skill["archive"] + f"\r\n--{boundary}--\r\n".encode("ascii"))
                row = self.request("POST", "/api/v1/skills/catalog", tenant=tenant, raw=raw, content_type="multipart/form-data; boundary=" + boundary, operation="skill_catalog_register")["data"]
            previous = catalog_ids.get(name)
            require(previous in (None, row["id"]), "skill_catalog_identity_changed")
            catalog_ids[name] = identifier(row["id"])
            self.job["state"]["skill_catalog_ids"] = catalog_ids
            self.checkpoint()
            catalog_ids = self.job["state"]["skill_catalog_ids"]
        self.action("/skills/install")
        for name, installed in self.job["state"]["restricted_skills"].items():
            require(installed.get("enabled") is True and installed["sha256"] == self.plan["skills"][name]["sha256"] and installed["catalog_sha256"] == self.plan["skills"][name]["bundle_sha256"], "restricted_skill_digest_mismatch")

    def agent_payload(self, key):
        result = copy.deepcopy(self.plan["agents"][key])
        config = result["config"]
        config["model_id"] = self.manifest["models"]["chat"]
        config["rerank_model_id"] = self.manifest["models"].get("rerank", "")
        kb_ids = []
        for value in config["knowledge_bases"]:
            if value == "@hq":
                kb_ids.extend(self.manifest["hq_knowledge_base_ids"])
            elif value == "@partner":
                if not self.shared_enterprise:
                    kb_ids.append(self.job["state"]["knowledge_base_id"])
            else:
                raise OnboardError("unexpected_agent_kb_binding")
        config["knowledge_bases"] = list(dict.fromkeys(kb_ids))
        config["kb_selection_mode"] = "selected"
        config["sandbox_config_id"] = ""
        config["skills_selection_mode"] = "none"
        config["selected_skills"] = []
        suggestions = config.get("question_suggestions", {})
        if "followups" in suggestions:
            require(suggestions["followups"] == {"enabled": False, "mode": "curated", "count": 0} and "follow_ups" not in suggestions, "unsupported_legacy_followups")
            suggestions.pop("followups")
            suggestions["follow_ups"] = {"enabled": False, "mode": "hybrid", "count": 3}
        return result

    def agents(self):
        tenant = self.job["tenant_id"]
        agents = rows(self.request("GET", "/api/v1/agents", tenant=tenant))
        for key in ("mentor", "planner", "reviewer"):
            payload = self.agent_payload(key)
            known = self.job["state"].get("agent_ids", {}).get(key)
            if known:
                existing = [a for a in agents if a["id"] == known]
            else:
                existing = [a for a in agents if a.get("name") == payload["name"] and a.get("created_by") == self.job["owner_user_id"] and not a.get("is_builtin")]
            require(len(existing) <= 1, "ambiguous_agent")
            if known:
                require(len(existing) == 1, "recorded_agent_missing")
            if existing:
                agent_id = identifier(existing[0]["id"])
                if self.manifest.get("target_tenant_id"):
                    saved = self.request("GET", "/api/v1/agents/" + agent_id, tenant=tenant)["data"]
                    require(saved.get("created_by") == self.job["owner_user_id"] and not saved.get("is_builtin") and preset_config_matches(saved.get("config", {}), payload["config"]), "existing_agent_requires_review")
                    if not known:
                        require(all(saved.get(field) == payload.get(field) for field in ("name", "description", "avatar")), "existing_agent_requires_review")
                else:
                    self.request("PUT", "/api/v1/agents/" + agent_id, payload, tenant=tenant, operation="agent_reconcile")
            else:
                agent_id = identifier(self.request("POST", "/api/v1/agents", payload, tenant=tenant, operation="agent_create")["data"]["id"])
            self.job["state"].setdefault("agent_ids", {})[key] = agent_id
            self.checkpoint()

    def verify_resources(self):
        tenant = self.job["tenant_id"]
        self.sandbox_policy()
        if self.shared_enterprise:
            require(not self.job["state"].get("knowledge_base_id") and not self.job["state"].get("contributor_user_id"), "shared_enterprise_private_knowledge_not_allowed")
        else:
            kb = self.request("GET", "/api/v1/knowledge-bases/" + self.job["state"]["knowledge_base_id"], tenant=tenant)["data"]
            require(kb.get("creator_id") == self.job["owner_user_id"] if self.headquarters else kb.get("creator_id") and kb["creator_id"] != self.job["owner_user_id"], "knowledge_creator_mismatch")
            require(kb.get("summary_model_id", "") == "" and not kb.get("indexing_strategy", {}).get("wiki_enabled") and not kb.get("indexing_strategy", {}).get("graph_enabled"), "partner_kb_unsafe_pipeline")
        if self.headquarters:
            for kb_id in self.manifest["hq_knowledge_base_ids"]:
                source = self.request("GET", "/api/v1/knowledge-bases/" + identifier(kb_id), tenant=tenant)["data"]
                require(str(source.get("tenant_id")) == str(tenant), "headquarters_source_not_owned")
        else:
            shared = rows(self.request("GET", "/api/v1/shared-knowledge-bases", tenant=tenant))
            for kb_id in self.manifest["hq_knowledge_base_ids"]:
                matches = [item for item in shared if (item.get("knowledge_base") or {}).get("id") == kb_id]
                require(matches and all(item.get("effective_permission", item.get("permission")) == "viewer" for item in matches), "hq_share_not_read_only")
        for key, agent_id in self.job["state"]["agent_ids"].items():
            saved = self.request("GET", "/api/v1/agents/" + identifier(agent_id), tenant=tenant)["data"]
            wanted = self.agent_payload(key)
            for field in ("system_prompt", "knowledge_bases", "model_id", "rerank_model_id", "boyan_hybrid_search_without_rerank", "sandbox_config_id", "skills_selection_mode", "allowed_tools"):
                require(saved["config"].get(field, "") == wanted["config"].get(field, ""), "agent_config_mismatch")

    def report(self):
        scope_check = "企业知识库只读、跨空间会话与文件隔离" if self.shared_enterprise else "Viewer/Contributor跨空间拒绝与资料上传"
        return {"partner_key": self.key, "spec_hash": self.plan["spec_hash"], "status": self.job["status"], "tenant_id": self.job["tenant_id"], "state": self.job["state"], "manual_checks": ["指定伙伴身份与账号交付", scope_check, "Agent真实问答与固定文件渲染", "渲染服务健康和输出内容审阅"]}

    def wait_readiness(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                result = self.request("GET", "/api/v1/business-workflow/readiness", tenant=self.job["tenant_id"], operation="renderer_readiness")["data"]
                ready = result.get("ready") is True and result.get("template_integrity") is True and result.get("template_version") == "boyan-v1" and result.get("restricted_skill_count") == 9 and set(result.get("formats", [])) == {"docx", "pptx", "xlsx", "md"}
                if ready:
                    return
            except HTTPFailure as exc:
                if exc.status not in (502, 503, 504):
                    raise
            require(time.monotonic() < deadline, "renderer_readiness_timeout")
            time.sleep(min(5, max(0, deadline - time.monotonic())))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--ca-file")
    parser.add_argument("--env-file")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    try:
        require(0 <= args.timeout <= 3600, "invalid_readiness_timeout")
        plan = load_plan(args.manifest)
        result = {"mode": "plan", "partner_key": plan["manifest"]["partner_key"], "spec_hash": plan["spec_hash"], "agents": 3, "restricted_skills": 9, "new_accounts": False}
        if plan["manifest"].get("knowledge_mode") == "shared_enterprise":
            result.update({"knowledge_mode": "shared_enterprise", "enterprise_knowledge_bases": 1, "creates_working_knowledge_base": False})
        if plan["manifest"].get("target_tenant_id"):
            result["target_tenant_id"] = plan["manifest"]["target_tenant_id"]
            result["creates_workspace"] = False
        if args.apply:
            require(args.base_url, "base_url_required")
            client = Client(args.base_url, args.ca_file, read_environment(args.env_file), apply=True)
            result = Onboarder(client, plan, args.timeout).run()
        if args.report:
            path = Path(args.report)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except OnboardError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (KeyError, TypeError, ValueError, OSError):
        print(json.dumps({"status": "blocked", "error": "invalid_local_configuration_or_response"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
