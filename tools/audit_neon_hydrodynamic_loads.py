"""Read-only NEON surface/force audit; estimates on existing trajectories, no new solve.

Needs trimesh + rtree in an explicitly supplied Python dependency directory.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def read_surface(path):
    data=path.read_bytes()
    if len(data)<8:raise ValueError(f'{path}: truncated BOBJ header')
    n=struct.unpack_from('<i',data)[0]
    if n<3 or 4+12*n+4>len(data):
        raise ValueError(f'{path}: invalid or truncated BOBJ vertex array')
    x=np.frombuffer(data,'<f4',3*n,4).reshape(-1,3)
    m=struct.unpack_from('<i',data,4+12*n)[0]
    if m<1 or len(data)!=8+12*n+12*m:
        raise ValueError(f'{path}: invalid FLIP BOBJ triangle length')
    f=np.frombuffer(data,'<i4',3*m,8+12*n).reshape(-1,3)
    if not np.isfinite(x).all() or f.min()<0 or f.max()>=n:
        raise ValueError(f'{path}: nonfinite coordinates or invalid triangle indices')
    return x,f,hashlib.sha256(data).hexdigest()


def contains_bounded(mesh, points):
    # Ray broadphase can have thousands of candidate triangles per point.
    # Bound scratch memory independently of garment tessellation.
    return np.concatenate([mesh.contains(points[i:i+64])
                           for i in range(0,len(points),64)])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dependencies',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    sys.path.insert(0,str(args.dependencies))
    import trimesh
    from tools.report_neon_coupling_investigation import load_case, OUT, SAMPLE
    from tools.diagnose_neon_native_coupling import packed_volume
    from cloth_next.gaia.container import GaiaContainer
    case=load_case('zone30-bound','dry1')
    cache_path='F:/16_Cloth_neXt/cache_AD/S04_NEON_Garments_002_WaterFlow.gaia'
    rows=[];surfaces=[]
    with GaiaContainer(cache_path) as cache:
        source=Path(cache.manifest['water']['provider']['cache'])
        for frame in (1,15,30):
            path=source/f'{frame:06}.bobj'
            x,f,sha=read_surface(path)
            mesh=trimesh.Trimesh(vertices=x,faces=f,process=False)
            valid=bool(mesh.is_watertight and mesh.is_winding_consistent and mesh.volume>0)
            surfaces.append(dict(frame=frame,file=str(path),sha256=sha,vertices=len(x),
                faces=len(f),watertight=bool(mesh.is_watertight),
                winding_consistent=bool(mesh.is_winding_consistent),volume_m3=float(mesh.volume),
                bounds_m=mesh.bounds.tolist()))
            if not valid:
                raise ValueError('Surface cannot certify inside/outside; refuse wet area')
            field=cache.frame(frame-1)
            volume=packed_volume(field,(frame-1)/30)
            for name,poses in case['points'].items():
                tri=np.load(OUT/(name+'-mesh.npz'))['triangles']
                # Existing positions are solver-world (x,z,-y); BOBJ is Blender-world.
                pose=poses[frame-1][:,[0,2,1]]*[1,-1,1]
                v=(poses[min(frame,29)]-poses[max(frame-2,0)])*30/(min(frame,29)-max(frame-2,0))
                v=v[:,[0,2,1]]*[1,-1,1]
                triangles=pose[tri]
                cross=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
                area=np.linalg.norm(cross,axis=1)/2
                n=cross/np.maximum(2*area[:,None],1e-30)
                centroid=triangles.mean(1)
                # Three symmetric barycentric points, area weights 1/3 each.
                force=np.zeros((len(tri),3));wet=np.zeros(len(tri));proxy=np.zeros(len(tri))
                relative_speeds=[];missing_wet_area=0.
                for weights in ((2/3,1/6,1/6),(1/6,2/3,1/6),(1/6,1/6,2/3)):
                    points=np.einsum('i,tij->tj',weights,triangles)
                    inside=contains_bounded(mesh,points)
                    solver_points=points[:,[0,2,1]]*[1,1,-1]
                    u,occupancy,presence=SAMPLE(volume,solver_points,(frame-1)/30)
                    u=u[:,[0,2,1]]*[1,-1,1]
                    # A missing velocity sample cannot be replaced with a made-up flow.
                    supported=np.asarray(presence,dtype=bool).reshape(-1)
                    relative=u-np.einsum('i,tij->tj',weights,v[tri])
                    normal=np.einsum('ij,ij->i',relative,n)
                    active=inside & supported
                    missing_wet_area+=float(area@(inside & ~supported))/3
                    force+= (.5*1000*area/3*active*np.abs(normal)*normal)[:,None]*n
                    wet+=inside/3
                    proxy+=np.asarray(occupancy).reshape(-1)/3
                    relative_speeds.extend(np.linalg.norm(relative[active],axis=1).tolist())
                rows.append(dict(frame=frame,object=name,trajectory='existing official dry1',
                    total_area_m2=float(area.sum()),surface_wet_area_m2=float(area@wet),
                    wet_area_without_velocity_support_m2=missing_wet_area,
                    particle_support_weighted_area_m2=float(area@proxy),
                    load_bearing_triangles=int(np.count_nonzero(np.linalg.norm(force,axis=1))),
                    estimated_net_force_n=force.sum(0).tolist(),
                    estimated_sum_force_magnitudes_n=float(np.linalg.norm(force,axis=1).sum()),
                    relative_speed_max_m_s=max(relative_speeds,default=0.),
                    centroid_quadrature_wet_area_m2=float(area@contains_bounded(mesh,centroid))))
                print(frame,name,rows[-1]['surface_wet_area_m2'],rows[-1]['estimated_sum_force_magnitudes_n'],flush=True)
    result=dict(scope='offline force estimates, no hydrodynamic trajectory/contact reaction measured',
        density_kg_m3=1000,normal_coefficient=1,tangential_coefficient=0,
        assumptions='impermeable two-sided sheet closure; BOBJ in baked world metres; three-point wetting quadrature; no hydrostatics',
        surface_records=surfaces,loads=rows,
        pressure_named_files=[p.name for p in source.iterdir() if 'pressure' in p.name.lower()],
        levelset_named_files=[p.name for p in source.iterdir() if any(s in p.name.lower() for s in ('levelset','level_set','sdf'))])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2))


if __name__=='__main__':main()

