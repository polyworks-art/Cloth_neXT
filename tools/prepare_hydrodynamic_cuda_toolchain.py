"""Provision a private CUDA 12.8 compiler from NVIDIA's hashed redistributables.

No installer, registry, driver, or system environment changes are made.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    base = 'https://developer.download.nvidia.com/compute/cuda/redist/'
    manifest = urllib.request.urlopen(base + 'redistrib_12.8.1.json').read()
    (root / 'redistrib_12.8.1.json').write_bytes(manifest)
    data = json.loads(manifest)
    for name in ('cuda_nvcc', 'cuda_cudart', 'cuda_cccl'):
        item = data[name]['windows-x86_64']
        archive = root / Path(item['relative_path']).name
        if not archive.exists():
            print('Downloading', name, item['size'], flush=True)
            urllib.request.urlretrieve(base + item['relative_path'], archive)
        with archive.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != item['sha256']:
            raise ValueError(f'SHA256 mismatch: {archive}')
        print('Verified', name, flush=True)
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                relative = Path(*Path(entry.filename).parts[1:])
                dest = (root / relative).resolve()
                if not dest.is_relative_to(root):
                    raise ValueError('Archive path escapes output')
                if entry.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                elif relative.parts:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(entry) as src, dest.open('wb') as target:
                        shutil.copyfileobj(src, target)
    print('Portable CUDA ready:', root, flush=True)


if __name__ == '__main__':
    main()
