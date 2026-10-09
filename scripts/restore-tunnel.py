import argparse
import json
import re
import socket
import socketserver
import subprocess
import threading
import time
from pathlib import Path


LIMIT = threading.BoundedSemaphore(16)
STATE_LOCK = threading.Lock()
CLOSING = threading.Event()
SOCKETS = set()
PROCESSES = set()


def docker(arguments):
    result = subprocess.run(["docker", *arguments], capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError("DOCKER_INSPECTION_FAILED")
    return result.stdout


def caddy_id(project):
    ids = docker(["ps", "-q", "--filter", "label=com.docker.compose.project=" + project, "--filter", "label=com.docker.compose.service=caddy"]).decode().split()
    if len(ids) != 1:
        raise RuntimeError("UNIQUE_RUNNING_RESTORE_CADDY_REQUIRED")
    info = json.loads(docker(["inspect", ids[0]]))[0]
    lock = json.loads((Path(__file__).resolve().parents[1] / "deployment/production/images.lock.json").read_text(encoding="utf-8"))
    image_id = json.loads(docker(["image", "inspect", lock["images"]["caddy"]]))[0]["Id"]
    networks = list(info["NetworkSettings"].get("Networks", {}).values())
    if info["Image"] != image_id or not info["State"]["Running"] or not networks:
        raise RuntimeError("RESTORE_CADDY_STATE_OR_IMAGE_MISMATCH")
    network_info = json.loads(docker(["network", "inspect", *[item["NetworkID"] for item in networks]]))
    if not all(item.get("Internal") and item.get("Labels", {}).get("com.docker.compose.project") == project for item in network_info):
        raise RuntimeError("RESTORE_CADDY_NETWORK_MISMATCH")
    return info["Id"]


class Tunnel(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = False


class Forward(socketserver.BaseRequestHandler):
    def handle(self):
        if not LIMIT.acquire(blocking=False):
            return
        process = None
        try:
            self.request.settimeout(180)
            with STATE_LOCK:
                if CLOSING.is_set():
                    return
                process = subprocess.Popen(["docker", "exec", "-i", self.server.container_id, "nc", "-w", "180", "127.0.0.1", str(self.server.target_port)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                SOCKETS.add(self.request)
                PROCESSES.add(process)

            def upload():
                try:
                    while payload := self.request.recv(65536):
                        pending = memoryview(payload)
                        while pending:
                            size = process.stdin.write(pending)
                            if not size:
                                raise OSError("PIPE_CLOSED")
                            pending = pending[size:]
                except (OSError, ValueError):
                    pass
                finally:
                    try:
                        process.stdin.close()
                    except (OSError, ValueError):
                        pass

            worker = threading.Thread(target=upload, daemon=True)
            worker.start()
            while payload := process.stdout.read(65536):
                self.request.sendall(payload)
        except OSError:
            pass
        finally:
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            if process:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdin.close()
                process.stdout.close()
            with STATE_LOCK:
                SOCKETS.discard(self.request)
                PROCESSES.discard(process)
            LIMIT.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--seconds", type=int, default=600)
    args = parser.parse_args()
    if not re.fullmatch(r"boyankb-restore-[a-z0-9-]+", args.project) or not 30 <= args.seconds <= 3600:
        raise RuntimeError("RESTORE_PROJECT_AND_BOUNDED_DURATION_REQUIRED")
    container = caddy_id(args.project)
    servers = []
    running = []
    try:
        for host_port, target_port in ((9089, 80), (9443, 443)):
            server = Tunnel(("127.0.0.1", host_port), Forward)
            server.container_id = container
            server.target_port = target_port
            servers.append(server)
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            running.append(server)
        print("RESTORE_TUNNEL_READY", args.project, "127.0.0.1:9089,9443", flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            time.sleep(max(0, min(1, deadline - time.monotonic())))
    except KeyboardInterrupt:
        pass
    finally:
        CLOSING.set()
        for server in running:
            server.shutdown()
        for server in servers:
            server.server_close()
        with STATE_LOCK:
            sockets = list(SOCKETS)
            processes = list(PROCESSES)
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
                connection.close()
            except OSError:
                pass
        for process in processes:
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 5
        for process in processes:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    print("RESTORE_TUNNEL_CLOSED", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("RESTORE_TUNNEL_FAILED")
        raise SystemExit(1)
