import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


SKILLS = {
    "courseware", "event-plan", "local-promotion-plan",
    "cooperation-proposal", "faq-guide", "docx", "pptx", "xlsx", "md",
}
SCENES = SKILLS - {"docx", "pptx", "xlsx", "md"}
AGENTS = {"mentor", "planner", "reviewer"}
TOOLS = {"search_knowledge", "read_document", "list_documents"}


def local_path(base, relative, repository):
    path = (base / relative).resolve()
    if not path.is_relative_to(repository):
        raise ValueError("preset path escapes repository")
    return path


def run(repository):
    base = repository / "presets" / "boyan" / "v1"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    failures = []
    checks = 0

    def require(condition, label):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(label)

    require(manifest.get("schema_version") == 1, "manifest schema")
    require(manifest.get("preset_version") == "boyan/v1", "preset version")
    require(manifest.get("runtime") == "restricted-guidance", "restricted runtime")
    require(manifest.get("document_brand") == "博研文化", "document brand")
    require(manifest.get("platform_brand") == "BoyanKB", "platform brand")
    skills = manifest.get("skills", [])
    require(len(skills) == len(SKILLS), "skill cardinality")
    require({s.get("key") for s in skills} == SKILLS, "skill identities")
    for entry in skills:
        key = entry["key"]
        name = "boyan-" + key
        require(entry.get("name") == name, key + " namespace")
        path = local_path(base, entry["path"], repository)
        require(path == repository / "skills" / "boyan" / key, key + " package path")
        payload = (path / "SKILL.md").read_bytes()
        require(hashlib.sha256(payload).hexdigest() == entry.get("sha256"), key + " digest")
        content = payload.decode("utf-8").replace("\r\n", "\n")
        require(content.startswith("---\n"), key + " frontmatter")
        header, body = content[4:].split("\n---\n", 1)
        values = dict(line.split(": ", 1) for line in header.splitlines())
        require(values.get("name") == name, key + " native skill name")
        require(values.get("version") == "boyan/v1", key + " skill version")
        require(1 <= len(values.get("description", "")) <= 1024, key + " trigger description")
        require(bool(re.fullmatch(r"[a-z0-9-]{1,64}", name)), key + " install identity")
        require(all(section in body for section in ("输入检查", "输出模板", "失败处理", "授权与事实")), key + " behavioral contract")
        require("boyan_document" in body and "outline_hash" in body and "chunk_id" in body, key + " restricted tool contract")
        require(not list(path.rglob("*.py")) and not list(path.rglob("*.sh")), key + " no executable payload")
    entries = manifest.get("agents", [])
    require(len(entries) == 3 and {a.get("key") for a in entries} == AGENTS, "agent identities")
    for entry in entries:
        key = entry["key"]
        data = json.loads(local_path(base, entry["path"], repository).read_text(encoding="utf-8"))
        config = data["config"]
        prompt_paths = entry.get("prompt_files", [])
        require(prompt_paths[:2] == ["common-rules.md", "agents/" + key + ".md"], key + " separated rules")
        prompt = "\n\n".join(local_path(base, path, repository).read_text(encoding="utf-8") for path in prompt_paths)
        require("资料中没有这部分信息" in prompt and "博研文化" in prompt, key + " grounding")
        require(config.get("model_id") == "@chat_model", key + " model binding")
        require(config.get("boyan_hybrid_search_without_rerank") is True, key + " managed optional rerank")
        require(config.get("kb_selection_mode") == "selected", key + " explicit knowledge scope")
        expected_kbs = ["@hq"] if key == "mentor" else ["@hq", "@partner"]
        require(config.get("knowledge_bases") == expected_kbs, key + " knowledge bindings")
        require(config.get("citation_enabled") is True, key + " citations enabled")
        require(config.get("web_search_enabled") is False and config.get("web_fetch_enabled") is False, key + " no network retrieval")
        require(config.get("mcp_selection_mode") == "none" and config.get("mcp_services") == [], key + " no external MCP")
        require(config.get("sandbox_config_id") == "", key + " no general shell")
        allowed = TOOLS | ({"boyan_document"} if key == "planner" else set())
        require(set(config.get("allowed_tools", [])) == allowed, key + " minimal tools")
        require(0 < config.get("max_iterations", 0) <= 24, key + " iteration bound")
        require(0 < config.get("max_completion_tokens", 0) <= 16384, key + " output bound")
        require(config.get("memory_enabled") is False, key + " no unscoped memory")
        require(config.get("skills_selection_mode") == "none" and config.get("selected_skills") == [], key + " no native sandbox skill route")
    fixture = json.loads((base / "tests" / "behavior-cases.json").read_text(encoding="utf-8"))
    cases = fixture["cases"]
    require(len({c["id"] for c in cases}) == len(cases), "unique behavior cases")
    for subject in {"agent:" + a for a in AGENTS} | {"skill:" + s for s in SCENES}:
        selected = [case for case in cases if case["subject"] == subject]
        require(len(selected) >= 3, subject + " three independent cases")
        require({case["kind"] for case in selected} >= {"normal", "missing", "violation"}, subject + " behavior coverage")
        require(all(case["expect"]["must"] and case["expect"]["must_not"] for case in selected), subject + " explicit evaluation")
    print(json.dumps({"checks": checks, "passed": checks - len(failures), "failed": failures, "behavior_cases": len(cases), "behavior_cases_executed": False}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        return run(args.repository.resolve())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"failed": [type(error).__name__ + ": " + str(error)]}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
