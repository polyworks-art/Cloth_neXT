"""Stage native output from a completed regular bake for independent auditing."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloth_next.core.filesystem_paths import io_path

p = argparse.ArgumentParser()
p.add_argument('--run', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
a.run, a.output = a.run.resolve(), a.output.resolve()
report = json.loads((a.run/'report.json').read_text())
assert report['state'] == 'FINISHED' and report['result'] == 'PASS'
native = io_path(Path(report['native_output']), reserved_length=1000)
count = report['range'][1]-report['range'][0]+1
positions = np.stack([np.fromfile(native/f'vert_{i}.bin',dtype='<f4').reshape(-1,3) for i in range(count)])
assert np.isfinite(positions).all()
a.output.mkdir(parents=True, exist_ok=False)
np.save(a.output/'native-positions.npy', positions)
shutil.copy2(native.parent/'map.pickle', a.output/'map.cbor')
scene = next((a.run/'cache/.cloth_next_export/scene').rglob('scene.cbor'))
(a.output/'report.json').write_text(json.dumps(dict(source_scene=str(scene), cases=[dict(
    label=f"regular-blender-{report['range'][0]}-{report['range'][1]}",artifact_root=str(a.output),session=str(native.parent))]),indent=2))
