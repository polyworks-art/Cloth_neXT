# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental boundary control law. Not connected to production baking.

The bound applies to the estimated velocity and specified window, not to a
solver's unknown instantaneous state. Native validation must establish safety.
"""
import numpy as np


def limited_push(water_velocity, cloth_velocity, influence, response_time, window):
    """Return desired and bounded acceleration in physical m/s²."""
    if not np.isfinite([response_time, window]).all() or min(response_time, window) <= 0:
        raise ValueError('Positive finite response time and integration window required')
    water=np.asarray(water_velocity,dtype=float)
    cloth=np.asarray(cloth_velocity,dtype=float)
    support=np.asarray(influence,dtype=float)
    if water.shape!=cloth.shape or water.ndim!=2 or water.shape[1]!=3 or support.shape!=(len(water),):
        raise ValueError('Invalid Water Push sample shapes')
    if not np.isfinite(water).all() or not np.isfinite(cloth).all() or not np.isfinite(support).all() or np.any((support<0)|(support>1)):
        raise ValueError('Invalid Water Push samples or support')
    relative=water-cloth
    desired=relative*support[:,None]/response_time
    applied=relative*np.minimum(support/response_time,1/window)[:,None]
    return desired,applied


def output_velocity(current,previous,current_frame,previous_frame,fps):
    """Validated output secant; skipped authoritative boundaries are allowed."""
    if current_frame<=previous_frame or not np.isfinite(fps) or fps<=0:
        raise ValueError('Invalid authoritative output interval')
    current=np.asarray(current,dtype=float);previous=np.asarray(previous,dtype=float)
    if current.shape!=previous.shape or not np.isfinite(current).all() or not np.isfinite(previous).all():
        raise ValueError('Invalid output positions')
    return (current-previous)/((current_frame-previous_frame)/fps)


def quadratic_velocity(positions,times,evaluation_time=None):
    """Derivative of a nonuniform backward quadratic, optionally extrapolated.

    All times refer to public output samples. Extrapolation to a confirmed
    native hold time is a host estimate, not access to native state.
    """
    p=np.asarray(positions,dtype=float);t=np.asarray(times,dtype=float)
    if len(p)!=3 or t.shape!=(3,) or not np.isfinite(p).all() or not np.isfinite(t).all() or np.any(np.diff(t)<=0):
        raise ValueError('Three finite, strictly ordered output samples required')
    h0=t[1]-t[0];h1=t[2]-t[1]
    first=(p[1]-p[0])/h0;last=(p[2]-p[1])/h1
    acceleration=2*(last-first)/(h0+h1)
    velocity=last+h1/(h0+h1)*(last-first)
    when=t[2] if evaluation_time is None else float(evaluation_time)
    if not np.isfinite(when) or when<t[2]:raise ValueError('Invalid evaluation time')
    return velocity+acceleration*(when-t[2])


def discrete_push(water_velocity,cloth_velocity,influence,response_time,interval):
    """Exact-discrete requested relaxation; never changes cloth state directly."""
    # Reuse validation only; the old clipped controller is not the control law.
    limited_push(water_velocity,cloth_velocity,influence,response_time,interval)
    relative=np.asarray(water_velocity,dtype=float)-np.asarray(cloth_velocity,dtype=float)
    alpha=-np.expm1(-float(interval)/float(response_time))
    return relative*np.asarray(influence,dtype=float)[:,None]*alpha/interval
