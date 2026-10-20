# SPDX-License-Identifier: GPL-3.0-or-later
"""Stream real FLIP velocity + BOBJ-derived voxel SDF to the native solver.

No forces are baked. Sources are read-only. Each worker holds one source
frame, one surface and two dense working grids, regardless of sequence size.
Runs in the solver installation's Python (NumPy, SciPy); no ray-tracing
libraries are needed.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# Loaded by the external runtime Python, including Blender extension namespaces.
# Add only the installed extension parent; never import a development checkout.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if not __package__:
    # File entry point in the external runtime; retain relative imports when
    # this module is imported under Blender's extension namespace instead.
    __package__ = Path(__file__).resolve().parents[1].name + '.' + Path(__file__).resolve().parent.name

# Two float32x4 brick copies plus float64 distance transforms per cell.
MAX_SDF_CELLS=24_000_000
# The native kernel reads only the sign of the trilinear SDF. A cell whose
# eight corners share a sign keeps it under clamping, and corners straddling
# the interface lie within one cell diagonal of it, so clamping to 1.5 cell
# diagonals is exact for the solver and lets far bricks share storage.
SDF_BAND_CELLS=1.5


def _prepare_frame(job):
    """One source frame: sparse velocity bricks and compact SDF bricks."""
    if job['dependencies']:sys.path.insert(0,job['dependencies'])
    import numpy as np
    from scipy.ndimage import distance_transform_edt
    from .container import GaiaContainer
    from .native_fluid import pack_frame, AXES
    from .hydrodynamic_transport import dense_bricks, compact_bricks
    from .surface import read_surface
    from .voxelize import inside_voxels, surface_defects
    t0=time.monotonic();frame=job['frame']
    with GaiaContainer(job['cache']) as c:field=c.frame(job['index'])
    path=Path(job['surface']);x,f,sha=read_surface(path)
    faces=len(f);open_edges,nonmanifold_edges=surface_defects(f)
    bounds=json.loads(Path(job['bounds_file']).read_text())
    matrix=np.asarray(job['cache_matrix']);translation=matrix[:3,3]
    if not np.allclose(matrix[:3,:3],np.eye(3),atol=1e-7):
        raise ValueError('Surface preparation requires an identity baked cache basis')
    if not np.allclose(translation,[bounds['x'],bounds['y'],bounds['z']],atol=1e-6):
        raise ValueError('BOBJ world transform requires explicit nonidentity domain mapping')
    # Here current_domain_min == baked_bounds_min and world scale=1.
    table,bricks=pack_frame(field,cell_centers=True)
    d,h,w=field.influence.shape;lo=np.asarray(field.minimum);hi=np.asarray(field.maximum)
    cell=(hi-lo)/[w,h,d]
    velocity=(table,bricks,[w,h,d],[lo[0],lo[2],-lo[1]],AXES,cell)
    surface_cell=cell/job['surface_scale']
    if job['owner'] and not np.allclose(surface_cell, .00782636, rtol=0, atol=1e-8):
        raise ValueError('Fluid grid differs from the validated NEON transport resolution')
    surface_lo=lo.copy();surface_hi=hi.copy()
    if job['requested'] is not None:
        requested=np.asarray(job['requested'])
        surface_lo=np.floor(requested[:3]/surface_cell)*surface_cell
        surface_hi=np.ceil(requested[3:]/surface_cell)*surface_cell
    sw,sh,sd=np.rint((surface_hi-surface_lo)/surface_cell).astype(int)
    if int(sw)*int(sh)*int(sd)>MAX_SDF_CELLS:
        raise ValueError(f'Hydrodynamic SDF grid {sw}x{sh}x{sd} exceeds {MAX_SDF_CELLS} cells; '
                         'reduce the cloth extent or the FLIP resolution')
    inside=inside_voxels(x,f,surface_lo,surface_cell,(sw,sh,sd))
    del x,f
    # Native solver axes are x,z,-y. SDF storage axes are identity.
    mask=inside.transpose(1,0,2)[::-1]
    spacing=(surface_cell[1],surface_cell[2],surface_cell[0])
    band=SDF_BAND_CELLS*float(np.linalg.norm(surface_cell))
    if mask.any():
        sdf=distance_transform_edt(~mask,sampling=spacing)-distance_transform_edt(mask,sampling=spacing)
        np.clip(sdf,-band,band,out=sdf)
    else:sdf=np.full(mask.shape,band)
    values=np.zeros(mask.shape+(4,),dtype='<f4');values[...,0]=sdf;values[...,3]=1
    del sdf
    st,sb,res=dense_bricks(values)
    st,sb=compact_bricks(st,sb)
    sdf_volume=(st,sb,res,[surface_lo[0],surface_lo[2],-surface_hi[1]],np.eye(3),surface_cell[[0,2,1]])
    record=dict(frame=frame,surface=str(path),sha256=sha,surface_faces=faces,
        open_edges=open_edges,nonmanifold_edges=nonmanifold_edges,
        inside_cells=int(mask.sum()),cell_m=cell.tolist(),sdf_cell_m=surface_cell.tolist(),
        sdf_minimum_blender_m=surface_lo.tolist(),sdf_maximum_blender_m=surface_hi.tolist(),
        sdf_interface_scale_m=float(np.linalg.norm(surface_cell)),sdf_band_m=band,
        seconds=time.monotonic()-t0,velocity_bricks=int(len(bricks)),sdf_bricks=int(len(sb)),
        sdf_table_entries=int(len(st)))
    return velocity,sdf_volume,record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dependencies',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--frames',type=int,default=31)
    p.add_argument('--first',type=int,default=1,
                   help='First source/Blender frame; output solver time starts at zero')
    p.add_argument('--fps',type=float,default=30.)
    p.add_argument('--bounds',type=float,nargs=6,required=True,
                   help='Explicit Blender world-space SDF minimum xyz and maximum xyz')
    p.add_argument('--surface-scale',type=int,default=2,
                   help='SDF resolution relative to velocity grid; 2 approximately matches FLIP dx')
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--experimental-surface-directory',type=Path,
                   help='Explicit isolated reconstruction for validation only; original bounds/velocity still come from cache')
    p.add_argument('--owner-dataset', type=Path)
    p.add_argument('--workers',type=int,default=0,
                   help='Parallel frame workers; 0 picks from the CPU count')
    a=p.parse_args()
    if a.dependencies:sys.path.insert(0,str(a.dependencies))
    import numpy as np
    from concurrent.futures import ProcessPoolExecutor
    from .container import GaiaContainer
    from .native_fluid import validate_native_water
    from .hydrodynamic_transport import ExperimentalHydroWriter
    owner=a.owner_dataset is not None
    if a.surface_scale<1:raise ValueError('Surface scale must be a positive integer')
    if owner:
        from .owner_test import validate_dataset
        if a.surface_scale != 4:raise ValueError('Owner transport requires the validated 7.82636 mm fine grid')
        if a.experimental_surface_directory:
            raise ValueError('Owner reconstruction source is fixed by the validated dataset')
        a.experimental_surface_directory, bounds_source = validate_dataset(
            a.owner_dataset, a.cache, a.first, a.frames)
    if a.frames<2 or not np.isfinite(a.fps) or a.fps<=0:
        raise ValueError('Need at least two samples and a positive finite frame rate')
    requested=None
    if a.bounds:
        requested=np.asarray(a.bounds)
        if not np.isfinite(requested).all() or (requested[3:]<=requested[:3]).any():
            raise ValueError('Invalid explicit SDF bounds')
        requested=requested.tolist()
    started=time.monotonic();records=[]
    with GaiaContainer(a.cache) as c:
        water=c.manifest['water'];validate_native_water(water)
        if not owner:
            # The container records the FLIP bakefiles it was prepared from.
            bounds_source=Path(water['provider']['cache'])
            if not bounds_source.is_dir():
                raise ValueError(f'FLIP cache folder is missing: {bounds_source}')
        indices={}
        for index,record in enumerate(c.manifest['frames']):
            number=record['frame']
            if number in indices:raise ValueError(f'Duplicate source frame {number}')
            indices[number]=index
        selected=list(range(a.first,a.first+a.frames))
        missing=[frame for frame in selected if frame not in indices]
        if missing:raise ValueError(f'Missing GAIA source frames: {missing}')
        times=np.asarray([c.manifest['frames'][indices[frame]]['time'] for frame in selected])
        if not np.allclose(times-times[0],np.arange(a.frames)/a.fps,rtol=0,atol=1e-7):
            raise ValueError('GAIA source clock does not match requested frame rate')
    source=bounds_source
    surface_source=(a.experimental_surface_directory.resolve()
                    if a.experimental_surface_directory else source)
    for frame in selected:
        for path in (surface_source/f'{frame:06}.bobj',source/f'bounds{frame:06}.bbox'):
            if not path.is_file():raise ValueError(f'Missing FLIP surface source: {path}')
    writer=ExperimentalHydroWriter(a.output,start=0,step=1/a.fps,
        provenance=dict(source_cache=str(a.cache.resolve()),source_fingerprint=water['fingerprint'],
            first_source_frame=a.first,last_source_frame=selected[-1],source_fps=a.fps,
            method='BOBJ_three_axis_parity_majority_signed_EDT_band_v2',
            experimental_surface_directory=str(surface_source) if a.experimental_surface_directory else None,
            reconstruction_transport_approved=False if a.experimental_surface_directory else None,
            limits='voxel SDF clamped to a sign-exact band; subcell disconnected water can be lost; no pressure data'))
    jobs=[dict(cache=str(a.cache),index=indices[frame],frame=frame,
               surface=str(surface_source/f'{frame:06}.bobj'),
               bounds_file=str(source/f'bounds{frame:06}.bbox'),
               cache_matrix=water['provider']['cache_matrix'],surface_scale=a.surface_scale,
               requested=requested,owner=owner,
               dependencies=str(a.dependencies) if a.dependencies else '')
          for frame in selected]
    workers=a.workers or max(1,min(6,(os.cpu_count() or 2)//4))
    workers=min(workers,len(jobs))

    def publish(velocity,sdf_volume,record):
        writer.append(0,*velocity);writer.append(1,*sdf_volume)
        records.append(record);print(record,flush=True)
        (a.output/'preparation-progress.json').write_text(json.dumps(records,indent=2))

    if workers==1:
        for job in jobs:publish(*_prepare_frame(job))
    else:
        # Frames are independent; results are appended strictly in order with
        # a bounded window of frames in flight.
        with ProcessPoolExecutor(max_workers=workers) as pool:
            pending=[]
            for job in jobs:
                pending.append(pool.submit(_prepare_frame,job))
                if len(pending)>=2*workers:publish(*pending.pop(0).result())
            for future in pending:publish(*future.result())
    writer.provenance['surfaces']=records;writer.finish()
    total=sum(p.stat().st_size for p in a.output.rglob('*') if p.is_file())
    (a.output/'preparation-report.json').write_text(json.dumps(dict(frames=a.frames,bytes=total,
        workers=workers,seconds=time.monotonic()-started,records=records),indent=2))


if __name__=='__main__':main()
