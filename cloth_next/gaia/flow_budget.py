# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservative, allocation-free budgets for derived coupling grids."""
import math
from .native_fluid import BRICK, MAX_PAYLOAD_BYTES, MAX_FILE_PAYLOAD_BYTES
from .streaming import MAX_STREAM_VALUES_BYTES


def packed_frame_bytes(dimensions):
    return math.prod((n+BRICK-1)//BRICK for n in dimensions)*(4+512*16)


def native_encoding_bytes(dimensions):
    dense=math.prod(dimensions)*16
    return 2*dense+3*min(MAX_PAYLOAD_BYTES,packed_frame_bytes(dimensions))+(1<<20)


def coupling_budget(*, frames=1, fields=1, native=True):
    frames=max(1,int(frames));fields=max(1,int(fields))
    # Base PARAM is capped at 32 MiB. Reserve twice that plus generous
    # per-frame metadata, rather than filling the 1-GiB transport envelope.
    per_frame=(MAX_FILE_PAYLOAD_BYTES-(64<<20))//(frames*fields)-4096
    return dict(native=bool(native),frames=frames,fields=fields,
                active_limit=MAX_STREAM_VALUES_BYTES,
                packed_limit=min(MAX_PAYLOAD_BYTES,per_frame))


def fits_budget(dimensions,budget):
    if budget['native']:
        return (native_encoding_bytes(dimensions)<=budget['active_limit']
                and packed_frame_bytes(dimensions)<=budget['packed_limit'])
    return math.prod(dimensions)*24<=budget['active_limit']
