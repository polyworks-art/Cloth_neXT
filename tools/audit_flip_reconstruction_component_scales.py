"""Document surface component scales relative to the retained SDF voxel size."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--dependencies',required=True);a=p.parse_args();sys.path[:0]=[a.dependencies,str(Path(__file__).resolve().parents[1])]
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from tools.audit_neon_hydrodynamic_loads import read_surface
rows=[];cell=.007826363667845726
for frame in (130,131,132,133,135,157,160):
 x,f,_=read_surface(Path(f'.hydro-test-tmp/flip-grid-edge-reference-{frame}/reconstructed.bobj'));u=f.ravel();v=f[:,[1,2,0]].ravel();graph=coo_matrix((np.ones(len(u),dtype=np.uint8),(u,v)),shape=(len(x),len(x))).tocsr();count,label=connected_components(graph,directed=False);lo=np.full((count,3),np.inf);hi=np.full((count,3),-np.inf)
 for axis in range(3):np.minimum.at(lo[:,axis],label,x[:,axis]);np.maximum.at(hi[:,axis],label,x[:,axis])
 ext=hi-lo;volume=np.bincount(label[f[:,0]],weights=np.einsum('ij,ij->i',x[f[:,0]].astype(float),np.cross(x[f[:,1]].astype(float),x[f[:,2]].astype(float)))/6,minlength=count);small=(ext<cell).all(1);thin=(ext<cell).any(1)
 row=dict(frame=frame,full_surface_components=int(count),sdf_cell_m=cell,reconstruction_lattice_m=.008014196395874024,components_with_all_bbox_axes_below_one_sdf_cell=int(small.sum()),their_absolute_volume_m3=float(abs(volume[small]).sum()),components_with_any_bbox_axis_below_one_sdf_cell=int(thin.sum()),component_extents_minimum_m=ext.min(0).tolist(),warning='Small-component boxes are at-risk indicators, not measured lost volume; alignment matters, and thin branches can occur within larger components. SDF transport covers the validated cloth envelope only. Full source and reconstructed meshes retain every component; the native guard forbids force evaluation outside transport bounds.')
 rows.append(row);Path('docs/validation/gaia026/hydrodynamics/flip-original/resolution-component-scales.json').write_text(json.dumps(rows,indent=2));print(row,flush=True)
