"""Read-only inspection of the user's FLIP source scene."""
import bpy
import json
from pathlib import Path
import sys

out = Path(sys.argv[sys.argv.index('--')+1])
scene = bpy.context.scene
report = {'blend': bpy.data.filepath, 'fps': scene.render.fps / scene.render.fps_base,
          'units': scene.unit_settings.scale_length, 'frames': [scene.frame_start, scene.frame_end],
          'objects': []}
for obj in scene.objects:
    report['objects'].append({'name': obj.name, 'type': obj.type,
        'matrix': [list(row) for row in obj.matrix_world],
        'vertices': len(obj.data.vertices) if obj.type == 'MESH' else 0,
        'attributes': [a.name for a in obj.data.attributes] if obj.type == 'MESH' else [],
        'flip_type': getattr(getattr(obj, 'flip_fluid', None), 'object_type', ''),
        'cloth': str(getattr(getattr(obj, 'cloth_next', None), 'role', ''))})
if hasattr(scene, 'flip_fluid'):
    domain = scene.flip_fluid.get_domain_properties()
    if domain:
        report['flip'] = {'cache': domain.cache.get_cache_abspath(),
            'particle_velocity': domain.particles.enable_fluid_particle_velocity_vector_attribute,
            'surface_velocity': domain.surface.enable_velocity_vector_attribute,
            'particle_export': domain.particles.enable_fluid_particle_output}
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, indent=2), encoding='utf-8')
