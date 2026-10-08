#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v cygpath >/dev/null 2>&1; then
  repo_root="$(cygpath -w "$repo_root")"
fi
python_cmd=()
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    python_cmd=("$candidate")
    break
  fi
done
if (( ${#python_cmd[@]} == 0 )); then
  if command -v py >/dev/null 2>&1 && py -3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    python_cmd=(py -3)
  else
    printf '%s\n' 'FAIL: Python 3.9+ is required.' >&2
    exit 1
  fi
fi
exec "${python_cmd[@]}" -u - "$repo_root" "$@" <<'PY'
import argparse
import html
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
import uuid

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
root = pathlib.Path(sys.argv[1])
brand = json.loads((root / "branding" / "brand.json").read_text(encoding="utf-8"))
parser = argparse.ArgumentParser(description=f"Verify local {brand['productName']} document ingestion, retrieval and cited DeepSeek answers.")
parser.add_argument("--prepare-only", action="store_true", help="Verify ingestion and retrieval only; skip paid DeepSeek generation.")
args = parser.parse_args(sys.argv[2:])
settings = {}
env_path = root / ".env"
if env_path.is_file():
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if not match:
            continue
        name, value = match.groups()
        value = value.strip()
        if value.startswith(("'", '"')):
            try:
                parts = shlex.split(value, comments=True, posix=True)
                value = " ".join(parts)
            except ValueError:
                print(f"FAIL: Invalid quoting in .env variable {name}.", file=sys.stderr)
                sys.exit(1)
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        settings[name] = value
settings.update(os.environ)
for _ in range(3):
    for name, value in list(settings.items()):
        settings[name] = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: settings.get(m[1], m[0]), value)
secrets = [value for name, value in settings.items() if re.search(r"PASSWORD|SECRET|TOKEN|API_KEY", name, re.I) and value]
token = ""
base = settings.get("SMOKE_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
timeout = int(settings.get("SMOKE_TIMEOUT_SECONDS", "900"))
chat_model = settings.get("SMOKE_CHAT_MODEL_ID", "local-deepseek-chat")
embedding_model = settings.get("SMOKE_EMBEDDING_MODEL_ID", "local-embedding")
marker = brand["productSlug"] + "-local-smoke-v1"
question = "根据星尘计划启动备忘录，谁是首席科学家、研发的推进系统叫什么、原型机测试目标年份是哪一年？请逐项回答并引用知识库来源。"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def safe(value):
    text = str(value)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def fail(message):
    raise RuntimeError(message)


def require_setting(name):
    value = settings.get(name, "").strip()
    if not value or re.search(r"replace|changeme|your[-_ ]|placeholder|^<|\$\{", value, re.I):
        fail(f"Set {name} in .env before running this check.")
    return value


def request(path, method="GET", payload=None, raw=None, content_type=None, stream=False):
    headers = {"Accept": "text/event-stream" if stream else "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if payload is not None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(base + path, data=raw, headers=headers, method=method)
    try:
        response = opener.open(req, timeout=min(timeout, 300) if stream else 60)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read())
            detail = body.get("message", "")
            if isinstance(body.get("error"), dict):
                detail = body["error"].get("message", detail)
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f"{method} {path}: HTTP {exc.code}. {safe(detail)}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError(f"{method} {path}: connection failed or timed out.") from None
    if stream:
        if "text/event-stream" not in response.headers.get("Content-Type", ""):
            response.close()
            fail("Chat endpoint did not return an SSE stream.")
        return response
    with response:
        result = json.load(response)
    if isinstance(result, dict) and result.get("success") is False:
        fail(f"{method} {path}: API reported failure. {safe(result.get('message', ''))}")
    return result


def data(path, method="GET", payload=None):
    return request(path, method, payload).get("data")


def wait_healthy():
    deadline = time.monotonic() + min(timeout, 300)
    while time.monotonic() < deadline:
        try:
            with opener.open(base + "/health", timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(2)
    fail("The app health endpoint did not become ready.")


def compose_app(arguments):
    command = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.local.yml"]
    current = subprocess.run([*command, "ps", "--all", "-q", "app"], cwd=root, capture_output=True, text=True, timeout=30)
    if current.returncode != 0:
        fail("Cannot inspect the current app deployment.")
    container_id = current.stdout.strip()
    if container_id:
        branded = subprocess.run(["docker", "inspect", "--format", '{{ index .Config.Labels "io.boyankb.branding" }}', container_id], capture_output=True, text=True, timeout=30)
        if branded.returncode != 0:
            fail("Cannot inspect the current app branding configuration.")
        if branded.stdout.strip() == "1":
            if not (root / "docker-compose.branding.yml").is_file():
                fail("The active brand deployment requires docker-compose.branding.yml.")
            command.extend(["-f", "docker-compose.branding.yml"])
    completed = subprocess.run([*command, *arguments, "app"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if completed.returncode:
        fail("App compose operation failed: " + safe(completed.stderr[-2000:]))


def paginated(path):
    rows = []
    for page in range(1, 101):
        separator = "&" if "?" in path else "?"
        result = request(f"{path}{separator}page={page}&page_size=100")
        batch = result.get("data") or []
        if not isinstance(batch, list):
            fail(f"Unexpected list response from {path}.")
        rows.extend(batch)
        if len(batch) < 100 or len(rows) >= result.get("total", len(rows)):
            return rows
    fail(f"Pagination limit reached for {path}.")


def unique_named(rows, name, owned=False):
    matches = [row for row in rows if row.get("name") == name]
    if len(matches) > 1:
        fail(f"Multiple resources have the reserved smoke name {name}.")
    if matches and owned and matches[0].get("description") != marker:
        fail(f"Resource name {name} is already used outside this smoke test.")
    return matches[0] if matches else None


def upload(kb_id, source):
    boundary = brand["productSlug"] + "-smoke-" + uuid.uuid4().hex
    fields = {"enable_multimodel": "false", "channel": "api", "process_config": json.dumps({"summary_enabled": False, "graph_enabled": False, "question_generation_config": {"enabled": False}})}
    body = bytearray()
    for name, value in fields.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{source.name}"\r\nContent-Type: text/markdown\r\n\r\n'.encode())
    body.extend(source.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return request(f"/api/v1/knowledge-bases/{kb_id}/knowledge/file", "POST", raw=bytes(body), content_type="multipart/form-data; boundary=" + boundary)["data"]


def read_answer(session_id, agent_id, kb_id):
    payload = {"query": question, "knowledge_base_ids": [kb_id], "agent_id": agent_id, "agent_enabled": False, "web_search_enabled": False, "summary_model_id": chat_model, "disable_title": True, "channel": "api"}
    response = request(f"/api/v1/knowledge-chat/{session_id}", "POST", payload, stream=True)
    parts, references, event_lines = [], {}, []
    complete = False
    deadline = time.monotonic() + timeout
    with response:
        while time.monotonic() < deadline:
            line = response.readline()
            if not line:
                break
            text = line.decode("utf-8").rstrip("\r\n")
            if text.startswith("data:"):
                event_lines.append(text[5:].lstrip())
            elif not text and event_lines:
                event = json.loads("\n".join(event_lines))
                event_lines = []
                kind = event.get("response_type", "")
                if kind == "error":
                    fail("Chat SSE error: " + safe(event.get("content", "unknown error")))
                if kind == "answer":
                    parts.append(event.get("content", ""))
                for ref in event.get("knowledge_references") or []:
                    references[ref.get("id", "")] = ref
                if kind == "complete":
                    complete = True
                    break
    if not complete:
        fail("Chat stream ended without response_type=complete.")
    answer = "".join(parts).strip()
    if not answer:
        fail("The model returned an empty answer.")
    return answer, list(references.values())


def main():
    global token
    email = require_setting("SMOKE_ADMIN_EMAIL")
    password = require_setting("SMOKE_ADMIN_PASSWORD")
    if not args.prepare_only:
        require_setting("DEEPSEEK_API_KEY")
        print("Refreshing app container environment from .env.")
        compose_app(["up", "-d", "--no-deps", "--no-build"])
    source = root / "testdata/wiki_test/doc1_stardust_memo.md"
    if not source.is_file():
        fail("Missing testdata/wiki_test/doc1_stardust_memo.md.")
    wait_healthy()
    login_payload = {"email": email, "password": password}
    try:
        login = request("/api/v1/auth/login", "POST", login_payload)
    except RuntimeError as exc:
        if "HTTP 401" not in str(exc):
            raise
        request("/api/v1/auth/register", "POST", {"username": "local-smoke-admin", **login_payload})
        print("Registered smoke administrator account.")
        login = request("/api/v1/auth/login", "POST", login_payload)
    token = login.get("token", "")
    if not token:
        fail("Login returned no access token.")
    secrets.extend([token, login.get("refresh_token", "")])
    if not login.get("user", {}).get("is_system_admin"):
        if settings.get("WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL", "").strip().lower() != email.lower():
            fail("WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL must match SMOKE_ADMIN_EMAIL before bootstrap.")
        print("Restarting app after registration to apply system-admin bootstrap.")
        compose_app(["restart"])
        wait_healthy()
        login = request("/api/v1/auth/login", "POST", login_payload)
        token = login.get("token", "")
        secrets.extend([token, login.get("refresh_token", "")])
    principal = data("/api/v1/auth/me")
    if not principal.get("user", {}).get("is_system_admin"):
        fail("System-admin bootstrap did not promote this account; an existing administrator may already exist.")
    active_tenant = login.get("active_tenant", {}).get("id")
    if not active_tenant:
        fail("The administrator has no active workspace; configure personal workspace provisioning.")
    print(f"Verified system administrator and workspace {active_tenant}.")
    models = {row["id"]: row for row in data("/api/v1/models") or []}
    for model_id, model_type in ((embedding_model, "Embedding"), (chat_model, "KnowledgeQA")):
        model = models.get(model_id)
        if not model or model.get("type") != model_type or model.get("status") != "active":
            fail(f"Required active model {model_id} ({model_type}) was not provisioned.")
    if models[embedding_model].get("source") != "local":
        fail("The embedding model must use the local Ollama source for this smoke test.")
    if models[chat_model].get("source") != "remote":
        fail("The chat model must use the remote DeepSeek source for this smoke test.")
    chat_parameters = models[chat_model].get("parameters") or {}
    if chat_parameters.get("provider") != "deepseek" or urllib.parse.urlparse(chat_parameters.get("base_url", "")).hostname != "api.deepseek.com":
        fail("The chat model must use the DeepSeek provider and api.deepseek.com endpoint.")
    kb_name = "local-smoke-stardust"
    kb = unique_named(data("/api/v1/knowledge-bases") or [], kb_name, True)
    if kb is None:
        kb = data("/api/v1/knowledge-bases", "POST", {"name": kb_name, "description": marker, "type": "document", "embedding_model_id": embedding_model, "summary_model_id": chat_model, "chunking_config": {"chunk_size": 768, "chunk_overlap": 64, "separators": ["\n\n", "\n", "。"]}, "indexing_strategy": {"vector_enabled": True, "keyword_enabled": True, "wiki_enabled": False, "graph_enabled": False}})
    if kb.get("embedding_model_id") != embedding_model or not kb.get("indexing_strategy", {}).get("vector_enabled") or not kb.get("indexing_strategy", {}).get("keyword_enabled"):
        fail("The existing smoke knowledge base has incompatible model/index settings.")
    kb_id = kb["id"]
    documents = paginated(f"/api/v1/knowledge-bases/{kb_id}/knowledge")
    matches = [row for row in documents if row.get("file_name") == source.name]
    if len(matches) > 1:
        fail("Multiple smoke documents exist; resolve the duplicate before retrying.")
    document = matches[0] if matches else upload(kb_id, source)
    knowledge_id = document["id"]
    if document.get("parse_status") in ("failed", "cancelled"):
        print("Retrying the existing failed smoke document without uploading a duplicate.")
        document = data(f"/api/v1/knowledge/{knowledge_id}/reparse", "POST", {"process_config": {"summary_enabled": False, "graph_enabled": False, "question_generation_config": {"enabled": False}}})
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        document = data(f"/api/v1/knowledge/{knowledge_id}")
        status = document.get("parse_status")
        if status != previous:
            print(f"Document {knowledge_id}: {status}.")
            previous = status
        if status == "completed":
            break
        if status in ("failed", "cancelled", "deleting"):
            fail("Document processing stopped: " + status + ". " + safe(document.get("error_message", "")))
        time.sleep(3)
    else:
        fail("Document processing timed out before completed.")
    for mode in ("vector", "keyword", "hybrid"):
        results = data(f"/api/v1/knowledge-bases/{kb_id}/hybrid-search", "POST", {"query_text": "星尘计划 幽能引擎 2150", "match_count": 10, "vector_threshold": 0, "keyword_threshold": 0, "disable_vector_match": mode == "keyword", "disable_keywords_match": mode == "vector"}) or []
        hits = [row for row in results if row.get("knowledge_id") == knowledge_id and "2150" in row.get("content", "")]
        if not hits:
            fail(f"Real {mode} retrieval did not return the uploaded document and its target year.")
        print(f"Verified {mode} retrieval: {len(hits)} matching chunk(s).")
    if args.prepare_only:
        print("PREPARE_ONLY: registration, system-admin bootstrap, parsing and retrieval passed; DeepSeek generation and citations were NOT tested. Full smoke test has NOT passed.")
        return
    agent_name = "local-smoke-rag"
    agent = unique_named(data("/api/v1/agents") or [], agent_name, True)
    agent_payload = {"name": agent_name, "description": marker, "config": {"agent_mode": "quick-answer", "model_id": chat_model, "rerank_model_id": "", "system_prompt_id": "default_kb", "context_template_id": "default_context", "kb_selection_mode": "selected", "knowledge_bases": [kb_id], "temperature": 0, "max_completion_tokens": 1024, "thinking": False, "reasoning_effort": "off", "citation_enabled": True, "web_search_enabled": False, "multi_turn_enabled": False, "history_turns": 1, "enable_rewrite": False, "enable_query_expansion": False, "embedding_top_k": 10, "vector_threshold": 0.01, "keyword_threshold": 0.01, "rerank_top_k": 10, "rerank_threshold": 0, "fallback_strategy": "fixed", "fallback_response": "未检索到测试文档。", "mcp_selection_mode": "none", "skills_selection_mode": "none"}}
    agent = data(f"/api/v1/agents/{agent['id']}" if agent else "/api/v1/agents", "PUT" if agent else "POST", agent_payload)
    sessions = [row for row in paginated("/api/v1/sessions") if row.get("title") == "local-smoke-stardust" and row.get("description") == marker]
    if len(sessions) > 1:
        fail("Multiple smoke sessions exist; resolve the duplicate before retrying.")
    session = sessions[0] if sessions else data("/api/v1/sessions", "POST", {"title": "local-smoke-stardust", "description": marker})
    print("Requesting a real DeepSeek answer with knowledge-base retrieval.")
    answer, references = read_answer(session["id"], agent["id"], kb_id)
    print("Answer:\n" + safe(answer))
    expected = [ref for ref in references if ref.get("knowledge_id") == knowledge_id]
    citation_ids = set(html.unescape(value) for value in re.findall(r'<kb\b[^>]*\bchunk_id=["\x27]([^"\x27]+)["\x27][^>]*/?>', answer))
    cited = [ref for ref in expected if ref.get("id") in citation_ids]
    if not cited:
        fail("Answer citations do not match SSE references for the uploaded test document.")
    if "2150" not in answer or not any(value in answer for value in ("幽能引擎", "Psionic Engine")) or not any(value in answer for value in ("艾琳", "Elara Cole")):
        fail("The answer does not contain all three expected source facts.")
    for ref in cited:
        print(f"Verified citation: knowledge_id={knowledge_id}, chunk_id={ref['id']}.")
    print("PASS: administrator login, document parsing, vector/keyword/hybrid retrieval, real DeepSeek answer and source citations verified.")


try:
    main()
except KeyboardInterrupt:
    print("FAIL: interrupted.", file=sys.stderr)
    sys.exit(130)
except Exception as exc:
    print("FAIL: " + safe(exc), file=sys.stderr)
    sys.exit(1)
PY
