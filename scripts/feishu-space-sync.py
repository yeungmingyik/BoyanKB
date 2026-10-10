import argparse
import collections
import datetime
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


SPACE_ID = "7610776951925509072"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REQUIRED_SCOPES = frozenset({"wiki:wiki:readonly", "drive:export:readonly", "drive:file:download"})
DOWNLOAD_ALTERNATIVES = frozenset({"drive:drive:readonly", "drive:file:readonly"})
SUPPORTED_TYPES = frozenset({"doc", "docx", "sheet", "bitable", "file"})
KB_NAME = "合作伙伴专用空间"
KB_NAMES = frozenset({KB_NAME, "feishu-" + SPACE_ID})
KB_MARKER = "managed-feishu-sync:" + SPACE_ID
GUARD_LABEL = "io.weknora.feishu-guard"
GRANTED = 1
UNGRANTED = 2
SECRET_VALUES = set()


class SafeError(Exception):
    pass


class ApiError(SafeError):
    def __init__(self, kind, code, api_code=None):
        self.kind = kind
        self.code = code
        self.api_code = code if kind == "feishu" else api_code
        message = ("API request failed with HTTP " if kind == "http" else "Feishu API failed with code ") + str(code) + "."
        if kind == "http" and api_code is not None:
            message += " Feishu API code " + str(api_code) + "."
        super().__init__(message)


def fail(message):
    raise SafeError(message)


def emit(event, **fields):
    value = json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True)
    for secret in sorted(SECRET_VALUES, key=len, reverse=True):
        if secret:
            value = value.replace(secret, "[REDACTED]")
    print(value, flush=True)


def load_settings():
    names = {"FEISHU_APP_ID", "FEISHU_APP_SECRET", "SMOKE_ADMIN_EMAIL", "SMOKE_ADMIN_PASSWORD", "SMOKE_BASE_URL"}
    settings = {}
    path = REPO_ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
            if not match or match[1] not in names:
                continue
            name, value = match.groups()
            value = value.strip()
            if value.startswith(("'", '"')):
                try:
                    parts = shlex.split(value, comments=True, posix=True)
                except ValueError:
                    fail("Invalid quoting in required .env variable.")
                if len(parts) != 1:
                    fail("Invalid required .env value.")
                value = parts[0]
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
            settings[name] = value
    settings.update({name: os.environ[name] for name in names if name in os.environ})
    for name in names - {"SMOKE_BASE_URL"}:
        if settings.get(name):
            SECRET_VALUES.add(settings[name])
    return settings


def required(settings, name):
    value = settings.get(name, "").strip()
    if not value or re.search(r"replace|changeme|your[-_ ]|placeholder|^<|\$\{", value, re.I):
        fail("Set " + name + " in .env before this action.")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        fail("Invalid identifier in API response.")
    return value


def mapping(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            fail("Invalid JSON object in API response.")
    if not isinstance(value, dict):
        fail("Expected an API response object.")
    return value


def timestamp(value):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value) > 40:
        fail("Invalid timestamp in API response.")
    try:
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail("Invalid timestamp in API response.")
    return value


def node_timestamp(value):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}", value):
        fail("Invalid Wiki edit timestamp.")
    return value


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fail("HTTP redirect refused.")


class HttpClient:
    def __init__(self, base, feishu=False):
        parsed = urllib.parse.urlsplit(base)
        if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            fail("Invalid API base URL.")
        if feishu:
            allowed = parsed.scheme == "https" and parsed.hostname == "open.feishu.cn" and parsed.port in (None, 443)
        else:
            allowed = parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port is not None
        if not allowed:
            fail("API base URL is outside the allowed origin.")
        self.base = base.rstrip("/")
        self.feishu = feishu
        self.token = ""
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectRedirects())

    def request(self, path, method="GET", payload=None):
        if not path.startswith("/") or path.startswith("//") or "#" in path or "\\" in path:
            fail("Invalid API path.")
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=60) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            api_code = None
            if self.feishu:
                try:
                    error = json.loads(exc.read(1048576))
                    if isinstance(error, dict) and type(error.get("code")) is int:
                        api_code = error["code"]
                except (ValueError, UnicodeError, OSError):
                    pass
            raise ApiError("http", exc.code, api_code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            fail("API connection failed or timed out.")
        except (ValueError, UnicodeError):
            fail("API returned invalid JSON.")
        if self.feishu:
            result = mapping(result)
            code = result.get("code")
            if type(code) is not int:
                fail("Feishu response has no numeric result code.")
            if code != 0:
                raise ApiError("feishu", code)
        elif isinstance(result, dict) and result.get("success") is False:
            fail("WeKnora API reported failure.")
        return result


class FeishuClient(HttpClient):
    def __init__(self, settings):
        super().__init__("https://open.feishu.cn", feishu=True)
        result = self.request("/open-apis/auth/v3/tenant_access_token/internal", "POST", {
            "app_id": required(settings, "FEISHU_APP_ID"),
            "app_secret": required(settings, "FEISHU_APP_SECRET"),
        })
        token = result.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            fail("Feishu authentication returned no tenant access token.")
        self.token = token
        SECRET_VALUES.add(token)


def scope_state(client):
    data = mapping(client.request("/open-apis/application/v6/scopes").get("data"))
    rows = data.get("scopes")
    if not isinstance(rows, list):
        fail("Feishu response has no scope list.")
    scopes = {}
    for row in rows:
        row = mapping(row)
        name = row.get("scope_name")
        status = row.get("grant_status")
        identity = row.get("scope_type", "tenant")
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,200}", name):
            fail("Invalid scope name in Feishu response.")
        if type(status) is not int or status not in (GRANTED, UNGRANTED) or identity not in ("tenant", "user"):
            fail("Unknown scope identity or grant status; refusing permission changes.")
        key = (name, identity)
        if key in scopes:
            fail("Duplicate scope record in Feishu response.")
        scopes[key] = status
        emit("scope", scope=name, grant_status=status, identity=identity)
    declared = {name for name, identity in scopes if identity == "tenant"}
    granted = {name for (name, identity), status in scopes.items() if identity == "tenant" and status == GRANTED}
    needed = set(REQUIRED_SCOPES)
    broader = sorted(granted & DOWNLOAD_ALTERNATIVES)
    if broader:
        needed.discard("drive:file:download")
        emit("warning", reason="already_granted_broader_download_scope", scopes=broader)
    pending = {key for key, status in scopes.items() if status != GRANTED}
    missing = sorted(needed - declared)
    ungranted = sorted(needed - granted)
    emit("permission_check", missing_declared=missing, missing_granted=ungranted)
    return scopes, needed, pending, missing, ungranted


def permissions(client, apply=False):
    scopes, needed, pending, missing, ungranted = scope_state(client)
    if missing:
        fail("Required tenant scopes are undeclared. Configure and publish them in the Feishu developer console.")
    if not apply:
        if ungranted:
            fail("Required tenant scopes are not yet granted.")
        return
    if any(identity != "tenant" or name not in REQUIRED_SCOPES for name, identity in pending):
        fail("Unrelated pending scopes exist; bulk scope application refused.")
    if not pending:
        emit("permission_apply", status="not_needed")
        return
    client.request("/open-apis/application/v6/scopes/apply", "POST")
    emit("permission_apply", status="submitted_not_yet_granted", scopes=sorted(name for name, identity in pending))


def inventory(client, report=True):
    pending = collections.deque([""])
    nodes = {}
    while pending:
        parent = pending.popleft()
        page_token = ""
        cursors = set()
        for _ in range(10000):
            query = {"page_size": "50"}
            if parent:
                query["parent_node_token"] = parent
            if page_token:
                query["page_token"] = page_token
            path = "/open-apis/wiki/v2/spaces/" + SPACE_ID + "/nodes?" + urllib.parse.urlencode(query)
            data = mapping(client.request(path).get("data"))
            items = data.get("items")
            if items is None:
                items = []
            if not isinstance(items, list) or type(data.get("has_more")) is not bool:
                fail("Invalid Wiki node pagination response.")
            for row in items:
                row = mapping(row)
                node_id = identifier(row.get("node_token"))
                origin_space = row.get("origin_space_id", "")
                if row.get("space_id") != SPACE_ID or origin_space not in ("", SPACE_ID):
                    fail("Cross-space or unproven-space Wiki node refused.")
                if row.get("node_type") != "origin":
                    fail("Shortcut or unknown Wiki node type refused.")
                if row.get("parent_node_token", "") != parent:
                    fail("Wiki node parent mismatch.")
                obj_type = row.get("obj_type")
                if not isinstance(obj_type, str) or not re.fullmatch(r"[a-z0-9_]{1,40}", obj_type):
                    fail("Invalid Wiki object type.")
                if obj_type in SUPPORTED_TYPES:
                    identifier(row.get("obj_token"))
                if node_id in nodes or len(nodes) >= 100000:
                    fail("Duplicate Wiki node or inventory limit reached.")
                if type(row.get("has_child")) is not bool:
                    fail("Wiki child state is unknown.")
                nodes[node_id] = {"node_id": node_id, "space_id": SPACE_ID, "origin_space_id": origin_space,
                                  "node_type": "origin", "object_type": obj_type, "parent_id": parent,
                                  "node_edit_time": node_timestamp(row.get("node_edit_time")),
                                  "object_edit_time": node_timestamp(row.get("obj_edit_time"))}
                if row["has_child"]:
                    pending.append(node_id)
            if not data["has_more"]:
                break
            page_token = data.get("page_token")
            if not isinstance(page_token, str) or not page_token or page_token in cursors:
                fail("Missing or repeated Wiki pagination cursor.")
            cursors.add(page_token)
        else:
            fail("Wiki pagination limit reached.")
    if report:
        for node in sorted(nodes.values(), key=lambda row: row["node_id"]):
            emit("node", **node)
        counts = dict(sorted(collections.Counter(row["object_type"] for row in nodes.values()).items()))
        emit("inventory", space_id=SPACE_ID, nodes=len(nodes), object_types=counts,
             supported_nodes=sum(row["object_type"] in SUPPORTED_TYPES for row in nodes.values()))
    return nodes


def audit_documents(client, node_ids=None):
    selected = sorted({identifier(node_id) for node_id in node_ids}) if node_ids else None
    nodes = inventory(client, report=False)
    if selected is None:
        selected = sorted(node_id for node_id, row in nodes.items() if row["object_type"] == "docx")
    if not selected:
        fail("No docx Wiki nodes are available for permission verification.")
    if any(node_id not in nodes for node_id in selected):
        fail("A requested node is outside the authorized-space inventory.")
    if any(nodes[node_id]["object_type"] != "docx" for node_id in selected):
        fail("Document permission audits currently support docx Wiki nodes only.")
    documents_to_check = []
    for node_id in selected:
        path = "/open-apis/wiki/v2/spaces/get_node?" + urllib.parse.urlencode({"token": node_id})
        try:
            data = mapping(client.request(path).get("data"))
            node = mapping(data.get("node"))
        except ApiError as exc:
            emit("check_error", node_id=node_id, action="get_node", http_status=exc.code if exc.kind == "http" else None, api_code=exc.api_code)
            fail("Document metadata could not be verified; no document permission checks were performed.")
        except SafeError:
            emit("check_error", node_id=node_id, action="get_node", reason="unverified_metadata")
            fail("Document metadata could not be verified; no document permission checks were performed.")
        if node.get("node_token") != node_id or node.get("space_id") != SPACE_ID or node.get("origin_space_id", "") not in ("", SPACE_ID):
            fail("Document metadata does not prove membership in the authorized Wiki space.")
        if node.get("node_type") != "origin" or node.get("obj_type") != "docx":
            fail("Shortcut, unknown, or changed Wiki document type refused.")
        documents_to_check.append((node_id, identifier(node.get("obj_token"))))
    failed = False
    calls = collections.deque()
    for node_id, object_id in documents_to_check:
        for action in ("view", "export"):
            now = time.monotonic()
            while calls and now - calls[0] >= 60:
                calls.popleft()
            if len(calls) >= 90:
                time.sleep(max(0, 60 - (now - calls[0])) + 0.1)
                now = time.monotonic()
                while calls and now - calls[0] >= 60:
                    calls.popleft()
            calls.append(time.monotonic())
            path = "/open-apis/drive/v1/permissions/" + object_id + "/members/auth?" + urllib.parse.urlencode({"type": "docx", "action": action})
            try:
                data = mapping(client.request(path).get("data"))
                result = data.get("auth_result")
                if type(result) is not bool:
                    fail("Document permission response has no boolean authorization result.")
            except ApiError as exc:
                emit("check_error", node_id=node_id, action=action, http_status=exc.code if exc.kind == "http" else None, api_code=exc.api_code)
                failed = True
                continue
            except SafeError:
                emit("check_error", node_id=node_id, action=action, reason="unverified_authorization_result")
                failed = True
                continue
            emit("document_permission", node_id=node_id, action=action, auth_result=result)
            failed = failed or not result
    if failed:
        fail("One or more document permissions are denied or unverified.")


def positive_space_probe(client):
    data = mapping(client.request("/open-apis/wiki/v2/spaces/" + SPACE_ID + "/nodes?page_size=1").get("data"))
    items = data.get("items") or []
    if not isinstance(items, list):
        fail("Authorized-space access probe returned invalid metadata.")
    for row in items:
        if mapping(row).get("space_id") != SPACE_ID:
            fail("Authorized-space access probe returned mismatched metadata.")


def audit_access(client, deny_space=None):
    if deny_space is not None and (not re.fullmatch(r"[0-9]{1,32}", deny_space) or deny_space == SPACE_ID):
        fail("The negative probe requires a different numeric Wiki space ID.")
    positive_space_probe(client)
    emit("authorized_space_probe", space_id=SPACE_ID, accessible=True)
    page_token = ""
    cursors = set()
    spaces = set()
    for _ in range(10000):
        query = {"page_size": "50"}
        if page_token:
            query["page_token"] = page_token
        data = mapping(client.request("/open-apis/wiki/v2/spaces?" + urllib.parse.urlencode(query)).get("data"))
        items = data.get("items") or []
        if not isinstance(items, list) or type(data.get("has_more")) is not bool:
            fail("Invalid Wiki space pagination response.")
        for row in items:
            space_id = identifier(mapping(row).get("space_id"))
            if space_id in spaces:
                fail("Duplicate Wiki space in paginated audit.")
            spaces.add(space_id)
        if not data["has_more"]:
            break
        page_token = data.get("page_token")
        if not isinstance(page_token, str) or not page_token or page_token in cursors:
            fail("Missing or repeated Wiki space pagination cursor.")
        cursors.add(page_token)
    else:
        fail("Wiki space pagination limit reached.")
    emit("wiki_access_audit", space_ids=sorted(spaces), visible_spaces=len(spaces),
         limitation="visible_wiki_spaces_only_not_drive_or_individual_document_acl")
    if deny_space is None:
        return
    try:
        client.request("/open-apis/wiki/v2/spaces/" + deny_space + "/nodes?page_size=1")
    except ApiError as exc:
        denied = exc.kind == "http" and exc.code == 403
        denied = denied or exc.api_code == 131006 and (exc.kind == "feishu" or exc.code == 400)
        if not denied:
            fail("Negative access probe is inconclusive; only HTTP 403 or Feishu 131006 proves denial.")
        positive_space_probe(client)
        emit("negative_space_probe", space_id=deny_space, access_denied=True, authorized_space_rechecked=True)
        return
    positive_space_probe(client)
    emit("negative_space_probe", space_id=deny_space, accessible=True)
    fail("The application can enumerate the negative-test Wiki space.")


def docker_json(arguments):
    try:
        result = subprocess.run(["docker", *arguments], cwd=REPO_ROOT, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        fail("Cannot inspect the local guarded app container.")
    if result.returncode:
        fail("Docker inspection failed; no credentials were submitted to WeKnora.")
    try:
        value = json.loads(result.stdout)
    except ValueError:
        fail("Docker inspection returned invalid JSON.")
    if not isinstance(value, list) or len(value) != 1:
        fail("Docker inspection did not resolve exactly one object.")
    return mapping(value[0])


def require_guard(base):
    container = docker_json(["container", "inspect", "WeKnora-app"])
    image_id = container.get("Image")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        fail("Cannot identify the running app image.")
    image = docker_json(["image", "inspect", image_id])
    labels = mapping(mapping(image.get("Config")).get("Labels") or {})
    if labels.get(GUARD_LABEL) != "1":
        fail("The running app image lacks the required Feishu guard label.")
    state = mapping(container.get("State"))
    if state.get("Running") is not True or mapping(state.get("Health") or {}).get("Status") != "healthy":
        fail("The guarded app container is not healthy.")
    values = mapping(container.get("Config")).get("Env")
    if not isinstance(values, list):
        fail("The app runtime environment cannot be verified.")
    env = dict(value.split("=", 1) for value in values if isinstance(value, str) and "=" in value)
    aes_key = env.get("SYSTEM_AES_KEY", "")
    if aes_key:
        SECRET_VALUES.add(aes_key)
    if env.get("FEISHU_DOCX_PARSE_MODE") != "export" or len(aes_key.encode("utf-8")) != 32:
        fail("The app must use export parsing and a 32-byte SYSTEM_AES_KEY.")
    ollama_url = urllib.parse.urlsplit(env.get("OLLAMA_BASE_URL", ""))
    if ollama_url.scheme != "http" or ollama_url.hostname != "ollama" or ollama_url.port != 11434:
        fail("The app must use the local Ollama container for embedding.")
    port = str(urllib.parse.urlsplit(base).port)
    ports = mapping(mapping(container.get("NetworkSettings")).get("Ports"))
    bindings = ports.get("8080/tcp") or []
    if not any(row.get("HostIp") == "127.0.0.1" and row.get("HostPort") == port for row in bindings):
        fail("The requested API port does not match the guarded app container.")
    emit("guard", image_label=True, export_mode=True, encryption_key_valid=True, local_embedding=True)


def login(settings):
    client = HttpClient(settings.get("SMOKE_BASE_URL", "http://127.0.0.1:8080"))
    result = mapping(client.request("/api/v1/auth/login", "POST", {
        "email": required(settings, "SMOKE_ADMIN_EMAIL"),
        "password": required(settings, "SMOKE_ADMIN_PASSWORD"),
    }))
    token = result.get("token")
    if not isinstance(token, str) or not token:
        fail("WeKnora login returned no access token.")
    client.token = token
    SECRET_VALUES.add(token)
    if isinstance(result.get("refresh_token"), str):
        SECRET_VALUES.add(result["refresh_token"])
    if not mapping(result.get("active_tenant") or {}).get("id"):
        fail("The administrator has no active workspace.")
    return client


def local_data(client, path, method="GET", payload=None):
    result = mapping(client.request(path, method, payload))
    if result.get("success") is not True or "data" not in result:
        fail("Unexpected WeKnora response envelope.")
    return result["data"]


def ingestion_policy(kb, read_only):
    summary_configured = bool(kb.get("summary_model_id"))
    return {"validation_mode": "read_only" if read_only else "sync",
            "summary_model_configured": summary_configured, "sync_allowed": not summary_configured}


def knowledge_base(client, create=False, read_only=False):
    if create and read_only:
        fail("Read-only verification cannot create a knowledge base.")
    models = local_data(client, "/api/v1/models")
    if not isinstance(models, list):
        fail("Model list is invalid.")
    candidates = [model for model in models if model.get("id") == "local-embedding"]
    if len(candidates) != 1 or any(candidates[0].get(key) != value for key, value in {
        "type": "Embedding", "source": "local", "status": "active", "name": "bge-m3:latest",
    }.items()):
        fail("The active local BGE-M3 embedding model is unavailable.")
    rows = local_data(client, "/api/v1/knowledge-bases")
    if not isinstance(rows, list):
        fail("Knowledge base list is invalid.")
    matches = [mapping(row) for row in rows if row.get("name") in KB_NAMES or row.get("description") == KB_MARKER]
    if len(matches) > 1:
        fail("Multiple managed knowledge bases exist; refusing ambiguous selection.")
    if not matches:
        if not create:
            fail("The managed Feishu knowledge base does not exist.")
        kb = mapping(local_data(client, "/api/v1/knowledge-bases", "POST", {
            "name": KB_NAME, "type": "document", "description": KB_MARKER,
            "embedding_model_id": "local-embedding", "summary_model_id": "",
            "chunking_config": {"chunk_size": 768, "chunk_overlap": 64, "separators": ["\n\n", "\n", "。"]},
            "question_generation_config": {"enabled": False},
            "auto_tag_config": {"enabled": False}, "profile_config": {"enabled": False},
            "vlm_config": {"enabled": False}, "asr_config": {"enabled": False},
            "indexing_strategy": {"vector_enabled": True, "keyword_enabled": True, "wiki_enabled": False, "graph_enabled": False},
        }))
    else:
        kb = mapping(local_data(client, "/api/v1/knowledge-bases/" + identifier(matches[0].get("id"))))
    if kb.get("name") not in KB_NAMES or kb.get("description") != KB_MARKER or kb.get("type") != "document":
        fail("The managed knowledge base name or ownership marker conflicts.")
    if kb.get("embedding_model_id") != "local-embedding":
        fail("The managed knowledge base must use local embedding.")
    strategy = mapping(kb.get("indexing_strategy"))
    if strategy.get("vector_enabled") is not True or strategy.get("keyword_enabled") is not True:
        fail("The managed knowledge base must enable vector and keyword indexes.")
    if strategy.get("wiki_enabled") or strategy.get("graph_enabled"):
        fail("The managed knowledge base enables unexpected enrichment.")
    for key in ("question_generation_config", "auto_tag_config", "profile_config", "vlm_config", "asr_config"):
        if mapping(kb.get(key) or {}).get("enabled"):
            fail("Model enrichment must remain disabled for this knowledge base.")
    vlm = mapping(kb.get("vlm_config") or {})
    if vlm.get("model_name") or vlm.get("base_url") or mapping(kb.get("image_processing_config") or {}).get("model_id"):
        fail("Legacy image model enrichment must remain disabled.")
    identifier(kb.get("id"))
    policy = ingestion_policy(kb, read_only)
    emit("ingestion_policy", **policy)
    if not read_only and not policy["sync_allowed"]:
        fail("Sync is blocked because a summary model is configured; read-only verify or snapshot remains available.")
    emit("knowledge_base", knowledge_base_id=kb["id"])
    return kb


def datasource(client, kb, settings, create=False):
    rows = client.request("/api/v1/datasource?" + urllib.parse.urlencode({"kb_id": kb["id"]}))
    if not isinstance(rows, list) or len(rows) > 1:
        fail("The managed knowledge base must have exactly one Feishu data source.")
    if not rows:
        if not create:
            fail("The managed Feishu data source does not exist.")
        ds = mapping(client.request("/api/v1/datasource", "POST", {
            "knowledge_base_id": kb["id"], "name": KB_NAME, "type": "feishu",
            "config": {
                "type": "feishu", "resource_ids": [SPACE_ID],
                "credentials": {"app_id": required(settings, "FEISHU_APP_ID"), "app_secret": required(settings, "FEISHU_APP_SECRET")},
                "settings": {"timezone": "Asia/Shanghai", "allowed_space_id": SPACE_ID},
            },
            "sync_mode": "incremental", "sync_schedule": "", "status": "paused",
            "sync_deletions": False, "conflict_strategy": "overwrite",
        }))
    else:
        ds = mapping(rows[0])
    latest = ds.get("latest_sync_log")
    ds = mapping(client.request("/api/v1/datasource/" + identifier(ds.get("id"))))
    if latest:
        ds["latest_sync_log"] = latest
    cfg = mapping(ds.get("config"))
    expected = {"knowledge_base_id": kb["id"], "type": "feishu", "status": "paused",
                "sync_mode": "incremental", "sync_schedule": "", "conflict_strategy": "overwrite", "sync_deletions": False}
    if ds.get("name") not in KB_NAMES or any(ds.get(key) != value for key, value in expected.items()):
        fail("The existing data source has unexpected sync settings; no changes were made to it.")
    if cfg.get("type") != "feishu" or cfg.get("resource_ids") != [SPACE_ID]:
        fail("The data source is not restricted to the authorized Wiki space.")
    if mapping(cfg.get("settings")).get("allowed_space_id") != SPACE_ID:
        fail("The data source does not enable the required space guard.")
    if mapping(cfg.get("settings")).get("base_url") not in (None, "", "https://open.feishu.cn"):
        fail("The data source API origin is not allowed.")
    if create and rows:
        local_data(client, "/api/v1/datasource/" + ds["id"] + "/credentials", "PUT", {
            "credentials": {"app_id": required(settings, "FEISHU_APP_ID"), "app_secret": required(settings, "FEISHU_APP_SECRET")},
        })
        emit("data_source_credentials", refreshed=True)
    emit("data_source", data_source_id=ds["id"], space_id=SPACE_ID, sync_mode="incremental", scheduled=False, sync_deletions=False)
    return ds


def documents(client, kb_id, ds_id, nodes):
    all_rows = []
    for page in range(1, 10001):
        result = mapping(client.request("/api/v1/knowledge-bases/" + kb_id + "/knowledge?" + urllib.parse.urlencode({"page": page, "page_size": 100})))
        batch = result.get("data")
        total = result.get("total")
        if not isinstance(batch, list) or type(total) is not int or total < 0:
            fail("Invalid knowledge pagination response.")
        all_rows.extend(batch)
        if len(all_rows) >= total:
            if len(all_rows) != total:
                fail("Knowledge changed during pagination; retry verification.")
            break
        if not batch:
            fail("Knowledge pagination ended before the advertised total.")
    else:
        fail("Knowledge pagination limit reached.")
    by_external = {}
    ids = set()
    for row in all_rows:
        row = mapping(row)
        metadata = mapping(row.get("metadata"))
        knowledge_id = identifier(row.get("id"))
        external_id = metadata.get("external_id")
        if not isinstance(external_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:#(?:file|image)#[A-Za-z0-9_-]+)?", external_id):
            fail("Invalid external knowledge identifier.")
        node_id = external_id.split("#", 1)[0]
        if row.get("channel") != "feishu" or metadata.get("datasource_id") != ds_id:
            fail("Unrelated knowledge exists in the managed knowledge base.")
        if metadata.get("space_id") != SPACE_ID or metadata.get("source_resource_id") != SPACE_ID:
            fail("Knowledge provenance is outside the authorized Wiki space.")
        if node_id not in nodes or nodes[node_id]["object_type"] not in SUPPORTED_TYPES:
            fail("Stored knowledge does not belong to the current supported-node inventory.")
        if external_id in by_external or knowledge_id in ids:
            fail("Duplicate knowledge or external identifiers detected.")
        status = row.get("parse_status")
        if status not in ("pending", "processing", "finalizing", "completed", "failed", "cancelled"):
            fail("Unknown document parse status.")
        file_hash = row.get("file_hash") or ""
        if not isinstance(file_hash, str) or file_hash and not re.fullmatch(r"[A-Fa-f0-9]{16,128}", file_hash):
            fail("Invalid document content hash.")
        by_external[external_id] = {"knowledge_id": knowledge_id, "external_id": external_id, "parse_status": status,
                                    "file_hash": file_hash, "source_updated_at": timestamp(metadata.get("source_updated_at")),
                                    "updated_at": timestamp(row.get("updated_at"))}
        ids.add(knowledge_id)
    return by_external


def await_documents(client, kb_id, ds_id, nodes, timeout):
    expected = {node_id for node_id, row in nodes.items() if row["object_type"] in SUPPORTED_TYPES}
    if not expected:
        fail("The space contains no supported documents; ingestion is not verified.")
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        rows = documents(client, kb_id, ds_id, nodes)
        counts = dict(sorted(collections.Counter(row["parse_status"] for row in rows.values()).items()))
        missing = expected - set(rows)
        state = (len(rows), len(missing), tuple(counts.items()))
        if state != previous:
            emit("parsing", documents=len(rows), missing_nodes=len(missing), statuses=counts)
            previous = state
        if counts.get("failed", 0) or counts.get("cancelled", 0):
            fail("One or more imported documents failed or were cancelled.")
        if not missing and counts.get("completed", 0) == len(rows):
            for row in sorted(rows.values(), key=lambda item: item["external_id"]):
                emit("document", **row)
            emit("verified", space_id=SPACE_ID, documents=len(rows), supported_nodes=len(expected), all_parsed=True)
            return rows
        if time.monotonic() >= deadline:
            fail("Document parsing or inventory coverage did not complete before the timeout.")
        time.sleep(min(5, max(0, deadline - time.monotonic())))


def await_sync(client, ds, timeout):
    latest = ds.get("latest_sync_log") or {}
    if mapping(latest).get("status") == "running":
        log = latest
        emit("sync", status="reusing_running_job", sync_log_id=identifier(log.get("id")))
    else:
        log = mapping(client.request("/api/v1/datasource/" + ds["id"] + "/sync", "POST"))
    log_id = identifier(log.get("id"))
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        log = mapping(client.request("/api/v1/datasource/logs/" + log_id))
        if log.get("data_source_id") != ds["id"]:
            fail("Sync log belongs to another data source.")
        status = log.get("status")
        if status not in ("running", "success", "partial", "failed", "canceled"):
            fail("Unknown sync status.")
        counts = {}
        for key in ("items_total", "items_created", "items_updated", "items_skipped", "items_failed"):
            value = log.get(key, 0)
            if type(value) is not int or value < 0:
                fail("Invalid sync log counters.")
            counts[key] = value
        state = (status, tuple(counts.items()))
        if state != previous:
            emit("sync", sync_log_id=log_id, status=status, **counts)
            previous = state
        if status == "success":
            if counts["items_failed"]:
                fail("A successful sync log still reports failed items.")
            return counts
        if status != "running":
            fail("Sync failed, was partial, or was canceled; error bodies are suppressed.")
        if time.monotonic() >= deadline:
            fail("Sync did not finish before the timeout; it may still be running.")
        time.sleep(min(5, max(0, deadline - time.monotonic())))


def synchronize(settings, verify=False, timeout=7200):
    base = settings.get("SMOKE_BASE_URL", "http://127.0.0.1:8080")
    HttpClient(base)
    require_guard(base)
    feishu = FeishuClient(settings)
    permissions(feishu)
    nodes = inventory(feishu)
    if not any(row["object_type"] in SUPPORTED_TYPES for row in nodes.values()):
        fail("There are no supported documents to import.")
    client = login(settings)
    kb = knowledge_base(client, create=not verify, read_only=verify)
    ds = datasource(client, kb, settings, create=not verify)
    before = documents(client, kb["id"], ds["id"], nodes)
    counts = None
    if not verify:
        require_guard(base)
        counts = await_sync(client, ds, timeout)
    after = await_documents(client, kb["id"], ds["id"], nodes, timeout)
    if counts is not None and counts["items_total"] == 0:
        before_ids = {key: row["knowledge_id"] for key, row in before.items()}
        after_ids = {key: row["knowledge_id"] for key, row in after.items()}
        if before_ids != after_ids:
            fail("An unchanged incremental sync changed the document identifiers.")
        emit("incremental_noop", document_count_unchanged=True, document_ids_unchanged=True)
    return client, kb, ds, nodes, after


def snapshot_path(value, writing=False):
    temporary_root = REPO_ROOT / "temp"
    if temporary_root.resolve().parent != REPO_ROOT:
        fail("Repository temp directory must not redirect outside its expected location.")
    path = pathlib.Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    try:
        path.relative_to(temporary_root)
    except ValueError:
        fail("Snapshot paths must stay inside the repository temp directory.")
    if path.suffix.lower() != ".json" or path == temporary_root:
        fail("Snapshot path must name a JSON file inside repository temp.")
    if writing and path.exists():
        fail("Snapshot already exists; choose a new output file.")
    if not writing and (not path.is_file() or path.stat().st_size > 10000000):
        fail("Baseline snapshot is missing or too large.")
    return path


def write_snapshot(path, kb, ds, nodes, rows):
    payload = {"version": 1, "space_id": SPACE_ID, "knowledge_base_id": kb["id"], "data_source_id": ds["id"],
               "ingestion_policy": ingestion_policy(kb, read_only=True), "documents": []}
    for external_id, row in sorted(rows.items()):
        node = nodes[external_id.split("#", 1)[0]]
        payload["documents"].append({key: row[key] for key in ("external_id", "knowledge_id", "file_hash", "source_updated_at", "updated_at")})
        payload["documents"][-1].update({"node_edit_time": node["node_edit_time"], "object_edit_time": node["object_edit_time"]})
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if any(secret and secret in encoded for secret in SECRET_VALUES):
        fail("Snapshot unexpectedly contains a credential value; refusing to save it.")
    checked = snapshot_path(str(path), writing=True)
    checked.parent.mkdir(parents=True, exist_ok=True)
    try:
        with checked.open("x", encoding="utf-8") as output:
            output.write(encoded)
    except FileExistsError:
        fail("Snapshot already exists; refusing to overwrite it.")
    emit("snapshot", documents=len(rows), saved=True)


def verify_marker(client, kb, ds, nodes, rows, node_id, expected_text, baseline=None):
    node_id = identifier(node_id)
    if node_id not in nodes or node_id not in rows:
        fail("The requested probe node is not present in both inventory and local documents.")
    if not isinstance(expected_text, str) or not expected_text.strip() or len(expected_text) > 256:
        fail("The expected probe text must contain between 1 and 256 characters.")
    row = rows[node_id]
    if row["parse_status"] != "completed":
        fail("The probe document has not completed parsing.")
    changes = {}
    if baseline is not None:
        try:
            previous = mapping(json.loads(baseline.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            fail("Cannot read the baseline snapshot.")
        if previous.get("version") != 1 or previous.get("space_id") != SPACE_ID or previous.get("knowledge_base_id") != kb["id"] or previous.get("data_source_id") != ds["id"]:
            fail("Baseline snapshot belongs to another managed space or data source.")
        entries = previous.get("documents")
        if not isinstance(entries, list):
            fail("Baseline snapshot document list is invalid.")
        matches = [mapping(item) for item in entries if mapping(item).get("external_id") == node_id]
        if len(matches) != 1:
            fail("Baseline snapshot does not contain exactly one probe document.")
        old = matches[0]
        if not isinstance(old.get("file_hash"), str) or not re.fullmatch(r"[A-Fa-f0-9]{16,128}", old["file_hash"]):
            fail("Baseline snapshot has no valid probe content hash.")
        changes = {
            "content_hash_changed": bool(row["file_hash"]) and old["file_hash"] != row["file_hash"],
            "source_updated_at_changed": timestamp(old.get("source_updated_at")) != row["source_updated_at"],
            "knowledge_id_changed": old.get("knowledge_id") != row["knowledge_id"],
            "node_edit_time_changed": node_timestamp(old.get("node_edit_time")) != nodes[node_id]["node_edit_time"],
            "object_edit_time_changed": node_timestamp(old.get("object_edit_time")) != nodes[node_id]["object_edit_time"],
        }
        if not changes["content_hash_changed"] and not changes["source_updated_at_changed"]:
            fail("Probe content hash and source timestamp are unchanged from the baseline.")
    result = local_data(client, "/api/v1/knowledge-bases/" + kb["id"] + "/hybrid-search", "POST", {
        "query_text": expected_text, "knowledge_ids": [row["knowledge_id"]], "match_count": 100,
        "disable_vector_match": True, "disable_keywords_match": False, "keyword_threshold": 0,
    })
    if not isinstance(result, list):
        fail("Probe retrieval returned an invalid result list.")
    matched = []
    for item in result:
        item = mapping(item)
        if item.get("knowledge_id") != row["knowledge_id"]:
            fail("Probe retrieval escaped the selected knowledge document.")
        content = item.get("content")
        if isinstance(content, str) and expected_text in content:
            matched.append(identifier(item.get("id")))
    if not matched:
        fail("The expected probe text was not found in indexed chunks of the selected node.")
    emit("probe_verified", node_id=node_id, knowledge_id=row["knowledge_id"], matching_chunk_ids=sorted(set(matched)),
         expected_text_found=True, baseline_compared=baseline is not None, **changes)


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    permission_parser = actions.add_parser("permissions")
    permission_parser.add_argument("--apply", action="store_true")
    actions.add_parser("inventory")
    audit_parser = actions.add_parser("audit-access")
    audit_parser.add_argument("--deny-space")
    document_audit_parser = actions.add_parser("audit-documents", description="Check view and export permission for docx Wiki nodes in the authorized space; other object types are not audited.")
    document_audit_parser.add_argument("--node-token", action="append", dest="node_tokens")
    for name in ("sync", "verify", "snapshot"):
        command = actions.add_parser(name)
        command.add_argument("--timeout", type=int, default=7200)
        if name == "verify":
            command.add_argument("--node-token")
            command.add_argument("--expect-text")
            command.add_argument("--baseline")
        elif name == "snapshot":
            command.add_argument("--output", required=True)
    args = parser.parse_args()
    settings = load_settings()
    if args.action == "permissions":
        permissions(FeishuClient(settings), args.apply)
    elif args.action == "inventory":
        inventory(FeishuClient(settings))
    elif args.action == "audit-access":
        audit_access(FeishuClient(settings), args.deny_space)
    elif args.action == "audit-documents":
        audit_documents(FeishuClient(settings), args.node_tokens)
    else:
        if not 1 <= args.timeout <= 86400:
            fail("Timeout must be between 1 and 86400 seconds.")
        output = snapshot_path(args.output, writing=True) if args.action == "snapshot" else None
        baseline = snapshot_path(args.baseline) if args.action == "verify" and args.baseline else None
        if args.action == "verify" and (bool(args.node_token) != bool(args.expect_text) or baseline is not None and not args.node_token):
            fail("Probe verification requires both --node-token and --expect-text; --baseline also requires them.")
        client, kb, ds, nodes, rows = synchronize(settings, verify=args.action != "sync", timeout=args.timeout)
        if output is not None:
            write_snapshot(output, kb, ds, nodes, rows)
        if args.action == "verify" and args.node_token:
            verify_marker(client, kb, ds, nodes, rows, args.node_token, args.expect_text, baseline)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        main()
    except ApiError as exc:
        emit("failure", message=str(exc), http_status=exc.code if exc.kind == "http" else None, api_code=exc.api_code)
        sys.exit(1)
    except SafeError as exc:
        emit("failure", message=str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        emit("failure", message="Interrupted.")
        sys.exit(130)
    except Exception:
        emit("failure", message="Unexpected local error; details suppressed to protect credentials.")
        sys.exit(1)
