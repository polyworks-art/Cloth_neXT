"""Validate complete selected FFP3 payloads without changing the FLIP cache."""
import json
from pathlib import Path
import sys
import hashlib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloth_next.gaia.particle_stream import vector_layout

root = Path('F:/16_Cloth_neXt/SW04/SW04_NEON_flip_fluid_cache/bakefiles')
rows = []
for frame in range(130, 161):
    row = dict(frame=frame)
    for kind, prefix in [('positions','fluidparticles'), ('velocities','fluidparticlesvelocity')]:
        path = root / f'{prefix}{frame:06}.ffp3'
        counts, offset = vector_layout(path)
        x = np.memmap(path, dtype='<f4', mode='r', offset=offset).reshape(-1,3)
        row[kind] = dict(count=len(x), finite=bool(np.isfinite(x).all()), minimum=x.min(0).tolist(), maximum=x.max(0).tolist(),
            sha256=hashlib.file_digest(path.open('rb'), 'sha256').hexdigest())
        del x
    assert row['positions']['count'] == row['velocities']['count']
    rows.append(row)
    print(frame, flush=True)
Path('docs/validation/gaia026/hydrodynamics/particle-payloads-130-160.json').write_text(json.dumps(rows, indent=2))
