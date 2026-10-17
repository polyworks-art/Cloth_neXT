# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental typed brick storage, never emitted into official GAIA PARAM.

Velocity and SDF have independent coordinate transforms and identical clocks.
The SDF occupies the first float of the shared four-float brick storage with
identity axes; its meaning is declared by the experimental outer manifest.
"""
import json
from pathlib import Path
import numpy as np

ROLES=('velocity_m_s_support','signed_distance_m_x_identity')


def dense_bricks(values):
    values=np.asarray(values,dtype='<f4')
    if values.ndim!=4 or values.shape[-1]!=4 or not np.isfinite(values).all():
        raise ValueError('Expected finite D,H,W,4 experimental volume')
    d,h,w=values.shape[:3]
    dims=np.array([w,h,d]);bd=(dims+7)//8
    table=np.arange(int(np.prod(bd)),dtype='<u4')
    blocks=[]
    for z in range(bd[2]):
        for y in range(bd[1]):
            for x in range(bd[0]):
                part=values[z*8:min((z+1)*8,d),y*8:min((y+1)*8,h),x*8:min((x+1)*8,w)]
                block=np.zeros((8,8,8,4),dtype='<f4')
                block[:part.shape[0],:part.shape[1],:part.shape[2]]=part
                blocks.append(block.reshape(512,4))
    return table,np.asarray(blocks),dims.tolist()


class ExperimentalHydroWriter:
    def __init__(self,path,*,start,step,provenance):
        self.path=Path(path)
        self.path.mkdir(parents=True,exist_ok=False)
        if not np.isfinite([start,step]).all() or step<=0:
            raise ValueError('Invalid hydrodynamic clock')
        self.start=float(start);self.step=float(step);self.provenance=provenance
        self.frames=[[],[]]

    def append(self,role,table,bricks,resolution,corner,axes,cell):
        if role not in (0,1):raise ValueError('Invalid hydrodynamic role')
        i=len(self.frames[role]);sub=self.path/f'field-{role}-fluid'
        sub.mkdir(exist_ok=True)
        table=np.asarray(table,dtype='<u4');bricks=np.asarray(bricks,dtype='<f4')
        if not np.isfinite(bricks).all():raise ValueError('Nonfinite hydrodynamic frame')
        if role==1 and not np.array_equal(axes,np.eye(3)):
            raise ValueError('SDF axes must be identity')
        a=f'field-{role}-fluid/table-{i:05}.bin';b=f'field-{role}-fluid/bricks-{i:05}.bin'
        table.tofile(self.path/a);bricks.tofile(self.path/b)
        transform=np.r_[corner,np.asarray(axes).T.ravel(),cell].astype('<f8')
        self.frames[role].append(dict(table=a,bricks=b,resolution=list(map(int,resolution)),transform=transform))

    def finish(self):
        if len(self.frames[0])!=len(self.frames[1]) or len(self.frames[0])<2:
            raise ValueError('Velocity/SDF frames must match and include two samples')
        lines=[]
        for role,frames in enumerate(self.frames):
            origin=f'field-{role}-origin.bin';track=f'field-{role}-track.bin'
            np.zeros(3,dtype='<f8').tofile(self.path/origin)
            row=np.zeros(28,dtype='<f4');row[[0,4,8,16,18,19,20]]=1
            row.tofile(self.path/track)  # zero strength: never an acceleration field
            sub=f'field-{role}-fluid/transform.bin'
            np.array([f['transform'] for f in frames],dtype='<f8').tofile(self.path/sub)
            lines.extend(['[[field]]','type = "fluid"','shape = "point"','falloff = "sphere"',
                'z_direction = "both"','start = 0.0',f'step = {self.step!r}',
                f'origin = "{origin}"',f'track = "{track}"','targets = 32',
                '[field.fluid]',f'resolution = {json.dumps([f["resolution"] for f in frames])}',
                'use_density = true',f'start = {self.start!r}',f'step = {self.step!r}',
                f'transform = "{sub}"',f'tables = {json.dumps([f["table"] for f in frames])}',
                f'bricks = {json.dumps([f["bricks"] for f in frames])}',''])
        (self.path/'force_field.toml').write_text('\n'.join(lines),encoding='utf-8')
        # Published last. Interrupted preparation cannot masquerade as complete.
        (self.path/'hydrodynamics.json').write_text(json.dumps(dict(version=1,
            roles=ROLES,units='metre_second',start=self.start,step=self.step,
            frames=len(self.frames[0]),provenance=self.provenance),indent=2))
