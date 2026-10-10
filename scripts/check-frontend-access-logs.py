import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone


def verify_logs(logs, marker, expected):
    if marker in logs or not all(re.search(r'"' + method + ' ' + path + r' HTTP/[0-9.]+" ' + str(status) + ' ', logs) for method, path, status in expected):
        raise ValueError()


def check_upstream_failure(image):
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError()
    marker = "trial-upstream-probe-" + uuid.uuid4().hex
    created = subprocess.run(["docker", "run", "--rm", "--detach", "--network", "none", "--memory", "128m", "--cpus", "0.5", "--pids-limit", "64", "--label", "io.boyankb.accesslog-probe=true", "--env", "APP_HOST=127.0.0.1", "--env", "APP_PORT=9", "--env", "APP_SCHEME=http", image], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45, check=True)
    container = created.stdout.decode().strip()
    if not re.fullmatch(r"[a-f0-9]{64}", container):
        raise ValueError()
    try:
        ready = False
        for _ in range(20):
            result = subprocess.run(["docker", "exec", container, "nginx", "-t"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
            if result.returncode == 0:
                ready = True
                break
            time.sleep(0.5)
        if not ready:
            raise ValueError()
        checks = (("HEAD", "/r/" + marker, re.escape("/r/[redacted]")), ("GET", "/api/v1/auth/config", re.escape("/api/v1/auth/config")))
        for method, path, _ in checks:
            request = f"{method} {path}?token={marker} HTTP/1.1\r\nHost: localhost\r\nReferer: https://example.invalid/?token={marker}\r\nUser-Agent: {marker}\r\nConnection: close\r\n\r\n"
            result = subprocess.run(["docker", "exec", "-i", container, "nc", "-w", "5", "127.0.0.1", "80"], input=request.encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, check=True)
            if not result.stdout.startswith(b"HTTP/1.1 502 "):
                raise ValueError()
        logs = subprocess.run(["docker", "logs", container], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True)
        verify_logs((logs.stdout + logs.stderr).decode(errors="replace"), marker, tuple((method, pattern, 502) for method, _, pattern in checks))
    finally:
        subprocess.run(["docker", "stop", "--time", "5", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--container", default="WeKnora-frontend")
    parser.add_argument("--check-upstream-failure", action="store_true")
    args = parser.parse_args()
    url = urllib.parse.urlparse(args.base_url)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.path not in ("", "/") or url.username or url.password or url.query or url.fragment or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]+", args.container):
        raise ValueError()
    details = subprocess.run(["docker", "inspect", args.container], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=True)
    item = json.loads(details.stdout)[0]
    labels = item.get("Config", {}).get("Labels", {})
    if labels.get("com.docker.compose.project") != "weknora-local" or labels.get("com.docker.compose.service") != "frontend":
        raise ValueError()
    since = datetime.now(timezone.utc).isoformat()
    marker = "trial-log-probe-" + uuid.uuid4().hex
    requests = (("GET", "/register", 200), ("GET", "/api/v1/auth/config", 200), ("GET", "/branding/logo.svg", 200), ("HEAD", "/r/" + marker, 404))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for method, path, status in requests:
        request = urllib.request.Request(args.base_url.rstrip("/") + path + "?token=" + marker, method=method, headers={"Referer": "https://example.invalid/register?token=" + marker, "User-Agent": marker})
        try:
            with opener.open(request, timeout=15) as response:
                if response.status != status:
                    raise ValueError()
                response.read(32)
        except urllib.error.HTTPError as error:
            if error.code != status:
                raise ValueError()
            error.close()
    time.sleep(1)
    result = subprocess.run(["docker", "logs", "--since", since, item["Id"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True)
    logs = (result.stdout + result.stderr).decode(errors="replace")
    expected = (("GET", r"/(?:register|index\.html)", 200), ("GET", re.escape(requests[1][1]), 200), ("GET", re.escape(requests[2][1]), 200), ("HEAD", re.escape("/r/[redacted]"), 404))
    verify_logs(logs, marker, expected)
    if args.check_upstream_failure:
        check_upstream_failure(item["Image"])
    print(json.dumps({"status": "passed", "routes": len(requests), "query_logged": False, "referrer_logged": False, "user_agent_logged": False, "resource_token_logged": False, "upstream_failure_checked": args.check_upstream_failure}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"status": "failed", "code": "ACCESS_LOG_ROUTE_CHECK_FAILED"}))
        sys.exit(1)
