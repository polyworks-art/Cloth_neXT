"""Merge completed parallel NEON cases without copying trajectory artifacts."""
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    merged=None
    labels=set()
    for root in a.inputs:
        source=json.loads((root/'report.json').read_text())
        if merged is None:
            merged={**source,'cases':[]}
        for key in ('source_scene_sha256','solver_sha256','frames','quality','saved_zones'):
            if source[key]!=merged[key]:raise ValueError(f'Incompatible {key}: {root}')
        if source.get('backend','cpu')!=merged.get('backend','cpu'):
            raise ValueError(f'Incompatible backend: {root}')
        for case in source['cases']:
            label=case['label']
            if label in labels:raise ValueError(f'Duplicate case: {label}')
            folder=Path(case.get('artifact_root',root/label)).resolve()
            if not (folder/'native-positions.npy').is_file():raise ValueError(f'Missing positions: {folder}')
            if not (Path(case['session'])/'output/finished.txt').is_file():
                raise ValueError(f'Incomplete native solve: {label}')
            merged['cases'].append({**case,'artifact_root':str(folder)})
            labels.add(label)
    a.output.mkdir(parents=True,exist_ok=True)
    (a.output/'report.json').write_text(json.dumps(merged,indent=2))
    print('Merged completed cases:',', '.join(sorted(labels)))


if __name__=='__main__':main()
