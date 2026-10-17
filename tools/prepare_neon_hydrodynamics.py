"""Stream real FLIP velocity + watertight BOBJ-derived voxel SDF to experiment.

No forces are baked. Sources are read-only. Memory is bounded to one source
frame, one surface/BVH and two dense working grids, regardless of sequence size.
"""
import argparse
import json
import sys
import time
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.gaia.container import GaiaContainer
from cloth_next.gaia.native_fluid import pack_frame, AXES, validate_native_water
from cloth_next.gaia.hydrodynamic_transport import ExperimentalHydroWriter,dense_bricks
from tools.audit_neon_hydrodynamic_loads import read_surface


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dependencies',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--frames',type=int,default=31)
    p.add_argument('--first',type=int,default=1,
                   help='First source/Blender frame; output solver time starts at zero')
    p.add_argument('--fps',type=float,default=30.)
    p.add_argument('--bounds',type=float,nargs=6,
                   help='Explicit Blender world-space SDF minimum xyz and maximum xyz')
    p.add_argument('--surface-scale',type=int,default=2,
                   help='SDF resolution relative to velocity grid; 2 approximately matches FLIP dx')
    p.add_argument('--cloth-crop',action='store_true',help='Cover captured cloth bounds plus 0.2 m, independently of velocity bounds')
    p.add_argument('--cache',type=Path,default=Path('F:/16_Cloth_neXt/cache_AD/S04_NEON_Garments_002_WaterFlow.gaia'))
    p.add_argument('--experimental-surface-directory',type=Path,
                   help='Explicit isolated reconstruction for validation only; original bounds/velocity still come from cache')
    a=p.parse_args();sys.path.insert(0,str(a.dependencies))
    if not 1<=a.surface_scale<=4:raise ValueError('Surface scale must be 1..4')
    if a.frames<2 or not np.isfinite(a.fps) or a.fps<=0:
        raise ValueError('Need at least two samples and a positive finite frame rate')
    if a.cloth_crop and a.first!=1:
        raise ValueError('Captured frame-1 cloth crop cannot certify animated mid-sequence bounds')
    import trimesh
    from scipy.ndimage import distance_transform_edt
    if not trimesh.ray.has_embree:raise RuntimeError('Embree required for bounded preparation time')
    started=time.monotonic();records=[]
    with GaiaContainer(a.cache) as c:
        water=c.manifest['water'];validate_native_water(water)
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
        source=Path(water['provider']['cache'])
        surface_source=(a.experimental_surface_directory.resolve()
                        if a.experimental_surface_directory else source)
        for frame in selected:
            for path in (surface_source/f'{frame:06}.bobj',source/f'bounds{frame:06}.bbox'):
                if not path.is_file():raise ValueError(f'Missing FLIP surface source: {path}')
        writer=ExperimentalHydroWriter(a.output,start=0,step=1/a.fps,
            provenance=dict(source_cache=str(a.cache.resolve()),source_fingerprint=water['fingerprint'],
                first_source_frame=a.first,last_source_frame=selected[-1],source_fps=a.fps,
                method='closed_BOBJ_inside_centres_signed_EDT_v1',
                experimental_surface_directory=str(surface_source) if a.experimental_surface_directory else None,
                reconstruction_transport_approved=False if a.experimental_surface_directory else None,
                limits='voxel SDF; subcell disconnected water can be lost; no pressure data'))
        for k in range(a.frames):
            frame=selected[k];t0=time.monotonic();field=c.frame(indices[frame])
            path=surface_source/f'{frame:06}.bobj'
            x,f,sha=read_surface(path)
            mesh=trimesh.Trimesh(vertices=x,faces=f,process=False)
            if not(mesh.is_watertight and mesh.is_winding_consistent and mesh.volume>0):
                raise ValueError(f'{path}: surface is not a consistently oriented closed fluid volume')
            bounds=json.loads((source/f'bounds{frame:06}.bbox').read_text())
            provider=water['provider'];translation=np.asarray(provider['cache_matrix'])[:3,3]
            if not np.allclose(np.asarray(provider['cache_matrix'])[:3,:3],np.eye(3),atol=1e-7):
                raise ValueError('Surface preparation requires an identity baked cache basis')
            if not np.allclose(translation,[bounds['x'],bounds['y'],bounds['z']],atol=1e-6):
                raise ValueError('BOBJ world transform requires explicit nonidentity domain mapping')
            # Here current_domain_min == baked_bounds_min and world scale=1.
            table,bricks=pack_frame(field,cell_centers=True)
            d,h,w=field.influence.shape;lo=np.asarray(field.minimum);hi=np.asarray(field.maximum)
            cell=(hi-lo)/[w,h,d]
            writer.append(0,table,bricks,[w,h,d],[lo[0],lo[2],-lo[1]],AXES,cell)
            surface_cell=cell/a.surface_scale
            surface_lo=lo.copy();surface_hi=hi.copy()
            if a.bounds:
                requested=np.asarray(a.bounds)
                if not np.isfinite(requested).all() or (requested[3:]<=requested[:3]).any():
                    raise ValueError('Invalid explicit SDF bounds')
                surface_lo=np.floor(requested[:3]/surface_cell)*surface_cell
                surface_hi=np.ceil(requested[3:]/surface_cell)*surface_cell
            if a.cloth_crop:
                from tools.report_neon_coupling_investigation import PLAN,SOURCE
                garment=[]
                for target in PLAN['targets']:
                    local=np.fromfile(SOURCE/target['initial_artifact'],dtype='<f4').reshape(target['initial_shape'])
                    matrix=np.asarray(target['world_matrix'])
                    garment.append(local@matrix[:3,:3].T+matrix[:3,3])
                points=np.concatenate(garment)
                surface_lo=np.floor((points.min(0)-.2)/surface_cell)*surface_cell
                surface_hi=np.ceil((points.max(0)+.2)/surface_cell)*surface_cell
            sw,sh,sd=np.rint((surface_hi-surface_lo)/surface_cell).astype(int)
            axes=[surface_lo[j]+(np.arange(n)+.5)*surface_cell[j] for j,n in enumerate((sw,sh,sd))]
            inside=np.zeros((sd,sh,sw),bool)
            for z in range(sd):
                xx,yy=np.meshgrid(axes[0],axes[1]);points=np.c_[xx.ravel(),yy.ravel(),np.full(sw*sh,axes[2][z])]
                inside[z]=mesh.contains(points).reshape(sh,sw)
            # Native solver axes are x,z,-y. SDF storage axes are identity.
            mask=inside.transpose(1,0,2)[::-1]
            spacing=(surface_cell[1],surface_cell[2],surface_cell[0])
            if mask.any():
                sdf=distance_transform_edt(~mask,sampling=spacing)-distance_transform_edt(mask,sampling=spacing)
            else:sdf=np.full(mask.shape,np.linalg.norm(hi-lo))
            values=np.zeros(mask.shape+(4,),dtype='<f4');values[...,0]=sdf;values[...,3]=1
            st,sb,res=dense_bricks(values)
            writer.append(1,st,sb,res,[surface_lo[0],surface_lo[2],-surface_hi[1]],np.eye(3),surface_cell[[0,2,1]])
            records.append(dict(frame=frame,surface=str(path),sha256=sha,surface_faces=len(f),
                inside_cells=int(mask.sum()),cell_m=cell.tolist(),sdf_cell_m=surface_cell.tolist(),
                sdf_minimum_blender_m=surface_lo.tolist(),sdf_maximum_blender_m=surface_hi.tolist(),
                sdf_interface_scale_m=float(np.linalg.norm(surface_cell)),
                seconds=time.monotonic()-t0,velocity_bricks=int(len(bricks)),sdf_bricks=int(len(sb))))
            print(records[-1],flush=True)
            (a.output/'preparation-progress.json').write_text(json.dumps(records,indent=2))
        writer.provenance['surfaces']=records;writer.finish()
    total=sum(p.stat().st_size for p in a.output.rglob('*') if p.is_file())
    (a.output/'preparation-report.json').write_text(json.dumps(dict(frames=a.frames,bytes=total,
        seconds=time.monotonic()-started,records=records),indent=2))


if __name__=='__main__':main()
