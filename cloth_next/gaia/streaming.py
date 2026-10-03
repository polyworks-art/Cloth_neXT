# SPDX-License-Identifier: GPL-3.0-or-later
"""Frame-addressable, validated two-sample Water Flow transport windows.

No Blender, FLIP reconstruction, frontend import or solver mutation here.
"""
from dataclasses import dataclass
import math
import zipfile
import time
from pathlib import Path
import numpy as np
from .container import GaiaContainer
from .water_field import fingerprint, solver_grid

MAX_STREAM_VALUES_BYTES = 128 * 1024 * 1024


class WaterStreamCacheError(ValueError):
    pass


@dataclass(frozen=True)
class WaterWindow:
    boundary: int
    source_frames: tuple
    times: tuple
    minimum: tuple
    maximum: tuple
    values: np.ndarray


def source_pair(boundary, first, last, fps):
    """Solver boundary zero is Cloth's first Blender frame, not frame one."""
    if (not isinstance(boundary,int) or boundary < 0 or boundary > last-first
            or last < first or not math.isfinite(fps) or fps <= 0):
        raise ValueError('Invalid Water Flow boundary or frame/time mapping')
    current=first+boundary
    frames=(current,) if current==last else (current,current+1)
    return frames,tuple((frame-first)/fps for frame in frames)


class WaterStreamReader:
    """Own one ZIP index; decode frames only on demand, retain no field cache."""
    def __init__(self,path,*,first,last,fps,expected_fingerprint):
        self.path=Path(path)
        if type(first) is not int or type(last) is not int:
            raise WaterStreamCacheError('Water Flow source frame identity must be integral')
        self.first=first;self.last=last;self.fps=float(fps)
        source_pair(0,self.first,self.last,self.fps)
        try:
            self.container=GaiaContainer(self.path)
        except (OSError,ValueError,KeyError,zipfile.BadZipFile) as exc:
            raise WaterStreamCacheError(f'Cannot open prepared Water Flow cache: {exc}') from exc
        try:
            water=self.container.manifest['water']
            stored=water['fingerprint']
            if stored!=expected_fingerprint or fingerprint({k:v for k,v in water.items() if k!='fingerprint'})!=stored:
                raise WaterStreamCacheError('Water Flow cache fingerprint changed or is invalid; prepare it again')
            if water.get('field_format')!='gaia-0.23' or water.get('physical_velocity_units')!='m/s':
                raise WaterStreamCacheError('Unsupported Water Flow field kind or velocity units')
            if not math.isclose(float(water['fps']),self.fps,rel_tol=0.,abs_tol=1e-7):
                raise WaterStreamCacheError('Water Flow cache FPS differs from Cloth FPS')
            self.dimensions=tuple(water['dimensions'])
            if (len(self.dimensions)!=3 or any(type(v) is not int for v in self.dimensions)
                    or min(self.dimensions)<2):
                raise WaterStreamCacheError('Invalid Water Flow grid dimensions')
            self.records=self.container.manifest['frames'];self.index={}
            self.minimum=tuple(self.records[0]['min']);self.maximum=tuple(self.records[0]['max'])
            if (len(self.minimum)!=3 or len(self.maximum)!=3
                    or not np.isfinite((*self.minimum,*self.maximum)).all()
                    or any(b<=a for a,b in zip(self.minimum,self.maximum))):
                raise WaterStreamCacheError('Invalid Water Flow field bounds')
            layout=water.get('grid_layout',{})
            if ('bounds' in layout and layout['bounds']!=[list(self.minimum),list(self.maximum)]):
                raise WaterStreamCacheError('Water Flow frame bounds differ from the cache fingerprint recipe')
            cache_first=int(self.records[0]['frame'])
            for index,record in enumerate(self.records):
                frame=record['frame']
                if type(frame) is not int or frame in self.index or frame!=cache_first+index:
                    raise WaterStreamCacheError('Water Flow cache has missing or duplicate source frames')
                if not math.isclose(float(record['time']),(frame-cache_first)/self.fps,rel_tol=0.,abs_tol=1e-7):
                    raise WaterStreamCacheError(f'Water Flow frame {frame} has an invalid source time')
                if tuple(record['min'])!=self.minimum or tuple(record['max'])!=self.maximum:
                    raise WaterStreamCacheError(f'Water Flow frame {frame} changes field bounds')
                if set(record['arrays'])!={'velocity','influence'}:
                    raise WaterStreamCacheError(f'Water Flow frame {frame} has incompatible field arrays')
                self.index[frame]=index
            if self.first not in self.index or self.last not in self.index:
                raise WaterStreamCacheError('Prepared Water Flow cache does not cover the Cloth frame range')
            self.fingerprint=stored
            self.frame_bytes=math.prod(self.dimensions)*12
            if self.frame_bytes*min(2,self.last-self.first+1)>MAX_STREAM_VALUES_BYTES:
                raise WaterStreamCacheError('Water Flow two-frame window exceeds the 128 MiB streaming values budget')
        except (KeyError,TypeError,ValueError,IndexError) as exc:
            self.close()
            if isinstance(exc,WaterStreamCacheError):raise
            raise WaterStreamCacheError(f'Invalid prepared Water Flow cache metadata: {exc}') from exc
        except BaseException:
            self.close();raise

    def window(self,boundary,*,influence=1.,velocity_scale=1.):
        frames,times=source_pair(boundary,self.first,self.last,self.fps)
        if not np.isfinite((influence,velocity_scale)).all() or not 0<=influence<=1 or not 0<=velocity_scale<=10:
            raise ValueError('Invalid streamed Water Flow strength')
        # Solver layout is [time, Blender Y, Blender Z, Blender X, component].
        w,h,d=self.dimensions
        output=np.empty((len(frames),h,d,w,3),dtype='<f4')
        bounds=None
        timings={'read_seconds':0.,'decode_validate_seconds':0.,'encode_seconds':0.}
        for ordinal,frame in enumerate(frames):
            try:
                field=self.container.frame(self.index[frame])
                for name,value in self.container.last_frame_timings.items():timings[name]+=value
                started=time.perf_counter()
                if field.velocity.shape!=(d,h,w,3):
                    raise WaterStreamCacheError(f'Water Flow frame {frame} has incompatible dimensions/node ordering')
                lo,hi,encoded=solver_grid(field)
                if bounds is None:bounds=(lo,hi)
                elif bounds!=(lo,hi):raise WaterStreamCacheError(f'Water Flow frame {frame} has incompatible bounds')
                output[ordinal]=encoded*influence*velocity_scale
                timings['encode_seconds']+=time.perf_counter()-started
                del field,encoded
            except (KeyError,OSError,ValueError,zipfile.BadZipFile) as exc:
                raise WaterStreamCacheError(f'Cannot read Water Flow source frame {frame}: {exc}') from exc
        self.last_window_timings=timings
        return WaterWindow(boundary,frames,times,*bounds,output)

    def close(self):self.container.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
