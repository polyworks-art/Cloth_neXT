import gc,json,weakref,zipfile,zlib
import numpy as np
import pytest
from cloth_next.gaia.container import write_water_container,GaiaContainer
from cloth_next.gaia.water_field import reconstruct,fingerprint
from cloth_next.gaia.solver_fields import official_grid_payload
from cloth_next.gaia.streaming import WaterStreamReader,WaterStreamCacheError,source_pair


def cache(tmp_path,first=50,count=6):
    metadata={'fps':24.,'dimensions':[3,4,5],'field_format':'gaia-0.23','physical_velocity_units':'m/s'}
    metadata['fingerprint']=fingerprint(metadata)
    path=tmp_path/'stream.gaia'
    def sequence():
        for i in range(count):
            field=reconstruct([[.5,.5,.5]],[[i+1.,.2,-.3]],[0,0,0],[1,1,1],[3,4,5])
            yield first+i,i/24.,field
            del field
    write_water_container(path,metadata,sequence())
    return path,metadata['fingerprint']


def test_authoritative_offsets_and_final_one_sample():
    assert source_pair(0,120,200,24.)==((120,121),(0.,1/24.))
    assert source_pair(20,120,200,24.)==((140,141),(20/24.,21/24.))
    assert source_pair(80,120,200,24.)==((200,),(80/24.,))
    assert source_pair(0,150,150,24.)==((150,),(0.,))
    with pytest.raises(ValueError):source_pair(81,120,200,24.)


def test_window_bit_identical_to_schedule_and_rebased_times(tmp_path):
    path,digest=cache(tmp_path)
    with GaiaContainer(path) as c:
        old=official_grid_payload(c,[0],influence=.5,velocity_scale=2.)['grids'][0]
    values=np.frombuffer(zlib.decompress(old['data']),dtype='<f4').reshape(old['shape'])
    with WaterStreamReader(path,first=52,last=54,fps=24.,expected_fingerprint=digest) as reader:
        window=reader.window(0,influence=.5,velocity_scale=2.)
        assert window.source_frames==(52,53) and window.times==(0.,1/24.)
        np.testing.assert_array_equal(window.values,values[2:4])
        np.testing.assert_array_equal(reader.window(2).values,values[4:5])


def test_missing_identity_fps_and_fingerprint_fail(tmp_path):
    path,digest=cache(tmp_path)
    for options in ({'first':49},{'last':56},{'fps':25.},{'expected_fingerprint':'wrong'}):
        args=dict(first=50,last=55,fps=24.,expected_fingerprint=digest);args.update(options)
        with pytest.raises(WaterStreamCacheError):WaterStreamReader(path,**args)


def test_corrupt_frame_fails_at_requested_identity(tmp_path):
    path,digest=cache(tmp_path)
    with zipfile.ZipFile(path) as z:contents={n:z.read(n) for n in z.namelist()}
    name='water/000003/velocity.npy'
    contents[name]=contents[name][:-1]+bytes([contents[name][-1]^1])
    with zipfile.ZipFile(path,'w') as z:
        for n,b in contents.items():z.writestr(n,b)
    with WaterStreamReader(path,first=50,last=55,fps=24.,expected_fingerprint=digest) as reader:
        reader.window(0)
        with pytest.raises(WaterStreamCacheError,match='source frame 53.*checksum'):reader.window(2)


@pytest.mark.parametrize('count',[10,250])
def test_frame_addressable_iteration_retains_no_field_arrays(tmp_path,monkeypatch,count):
    path,digest=cache(tmp_path,count=count)
    reads=[];original=GaiaContainer.frame
    def tracked(self,index):reads.append(index);return original(self,index)
    monkeypatch.setattr(GaiaContainer,'frame',tracked)
    with WaterStreamReader(path,first=50,last=49+count,fps=24.,expected_fingerprint=digest) as reader:
        refs=[]
        for boundary in range(count):
            window=reader.window(boundary);refs.append(weakref.ref(window.values))
            assert window.values.nbytes<=2*3*4*5*3*4
            del window
        gc.collect()
        assert all(ref() is None for ref in refs)
        assert len(reads)==count*2-1
        reads.clear();reader.window(count-1)
        assert reads==[count-1]  # direct ZIP member, no preceding field decode
