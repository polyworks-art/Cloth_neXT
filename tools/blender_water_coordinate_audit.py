"""Read-only numerical FLIP/world/grid/GAIA/overlay audit, with in-memory transform fixtures."""
from pathlib import Path
import io, json, sys, zipfile, zlib
import bpy
import numpy as np
from mathutils import Matrix, Vector
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.blender.water_flow import FLIPWaterFlowProvider
from cloth_next.gaia.water_field import reconstruct, grid_dimensions, solver_grid, debug_vectors, debug_samples
from cloth_next.gaia.container import write_water_container, GaiaContainer
from cloth_next.gaia.solver_fields import official_grid_payload
from cloth_next.ppf.coordinates import blender_vector_to_ppf
scene=bpy.context.scene
domain=scene.objects['FLIP Domain']
original_frame,original_subframe=scene.frame_current,scene.frame_subframe
original_matrix=domain.matrix_world.copy()
report={'blend':bpy.data.filepath,'domain':domain.name,'cases':[]}
output=ROOT/'dist/water-coordinate-audit.gaia'
def bounds(a):
    a=np.asarray(a);assert np.isfinite(a).all()
    return {'min':a.min(0).tolist(),'max':a.max(0).tolist()}
def stats(a):
    result=bounds(a);speed=np.linalg.norm(a,axis=1)
    result.update(magnitude_min=float(speed.min()),magnitude_max=float(speed.max()),magnitude_mean=float(speed.mean()))
    return result
try:
    for name,transform in [('authored',original_matrix),('translated',Matrix.Translation((5,-3,2))@original_matrix),('rotated',Matrix.Rotation(.7,4,'Z')@original_matrix),('scaled',Matrix.Scale(1.8,4)@original_matrix)]:
        domain.matrix_world=original_matrix
        bpy.context.view_layer.update()
        reset=FLIPWaterFlowProvider(bpy.context,domain,range(150,151),hash_sources=False)
        reset.sample(150)
        bpy.context.view_layer.update()
        domain.matrix_world=transform
        bpy.context.view_layer.update()
        provider=FLIPWaterFlowProvider(bpy.context,domain,range(150,151),hash_sources=False)
        positions,velocity=provider.sample(150)
        stale_positions=positions.copy()
        stale_matrix=np.asarray(provider.cache.get_cache_object().matrix_world).copy()
        bpy.context.view_layer.update()
        source=provider.cache.get_cache_object()
        local=np.asarray([v.co[:] for v in source.data.vertices])
        updated=np.asarray(source.matrix_world)
        positions=local@updated[:3,:3].T+updated[:3,3]
        print("STALE",name,bounds(stale_positions),"UPDATED",bounds(positions),"matrix_delta",float(np.max(np.abs(updated-stale_matrix))),flush=True)
        obj=provider.cache.get_cache_object()
        raw=np.asarray([v.co[:] for v in obj.data.vertices])
        attr=np.asarray([v.vector[:] for v in obj.data.attributes['flip_velocity'].data])
        dims=grid_dimensions(provider.minimum,provider.maximum,24)
        print("BEFORE",name,"bounds",provider.minimum,provider.maximum,"samples",bounds(positions),"matrix",np.asarray(obj.matrix_world).tolist(),flush=True)
        field=reconstruct(positions,velocity,provider.minimum,provider.maximum,dims)
        lo,hi,encoded=solver_grid(field)
        starts,ends=debug_vectors(field,1,.1)
        z,y,x=np.nonzero(field.influence>0.5)
        table=[]
        for i in np.linspace(0,len(x)-1,min(10,len(x)),dtype=int):
            xx,yy,zz=int(x[i]),int(y[i]),int(z[i])
            world=np.asarray(field.minimum)+(np.asarray(field.maximum)-field.minimum)*[xx,yy,zz]/(np.asarray(dims)-1)
            contribution=field.contribution[zz,yy,xx]
            solver_value=encoded[dims[1]-1-yy,zz,xx]
            expected=np.asarray(blender_vector_to_ppf(contribution))
            q=(positions-np.asarray(field.minimum))/(np.asarray(field.maximum)-field.minimum)*(np.asarray(dims)-1)
            weight=np.maximum(0.,1.-np.abs(q-[xx,yy,zz])).prod(1)
            raw_weighted=(velocity*weight[:,None]).sum(0)/weight.sum()
            np.testing.assert_allclose(raw_weighted,field.velocity[zz,yy,xx],atol=1e-6)
            table.append({'source_weighted_velocity':raw_weighted.tolist(),'source_reconstruction_difference':float(np.max(np.abs(raw_weighted-field.velocity[zz,yy,xx]))),'xyz_index' :[xx,yy,zz],'world_position':world.tolist(),'influence':float(field.influence[zz,yy,xx]),'water_velocity':field.velocity[zz,yy,xx].tolist(),'water_contribution':contribution.tolist(),'gaia_velocity':solver_value.tolist(),'difference':float(np.max(np.abs(solver_value-expected)))})
        assert max(t['difference'] for t in table)==0
        item={'before_world_position':bounds(stale_positions),'stale_matrix':stale_matrix.tolist(),'name':name,'domain_matrix':np.asarray(domain.matrix_world).tolist(),'domain_location':list(domain.location),'domain_rotation':list(domain.rotation_euler),'domain_scale':list(domain.scale),'cache_matrix':np.asarray(obj.matrix_world).tolist(),'domain_bounds':{'min':provider.minimum.tolist(),'max':provider.maximum.tolist()},'sample_count':len(positions),'cache_local_position':bounds(raw),'world_position':bounds(positions),'raw_file_positions':bounds(np.asarray(provider.cache.import_ffp3(str(provider.root/provider.position_pattern.format(150)),attribute_type='ATTRIBUTE_TYPE_VECTOR')[0]).reshape(-1,3)),'attribute_velocity':stats(attr),'world_velocity':stats(velocity),'dimensions_xyz':dims,'grid_shape_zyx':field.velocity.shape[:3],'grid_origin':field.minimum,'grid_bounds':{'min':field.minimum,'max':field.maximum},'node_spacing':((np.asarray(field.maximum)-field.minimum)/(np.asarray(dims)-1)).tolist(),'encoded_bounds':{'min':lo,'max':hi},'overlay_origins':bounds(starts),'overlay_endpoints':bounds(ends),'overlay_max_length':float(np.linalg.norm(ends-starts,axis=1).max()),'samples_outside_domain':int(np.count_nonzero(~np.all((positions>=provider.minimum)&(positions<=provider.maximum),axis=1))),'cells':table}
        # Report matrices both before and after an explicit depsgraph update.
        before=np.asarray(obj.matrix_world).copy()
        bpy.context.view_layer.update()
        item['cache_matrix_update_delta']=float(np.max(np.abs(np.asarray(obj.matrix_world)-before)))
        report['cases'].append(item)
        print('CASE',name,'sample bounds',item['world_position'],'velocity',item['world_velocity'],'line max',item['overlay_max_length'],flush=True)
        if name=='authored':
            # Four world probes; sampler follows the official inclusive nodes contract.
            def sample_grid(values,point,minimum,maximum):
                lo,hi=np.asarray(minimum),np.asarray(maximum)
                if np.any(point<lo) or np.any(point>hi):return np.zeros(values.shape[3:] or ())
                dims_xyz=np.asarray(values.shape[:3][::-1])
                q=(point-lo)/(hi-lo)*(dims_xyz-1)
                base=np.minimum(np.floor(q).astype(int),dims_xyz-2);frac=q-base
                result=np.zeros(values.shape[3:] or ())
                for zz in (0,1):
                    for yy in (0,1):
                        for xx in (0,1):
                            corner=np.asarray([xx,yy,zz]);w=np.where(corner,frac,1-frac).prod()
                            idx=base+corner;result+=w*values[idx[2],idx[1],idx[0]]
                return result
            peak=np.unravel_index(np.argmax(field.influence*np.linalg.norm(field.velocity,axis=3)),field.influence.shape)
            inside=np.asarray(field.minimum)+(np.asarray(field.maximum)-field.minimum)*np.asarray(peak[::-1])/(np.asarray(dims)-1)
            points=[('inside',inside),('outside',np.asarray(field.maximum)+[1,1,1]),('surface',positions[np.argmax(positions[:,2])]),('boundary',np.asarray([field.maximum[0],0.,.5]))]
            probes=[]
            for label,point in points:
                occupancy=float(sample_grid(field.influence,point,field.minimum,field.maximum))
                world_v=sample_grid(field.contribution,point,field.minimum,field.maximum)
                solver_p=np.asarray(blender_vector_to_ppf(point))
                solver_v=sample_grid(encoded,solver_p,lo,hi)
                difference=float(np.max(np.abs(solver_v-np.asarray(blender_vector_to_ppf(world_v)))))
                assert difference<2e-6
                probes.append({'kind':label,'position':point.tolist(),'occupancy':occupancy,'water_contribution':world_v.tolist(),'encoded_solver_velocity':solver_v.tolist(),'difference':difference})
            item['probes']=probes
            origins,_=debug_samples(field,1)
            a,b=debug_vectors(field,1,.2,mode='CONSTANT_X')
            np.testing.assert_allclose(b-a,np.broadcast_to([.2,0,0],a.shape),atol=1e-6)
            assert np.all(origins>=field.minimum) and np.all(origins<=field.maximum)
            item['positions_only_count']=len(origins)
            item['constant_x_max_difference']=float(np.max(np.abs((b-a)-[.2,0,0])))
            write_water_container(output,{'audit_frame':150},[(150,0,field)])
            with GaiaContainer(output) as container:
                grid=official_grid_payload(container,[0])['grids'][0]
                decoded=np.frombuffer(zlib.decompress(grid['data']),dtype='<f4').reshape(grid['shape'])
                np.testing.assert_array_equal(decoded[0],encoded)
            with zipfile.ZipFile(output,'a',compression=zipfile.ZIP_DEFLATED) as archive:
                buf=io.BytesIO();np.savez_compressed(buf,positions=positions,velocities=velocity,raw_positions=raw,raw_velocities=attr)
                archive.writestr('audit/source-frame150.npz',buf.getvalue())
                cloth=scene.objects.get('Plane')
                if cloth is not None:
                    mesh=cloth.data;mesh.calc_loop_triangles()
                    points=np.asarray([tuple(cloth.matrix_world@v.co) for v in mesh.vertices])
                    buf=io.BytesIO();np.savez_compressed(buf,positions=points,triangles=np.asarray([t.vertices[:] for t in mesh.loop_triangles]))
                    archive.writestr('fixture/Plane.npz',buf.getvalue())
finally:
    domain.matrix_world=original_matrix
    scene.frame_set(original_frame,subframe=original_subframe)
    bpy.context.view_layer.update()
with zipfile.ZipFile(output,'a',compression=zipfile.ZIP_DEFLATED) as archive:archive.writestr('audit/report.json',json.dumps(report,indent=2))
(ROOT/'dist/water-coordinate-audit.json').write_text(json.dumps(report,indent=2))
print('NUMERICAL AUDIT COMPLETE',flush=True)
