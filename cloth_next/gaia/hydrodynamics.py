# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental SI reference; not an official GAIA force-field capability.

Inputs must already be in metres and physical seconds. Wet fractions must come
from surface geometry or a prescribed reference, never particle-support density.
The coefficients describe a two-sided impermeable sheet closure, not a measured
textile material. Hydrostatics, added mass and fluid feedback are absent.
"""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class SheetDrag:
    density_kg_m3: float = 1000.0
    normal_coefficient: float = 1.0
    tangential_coefficient: float = 0.0

    def __post_init__(self):
        values = (self.density_kg_m3, self.normal_coefficient, self.tangential_coefficient)
        if not all(np.isfinite(v) and v >= 0 for v in values):
            raise ValueError('Density and resistance coefficients must be finite and nonnegative')


def triangle_load(positions_m, velocities_m_s, fluid_velocity_m_s, wet_fraction,
                  model=SheetDrag()):
    """Return nodal N, positive -dF/dv (9x9), and geometric diagnostics.

    Centroid quadrature, equal barycentric force distribution. The returned
    velocity tangent holds geometry and sampled fluid fixed; a Newton caller
    must recompute geometry and fluid at each iterate. It is a PSD quasi-Newton
    tangent, not the full follower-load/spatial-sampling derivative.
    """
    x = np.asarray(positions_m, dtype=float)
    v = np.asarray(velocities_m_s, dtype=float)
    u = np.asarray(fluid_velocity_m_s, dtype=float)
    if x.shape != (3, 3) or v.shape != (3, 3) or u.shape != (3,):
        raise ValueError('Expected triangle positions/velocities (3,3), fluid (3,)')
    if not all(np.isfinite(a).all() for a in (x, v, u)):
        raise ValueError('Nonfinite geometry or velocity')
    if not np.isfinite(wet_fraction) or not 0 <= wet_fraction <= 1:
        raise ValueError('Wet fraction must be a finite area fraction in [0,1]')
    cross = np.cross(x[1]-x[0], x[2]-x[0])
    twice_area = np.linalg.norm(cross)
    if twice_area == 0:
        return np.zeros((3, 3)), np.zeros((9, 9)), {'area_m2': 0., 'wet_area_m2': 0.}
    n = cross / twice_area
    area = twice_area / 2
    r = u-v.mean(axis=0)
    s = n @ r
    t = r-s*n
    speed = np.linalg.norm(t)
    factor = .5*model.density_kg_m3*area*wet_fraction
    total = factor*(model.normal_coefficient*abs(s)*s*n +
                    model.tangential_coefficient*speed*t)
    nn = np.outer(n, n)
    tangent = factor*2*model.normal_coefficient*abs(s)*nn
    if speed > 0:
        tangent += factor*model.tangential_coefficient*(speed*(np.eye(3)-nn)+np.outer(t,t)/speed)
    return np.tile(total/3, (3,1)), np.tile(tangent/9, (3,3)), {
        'area_m2': float(area), 'wet_area_m2': float(area*wet_fraction),
        'relative_speed_m_s': float(np.linalg.norm(r)),
        'dynamic_pressure_pa': float(.5*model.density_kg_m3*(r@r)),
        'normal_speed_m_s': float(s), 'force_n': total.tolist(),
    }


def nodal_masses(positions_m, faces, areal_density_kg_m2):
    """Lumped REST-area cloth masses, independent of the water force."""
    x = np.asarray(positions_m, float)
    f = np.asarray(faces, int)
    if not np.isfinite(areal_density_kg_m2) or areal_density_kg_m2 <= 0:
        raise ValueError('Positive finite cloth areal density required')
    triangles = x[f]
    area = np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0],
                                   triangles[:,2]-triangles[:,0]), axis=1)/2
    masses = np.zeros(len(x))
    np.add.at(masses, f.ravel(), np.repeat(area*areal_density_kg_m2/3, 3))
    return masses


def implicit_normal_step(x, velocity, dt, *, mass_kg, area_m2, fluid_m_s,
                         stiffness_n_m=0., rest_m=0., wall_max_m=None,
                         wet_fraction=1., model=SheetDrag(), tolerance_n=1e-9):
    """Reduced one-DOF reference with a unilateral moving wall (end-step pose).

    Solves momentum and contact together. A caller may use a separately derived
    cantilever stiffness; this is not a shell bending/contact solver substitute.
    Returns displacement, velocity, wall reaction N and Newton diagnostics.
    """
    values=(x,velocity,dt,mass_kg,area_m2,fluid_m_s,stiffness_n_m,rest_m,wet_fraction)
    if not all(np.isfinite(a) for a in values) or dt<=0 or mass_kg<=0 or area_m2<0 or stiffness_n_m<0 or not 0<=wet_fraction<=1:
        raise ValueError('Invalid reduced reference inputs')
    if wall_max_m is not None and not np.isfinite(wall_max_m):
        raise ValueError('Wall position must be finite')
    k=.5*model.density_kg_m3*area_m2*wet_fraction*model.normal_coefficient
    def residual(v):
        r=fluid_m_s-v
        return mass_kg*(v-velocity)/dt+stiffness_n_m*(x+dt*v-rest_m)-k*abs(r)*r
    v=float(velocity)
    for iteration in range(100):
        r=residual(v)
        if abs(r)<=tolerance_n:
            break
        derivative=mass_kg/dt+dt*stiffness_n_m+2*k*abs(fluid_m_s-v)
        v-=r/derivative
    else:
        raise ArithmeticError('Reduced hydrodynamic Newton solve did not converge')
    reaction=0.
    if wall_max_m is not None and x+dt*v>wall_max_m:
        v=(wall_max_m-x)/dt
        reaction=-residual(v)
        assert reaction>=0
    balance=residual(v)+reaction
    return dict(position_m=x+dt*v,velocity_m_s=v,reaction_n=reaction,
                residual_n=balance,newton_iterations=iteration+1)
