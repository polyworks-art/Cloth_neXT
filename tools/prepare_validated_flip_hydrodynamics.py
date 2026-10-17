"""Hash-bound reconstructed FLIP surfaces for isolated Blender validation only."""
import argparse,hashlib,json,os,runpy,sys
from pathlib import Path


def validate_sources(manifest_path, first, count):
    manifest=json.loads(manifest_path.read_text())
    if manifest.get('usage')!='ISOLATED_BLENDER_VALIDATION_ONLY' or manifest.get('release_approved') is not False:
        raise ValueError('Not an isolated validation manifest')
    source=Path(manifest['surface_directory']).resolve(strict=True)
    if count<2 or not 130<=first or first+count-1>160:
        raise ValueError('Outside the investigated source sequence')
    for frame in range(first,first+count):
        name=f'{frame:06}.bobj';expected=manifest['files'].get(name)
        if not expected:raise ValueError(f'Missing reconstructed source {frame}')
        with (source/name).open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=expected:raise ValueError(f'Reconstructed source changed: {frame}')
    return source


def main():
    root=Path(__file__).resolve().parents[1]
    if not os.environ.get('CLOTH_NEXT_EXPERIMENTAL_HYDRO_MANIFEST') or not os.environ.get('BLENDER_USER_RESOURCES'):
        raise ValueError('Requires isolated experimental Blender profile')
    p=argparse.ArgumentParser(add_help=False);p.add_argument('--first',type=int,required=True);p.add_argument('--frames',type=int,required=True)
    a,_=p.parse_known_args()
    if '--experimental-surface-directory' in sys.argv:raise ValueError('Surface override is fixed by validation manifest')
    source=validate_sources(root/'.hydro-test-tmp/flip-grid-edge-surfaces/validation-manifest.json',a.first,a.frames)
    sys.argv += ['--experimental-surface-directory',str(source)]
    runpy.run_path(str(root/'tools/prepare_neon_hydrodynamics.py'),run_name='__main__')


if __name__=='__main__':main()
