import hashlib
import json
import os
from pathlib import Path

import requests

lock = json.loads(Path('/config/embedding.lock.json').read_text())
root = Path('/models/bge-small-zh-v1.5')
root.mkdir(parents=True, exist_ok=True)
origins = [
    f'https://huggingface.co/{lock["model"]}/resolve/{lock["revision"]}',
    f'https://modelscope.cn/models/{lock["model"]}/resolve/{lock["mirrorRevision"]}',
]
source = os.environ.get('BOYANKB_MODEL_DOWNLOAD_SOURCE', 'huggingface')
if source == 'modelscope':
    origins.reverse()

for filename, expected in lock['files'].items():
    target = root / filename
    if target.exists() and hashlib.file_digest(target.open('rb'), 'sha256').hexdigest() == expected:
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.download')
    completed = False
    for origin in origins:
        try:
            with requests.get(f'{origin}/{filename}', stream=True, timeout=(10, 60)) as response:
                response.raise_for_status()
                digest = hashlib.sha256()
                total = 0
                with temporary.open('wb') as output:
                    for chunk in response.iter_content(1048576):
                        total += len(chunk)
                        if total > 120000000:
                            raise ValueError('MODEL_SIZE_INVALID')
                        output.write(chunk)
                        digest.update(chunk)
                if digest.hexdigest() != expected:
                    raise ValueError('MODEL_HASH_INVALID')
                temporary.replace(target)
                completed = True
                break
        except (requests.RequestException, ValueError, OSError):
            temporary.unlink(missing_ok=True)
    if not completed:
        raise SystemExit('MODEL_DOWNLOAD_FAILED')

print('MODEL_READY')
