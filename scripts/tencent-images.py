import argparse
import gzip
import hashlib
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT / "temp/tencent-cloud") or output.exists() or output.suffixes[-2:] != [".tar", ".gz"]:
        raise RuntimeError("NEW_PRIVATE_IMAGE_ARCHIVE_REQUIRED")
    receipt = output.with_suffix(".json")
    if receipt.exists() or not output.parent.is_dir():
        raise RuntimeError("NEW_RECEIPT_AND_EXISTING_DIRECTORY_REQUIRED")
    lock = json.loads((ROOT / "deployment/tencent/images.lock.json").read_text(encoding="utf-8"))
    references = [entry["tag"] for entry in lock["images"]]
    images = json.loads(subprocess.check_output(["docker", "image", "inspect", *references]))
    for expected, actual in zip(lock["images"], images, strict=True):
        if actual["Id"] != expected["id"] or actual["Os"] != "linux" or actual["Architecture"] != "amd64":
            raise RuntimeError("SOURCE_IMAGE_LOCK_OR_PLATFORM_MISMATCH")
    print(json.dumps({"export_started": True, "images": len(images), "uncompressed_image_size_sum": sum(item["Size"] for item in images)}), flush=True)
    process = subprocess.Popen(["docker", "image", "save", *references], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    written = 0
    next_progress = 512 * 1024 * 1024
    with output.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1, mtime=0) as archive:
            for block in iter(lambda: process.stdout.read(1024 * 1024), b""):
                archive.write(block)
                written += len(block)
                if written >= next_progress:
                    print(json.dumps({"export_read_mib": written // 1024**2}), flush=True)
                    next_progress += 512 * 1024 * 1024
    error = process.stderr.read()
    if process.wait() != 0:
        raise RuntimeError("IMAGE_EXPORT_FAILED")
    os.chmod(output, 0o600)
    data = {"format": "boyankb-tencent-images-v1", "platform": "linux/amd64", "images": lock["images"], "archive": output.name, "size": output.stat().st_size, "sha256": digest(output), "tar_size": written}
    with receipt.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    print(json.dumps({"export_complete": True, "size": data["size"], "sha256": data["sha256"], "receipt": str(receipt)}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(str(error), flush=True)
        raise SystemExit(1)
