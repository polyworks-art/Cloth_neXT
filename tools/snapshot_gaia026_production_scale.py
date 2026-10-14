"""Archive completed validation evidence without classifying running cases as PASS."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for path in sorted(args.root.rglob('report.json')):
        try:
            report = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
        if 'vertices' not in report or 'frames' not in report:
            continue
        relative = path.relative_to(args.root)
        destination = args.output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        record = dict(report=str(relative), result=report.get('result', 'UNKNOWN'),
                      vertices=report['vertices'], frames=report['frames'],
                      combination=report.get('combination'), dry=report.get('dry'),
                      wall_seconds=report.get('wall_seconds'),
                      native_file_metrics=report.get('native_file_metrics'),
                      quality=report.get('quality'),
                      artifacts=[])
        for artifact in [path.parent / 'fixture.blend',
                         *(Path(obj['pc2']) for obj in report.get('objects', []))]:
            if not artifact.is_file():
                record['artifacts'].append(dict(path=str(artifact), available=False))
                continue
            digest = hashlib.sha256()
            with artifact.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            record['artifacts'].append(dict(path=str(artifact), available=True,
                bytes=artifact.stat().st_size, sha256=digest.hexdigest()))
        resources = path.parent.parent / (path.parent.name + '-resources.json')
        if resources.is_file():
            target = args.output / resources.relative_to(args.root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resources, target)
        records.append(record)
    snapshot = dict(scope='Completed case reports only; running jobs omitted', cases=records)
    (args.output / 'artifact-index.json').write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
    print(json.dumps(dict(cases=len(records),
        passed=sum(r['result'] == 'PASS' for r in records),
        failed=sum(r['result'] == 'FAIL' for r in records))))


if __name__ == '__main__':
    main()
