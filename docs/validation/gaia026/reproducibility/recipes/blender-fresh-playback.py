"""Fresh-process PC2 playback check of a completed regular Bake (run inside Blender).

blender --background --python this.py -- --bake <bake dir> --output <json>
Opens the saved NEON-test.blend in a NEW Blender process, evaluates every frame and
compares each cloth object's evaluated vertices with its PC2 samples.
"""
import argparse, json, sys, importlib, traceback
from pathlib import Path
import addon_utils, bpy
import numpy as np


def main():
    p = argparse.ArgumentParser(); p.add_argument('--bake', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--addon-module', default='bl_ext.gaia_owner.cloth_next')
    a = p.parse_args(sys.argv[sys.argv.index('--')+1:])
    result = dict(result='RUNNING', blender=bpy.app.version_string, fresh_process=True, objects=[])
    try:
        report = json.loads((a.bake/'report.json').read_text())
        addon_utils.enable(a.addon_module, default_set=True)
        repos = bpy.context.preferences.extensions.repos
        for module in ('superhivemarket_com', 'api_portal_polyhammer_com'):
            if not any(r.module == module for r in repos): repos.new(name=module, module=module)
        for module in ('bl_ext.superhivemarket_com.flip_fluids_addon', 'bl_ext.api_portal_polyhammer_com.character_dna',
                       'bl_ext.api_portal_polyhammer_com.character_assembly'):
            addon_utils.enable(module, default_set=True)
        bpy.ops.wm.open_mainfile(filepath=str(a.bake/'NEON-test.blend'))
        from bl_ext.api_portal_polyhammer_com.character_dna.runtime import controller
        controller.register(); controller.after_load()
        solver_test = importlib.import_module(a.addon_module+'.blender.solver_test')
        iter_frames = importlib.import_module(a.addon_module+'.bake.pc2').iter_frames
        first, last = report['range']
        for record in report['playback']:
            obj = bpy.context.scene.objects[record['object']]
            modifiers = [m for m in obj.modifiers if solver_test.is_cloth_next_playback_modifier(obj, m)
                         and m.filepath and m.show_viewport]
            assert len(modifiers) == 1, [m.name for m in obj.modifiers]
            path = Path(bpy.path.abspath(modifiers[0].filepath))
            assert path.resolve() == Path(record['pc2']).resolve(), (path, record['pc2'])
            samples = list(iter_frames(path)); assert len(samples) == last-first+1
            error = 0.
            for sample, expected in enumerate(samples):
                bpy.context.scene.frame_set(first+sample)
                evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()); mesh = evaluated.to_mesh()
                try:
                    actual = np.asarray([tuple(v.co) for v in mesh.vertices]); assert actual.shape == expected.shape
                    error = max(error, float(np.max(np.abs(actual-expected))))
                finally: evaluated.to_mesh_clear()
            assert error < 1e-5, error
            result['objects'].append(dict(object=obj.name, modifier=modifiers[0].type, frames=len(samples),
                                          max_error_m=error, material=obj.cloth_next.material.preset,
                                          hydrodynamic_pressure=bool(obj.cloth_next.water_hydrodynamic_pressure)))
        result['attachment_count'] = sum(len(i.points) for i in bpy.context.scene.cloth_next_object_attachments)
        assert result['attachment_count'] == 186 and len(result['objects']) == 3
        result['result'] = 'PASS'
    except BaseException:
        result['result'] = 'FAIL'; result['traceback'] = traceback.format_exc(); traceback.print_exc()
    a.output.write_text(json.dumps(result, indent=2)); print('FRESH_PLAYBACK', json.dumps(result), flush=True)
    bpy.ops.wm.quit_blender()


if __name__ == '__main__':
    main()
