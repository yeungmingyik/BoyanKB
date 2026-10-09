import argparse
import ipaddress
import json
import math
import re
import subprocess
import sys
from urllib.parse import urlsplit


class CheckError(Exception):
    pass


def docker(context, arguments, stdin=None, timeout=30):
    try:
        result = subprocess.run(
            ["docker", "--context", context, *arguments],
            input=stdin,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as error:
        raise CheckError("DOCKER_COMMAND_UNAVAILABLE_OR_TIMEOUT") from error
    if result.returncode != 0:
        raise CheckError("DOCKER_COMMAND_FAILED")
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError) as error:
        raise CheckError("DOCKER_RESPONSE_INVALID") from error


def inspect_container(context, identifier):
    result = docker(context, ["container", "inspect", identifier])
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise CheckError("CONTAINER_INSPECT_INVALID")
    return result[0]


def no_runtime_mounts(info):
    for mount in info.get("Mounts", []):
        source = str(mount.get("Source", "")).replace("\\", "/").lower()
        target = str(mount.get("Destination", "")).replace("\\", "/").lower()
        if source in {"/", "/run", "/var/run", "/var/lib/docker"} or target in {"/host", "/run", "/var/run", "/var/lib/docker"}:
            return False
        if any(value.endswith(".sock") or "docker_engine" in value for value in [source, target]):
            return False
        if any(part in source or part in target for part in ["sandbox-docker-tls", "/.docker", "/docker/tls", "/containerd"]):
            return False
    return True


def environment(info):
    return dict(entry.split("=", 1) for entry in info.get("Config", {}).get("Env", []) if "=" in entry)


def app_checks(info):
    host = info.get("HostConfig", {})
    env = environment(info)
    return {
        "running": info.get("State", {}).get("Running") is True,
        "not_privileged": host.get("Privileged") is False,
        "separate_namespaces": host.get("NetworkMode") != "host" and host.get("PidMode", "") in {"", "private"} and host.get("IpcMode", "") in {"", "private"} and host.get("UTSMode", "") != "host",
        "no_runtime_mounts": no_runtime_mounts(info),
        "docker_fallback_disabled": env.get("WEKNORA_SANDBOX_DOCKER_ENABLED", "false").lower() in {"", "false", "0"},
        "no_remote_docker_environment": not any(env.get(key) for key in ["DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY"]),
    }


def sandbox_checks(info, args):
    host = info.get("HostConfig", {})
    config = info.get("Config", {})
    labels = config.get("Labels") or {}
    allowed_caps = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "SETGID", "SETUID", "KILL"}
    actual_caps = {str(cap).removeprefix("CAP_").upper() for cap in host.get("CapAdd") or []}
    security = host.get("SecurityOpt") or []
    env = environment(info)
    return {
        "running": info.get("State", {}).get("Running") is True,
        "managed_sandbox": labels.get("com.weknora.sandbox.managed") == "true",
        "expected_workspace": labels.get("weknora_tenant_id") == str(args.tenant_id),
        "expected_config": labels.get("weknora_sandbox_config_id") == args.config_id,
        "offline_network": host.get("NetworkMode") == "none" and set((info.get("NetworkSettings") or {}).get("Networks") or {}) <= {"none"},
        "isolated_runtime": host.get("Runtime") == "runsc",
        "not_privileged": host.get("Privileged") is False,
        "separate_namespaces": host.get("PidMode", "") in {"", "private"} and host.get("IpcMode", "") in {"", "private"} and host.get("UTSMode", "") != "host" and host.get("CgroupnsMode", "") != "host",
        "no_mounts": not info.get("Mounts") and not host.get("Binds") and not host.get("VolumesFrom"),
        "no_devices": not host.get("Devices") and not host.get("DeviceRequests") and not host.get("DeviceCgroupRules"),
        "no_host_gateway": not host.get("ExtraHosts"),
        "no_published_ports": not host.get("PortBindings") and not host.get("PublishAllPorts"),
        "cpu_limit": 0 < int(host.get("NanoCpus") or 0) <= args.max_cpus * 1_000_000_000,
        "memory_limit": 0 < int(host.get("Memory") or 0) <= args.max_memory_mb * 1024 * 1024,
        "swap_disabled": int(host.get("Memory") or 0) > 0 and host.get("MemorySwap") == host.get("Memory"),
        "pids_limit": 0 < int(host.get("PidsLimit") or 0) <= args.max_pids,
        "capabilities_restricted": "ALL" in (host.get("CapDrop") or []) and actual_caps <= allowed_caps,
        "no_new_privileges": any(value in {"no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"} for value in security) and not any(value in {"no-new-privileges:false", "no-new-privileges=false"} for value in security),
        "no_unconfined_profile": not any("unconfined" in value for value in security),
        "init_enabled": host.get("Init") is True,
        "no_proxy_environment": not any(value for key, value in env.items() if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}),
        "expected_image": info.get("Image") == args.image_id,
    }


PROBE = r'''
import errno
import json
import pathlib
import socket

result = {}
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    with socket.create_connection(listener.getsockname(), timeout=1) as peer:
        connection, _ = listener.accept()
        connection.close()
        result["loopback_positive_control"] = True

interfaces = {name for _, name in socket.if_nameindex()}
result["loopback_only"] = interfaces == {"lo"}
routes = pathlib.Path("/proc/net/route").read_text().splitlines()[1:]
result["no_ipv4_routes"] = not any(line.split() and line.split()[0] != "lo" for line in routes)
v6_path = pathlib.Path("/proc/net/ipv6_route")
v6_routes = v6_path.read_text().splitlines() if v6_path.exists() else []
result["no_ipv6_external_routes"] = not any(line.split() and line.split()[-1] != "lo" for line in v6_routes)

targets = {
    "metadata_ipv4": (socket.AF_INET, "169.254.169.254", 80),
    "private_10": (socket.AF_INET, "10.0.0.1", 443),
    "private_172": (socket.AF_INET, "172.16.0.1", 443),
    "private_192": (socket.AF_INET, "192.168.0.1", 443),
    "host_gateway": (socket.AF_INET, "172.17.0.1", 2376),
    "public_ipv4": (socket.AF_INET, "1.1.1.1", 443),
    "metadata_ipv6": (socket.AF_INET6, "fd00:ec2::254", 80),
    "private_ipv6": (socket.AF_INET6, "fd00::1", 443),
    "link_local_ipv6": (socket.AF_INET6, "fe80::1", 443),
    "mapped_metadata_ipv6": (socket.AF_INET6, "::ffff:169.254.169.254", 80),
    "public_ipv6": (socket.AF_INET6, "2606:4700:4700::1111", 443),
}
blocked_errors = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EACCES, errno.EPERM, errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL, errno.EINVAL}
for name, (family, host, port) in targets.items():
    try:
        with socket.socket(family, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect((host, port))
        result[name] = False
    except OSError as error:
        result[name] = error.errno in blocked_errors

result["no_docker_socket"] = not any(pathlib.Path(path).exists() for path in ["/var/run/docker.sock", "/run/docker.sock", "/run/containerd/containerd.sock"])
print(json.dumps(result, sort_keys=True))
'''


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    app = subparsers.add_parser("app-baseline")
    app.add_argument("--container", required=True)
    offline = subparsers.add_parser("offline-sandbox")
    offline.add_argument("--container", required=True)
    offline.add_argument("--tenant-id", type=int, required=True)
    offline.add_argument("--config-id", required=True)
    offline.add_argument("--image-id", required=True)
    offline.add_argument("--max-cpus", type=float, default=1)
    offline.add_argument("--max-memory-mb", type=int, default=1024)
    offline.add_argument("--max-pids", type=int, default=128)
    offline.add_argument("--probe-authorized-test-container", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", args.context):
        parser.error("invalid context")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", args.container):
        parser.error("invalid container")
    if args.mode == "offline-sandbox":
        if not re.fullmatch(r"[a-f0-9]{64}", args.container):
            parser.error("offline-sandbox requires the full container ID")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", args.image_id):
            parser.error("invalid image ID")
        if args.tenant_id < 1 or not math.isfinite(args.max_cpus) or min(args.max_cpus, args.max_memory_mb, args.max_pids) <= 0:
            parser.error("limits and tenant ID must be positive")
        if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", args.config_id):
            parser.error("invalid config ID")
        if args.probe_authorized_test_container and args.context in {"default", "desktop-linux"}:
            parser.error("probe requires an explicitly named dedicated remote context")
    return args


def main():
    args = parse_args()
    info = inspect_container(args.context, args.container)
    checks = app_checks(info) if args.mode == "app-baseline" else sandbox_checks(info, args)
    scope = "app_container_surface_only" if args.mode == "app-baseline" else "sandbox_create_envelope_only"
    if args.mode == "offline-sandbox" and args.probe_authorized_test_container:
        if not all(checks.values()):
            checks["probe_preflight"] = False
        else:
            context = docker(args.context, ["context", "inspect", args.context])
            docker_endpoint = context[0].get("Endpoints", {}).get("docker", {})
            endpoint = urlsplit(docker_endpoint.get("Host", ""))
            hostname = endpoint.hostname or ""
            if endpoint.scheme not in {"tcp", "ssh"} or not hostname or hostname.lower() == "localhost" or hostname.lower().endswith(".localhost"):
                raise CheckError("REMOTE_CONTEXT_REQUIRED")
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError:
                address = None
            if address is not None and (address.is_loopback or address.is_unspecified or address.is_link_local):
                raise CheckError("REMOTE_CONTEXT_REQUIRED")
            if endpoint.scheme == "tcp" and (docker_endpoint.get("SkipTLSVerify") is not False or not context[0].get("TLSMaterial", {}).get("docker")):
                raise CheckError("VERIFIED_TLS_CONTEXT_REQUIRED")
            remote = docker(args.context, ["exec", "-i", args.container, "/usr/bin/timeout", "-s", "KILL", "20", "python3", "-I", "-"], stdin=PROBE, timeout=25)
            expected = {"loopback_positive_control", "loopback_only", "no_ipv4_routes", "no_ipv6_external_routes", "metadata_ipv4", "private_10", "private_172", "private_192", "host_gateway", "public_ipv4", "metadata_ipv6", "private_ipv6", "link_local_ipv6", "mapped_metadata_ipv6", "public_ipv6", "no_docker_socket"}
            if not isinstance(remote, dict) or set(remote) != expected or not all(type(value) is bool for value in remote.values()):
                raise CheckError("PROBE_RESPONSE_INVALID")
            checks.update({"probe_" + key: value for key, value in remote.items()})
            scope = "sandbox_create_envelope_and_network_probe"
    passed = all(checks.values())
    print(json.dumps({"status": "PASS" if passed else "FAIL", "scope": scope, "checks": checks}, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CheckError, ValueError, TypeError, KeyError, IndexError, OverflowError, AttributeError, UnicodeError):
        print(json.dumps({"status": "UNVERIFIED", "code": "CHECK_UNAVAILABLE_OR_INVALID_RESPONSE"}))
        sys.exit(2)
