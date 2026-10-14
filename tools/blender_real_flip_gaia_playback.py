# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the isolated real-FLIP patch's PC2 in Blender's existing playback path."""
import argparse
import json
import os
from pathlib import Path
import sys
import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cloth_next.blender import registration,solver_test


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    report=json.loads((args.output/'native-report.json').read_text())
    cache=Path(report['pc2']).resolve()
    if cache.parent!=args.output.resolve():raise ValueError('PC2 must belong to this test output')
    with np.load(args.output/'real-flip-trajectories.npz') as data:
        expected=data['wet'].copy();triangles=data['triangles'].copy()
    registration.register()
    try:
        mesh=bpy.data.meshes.new('Real FLIP playback')
        mesh.from_pydata(expected[0].tolist(),[],triangles.tolist())
        obj=bpy.data.objects.new('Real FLIP playback',mesh)
        bpy.context.scene.collection.objects.link(obj)
        modifier=obj.modifiers.new('Cloth NeXt Cache','MESH_CACHE')
        first=report['source_frames'][0]
        solver_test._configure_playback_modifier(modifier,first)
        modifier.filepath=str(cache)
        for i in range(len(expected)):
            bpy.context.scene.frame_set(first+i);bpy.context.view_layer.update()
            evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            actual=np.asarray([tuple(v.co) for v in evaluated.data.vertices])
            np.testing.assert_allclose(actual,expected[i],atol=1e-6,rtol=1e-6)
            del evaluated
        modifier.filepath='';obj.modifiers.remove(modifier)
        solver_test._release_playback_readers()
        bpy.data.objects.remove(obj,do_unlink=True);bpy.data.meshes.remove(mesh)
        bpy.context.view_layer.update()
        moved=cache.with_suffix('.release-probe')
        if moved.exists():raise ValueError('Foreign release-probe file exists')
        os.replace(cache,moved);os.replace(moved,cache)
        result={'result':'PASS','frames':report['source_frames'],'checked_samples':len(expected),
                'pc2_released':True,'scope':'Isolated patch PC2 through existing Mesh Cache modifier'}
        (args.output/'playback-report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print('REAL FLIP PLAYBACK PASS '+json.dumps(result),flush=True)
    finally:registration.unregister()


if __name__=='__main__':main()
