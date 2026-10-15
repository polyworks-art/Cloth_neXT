# GAIA 3.0 — Acceptance Criteria (proposed)

**Status:** normative target specification; not a release certificate.
**Scope:** Cloth NeXt / GAIA, baked FLIP Fluids → GAIA → Cloth.
**Architecture rule:** **One-way only.** The cloth must never update FLIP velocity, pressure, free surface, obstacles, or cache. No solver feedback loop or FLIP re-bake is authorized.
**Implementation reality (15 Oct 2026):** existing GAIA 0.26 native velocity coupling has passed integration and long-sequence tests. Hydrodynamic Pressure is an experimental separate-solver CPU implementation with uniform prescribed flow, **not** yet a NEON FLIP-driven or CUDA-validated feature. Do not report this specification as implemented.

## Fundamental rule

**Large cloth motion MUST be possible when the actual fluid momentum transfer and unconstrained material/contact dynamics justify it.** A dry-like 3 mm response to a demonstrably strong impact on free cloth is not acceptable merely because a 250-frame cache was generated. Conversely, a cloth patch pressed against a collider may correctly move very little: quantify the contact reaction rather than prescribing displacement.

No post-solve offsets, hidden velocity/acceleration/displacement clamps, fabricated fluid data, arbitrary cinematic multipliers, or weakening of contact constraints to force a result.

Every release gate requires actual measured evidence, explicit test fixtures, tolerances chosen before execution, and a clear PASS/FAIL/NOT RUN. Passing a test for implementation of an assumed force closure does **not** validate that closure against measured real fabrics.

## P0 — mandatory for hydrodynamic GAIA 3.0 release

### G1 — Real FLIP input and provenance

- Load actual frame-dependent FLIP velocities and actual full fluid-surface/SDF data. Validate frame coverage, units, FPS, coordinate transforms, source fingerprint, and original-cache immutability.
- Sample velocity and wetting at current deformed triangle quadrature points in the **native** solver with spatial and temporal interpolation.
- Distinguish reconstructed particle support from measured physical density, free-surface location, and pressure; never equate these.
- Invalid, missing, or incompatible data fail explicitly; do not silently substitute zero flow.
- **PASS:** independent source-versus-native sampling probes at start/middle/end, interior/boundary, and interpolated times match within specified interpolation/reconstruction error bounds.

### G2 — Dimensionally consistent hydrodynamic forces

- Use real relative velocity, density in kg/m³, current triangle area/normals, fraction wetted and independently justified normal/tangential coefficients.
- Model forces in N, pressure in Pa, speeds in m/s. Assemble distributed triangle forces and velocity Jacobian into Newton/contact solve; no host postprocessing.
- Treat impact angle, opposite incidence, partial wetting, mesh refinement, dry limits and time-varying flow.
- Keep linear native Fluid Flow and quadratic hydrodynamics separately switchable; no unjustified double counting.
- **PASS:** total force matches the declared analytical *model* within 5% on predefined controlled fixtures; doubled relative speed yields about quadruple quadratic-load magnitude when other conditions remain fixed, and duplicated wetted area doubles the corresponding resultant model force.

### G3 — No artificially suppressed strong motion

- Maintain a **free or partially pinned** fabric impact reference at fixed mass, stiffness, mesh, fluid velocity and wetted area; include a second fixture with a body collider.
- For illustration only, water at 2 m/s impacting 0.1 m² normal to an impermeable surface with Cn=1 and rho=1000 kg/m³ starts with idealized q=2000 Pa and F=200 N. Real force evolves with fabric velocity, orientation and wetting.
- Before native tests, calculate a reference trajectory/force history for the agreed physical closure and boundary conditions. Fix tolerances *before* tuning.
- **PASS:** free-cloth displacement/velocity/impulse agree with independent converged reference within stated numerical tolerance. Include at least one fixture whose independently predicted motion exceeds **5 cm**, and ensure the native solver reproduces it. **5 cm is a test-selection condition, not a universal scene requirement.**
- Never weaken physics or raise strength to hit a cosmetic displacement target.

### G4 — Fluid loading with native contacts

- Press flexible cloth into static and animated body colliders; retain correct configured contact gap, body friction, self-contact, cloth-to-cloth, pins, attachments and material zones.
- Deformation around the blocked contact region must remain possible; moving cloth must not penetrate a collider beyond defined geometric tolerance.
- **PASS:** sampled continuous-motion and discrete contact checks, force/contact diagnostics and reference body pressure direction pass on both free and collider fixtures. Zero displacement into an immovable collider is not a fluid-force failure.

### G5 — Materials and inertia

- Correct effective cloth mass from areal density; separate this from already mass-normalized shell stiffness.
- Preserve distinct bending, stretching, damping and friction response, zone overrides and initial prestress. Document porous/permeable textile simplifications.
- **PASS:** individual parameter perturbation tests demonstrate expected behavior for increased density, bending stiffness, stretch resistance and damping without global default/preset changes.

### G6 — Robust nonlinear solves

- No NaN/Inf, runaway accelerations or silent stiffness/velocity caps. All triangles and cache frames finite; geometry/contact validity measured.
- **PASS:** dt-vs-dt/2 and mesh-resolution refinement match declared observables within preselected tolerances (aim 5% for converged smooth reference cases); difficult nonconvergent cases report failure rather than being relabeled success.
- Exercise impacts, changing wetted area, grazing flow, reverse flow, close contact and high speed; log Newton/PCG iterations.

### G7 — Native backend validation and capability gate

- CPU and CUDA both run actual compiled triangle-force and wetting kernels, including the complete native time integration and collision resolution.
- **PASS:** isolated CPU/CUDA force, Jacobian, geometry, and trajectory comparisons meet declared tolerances. A successful compile alone does not count.
- Existing official 0.22/0.23/0.26 installations remain untouched. Hydrodynamics UI is enabled **only** for an independently verified compatible native transport/build capability, not merely protocol version 0.26.

### G8 — Real NEON A/B/C validation

- Use copies of SW04_NEON with original three garments, attachments, zones, animation and collider setup; source FLIP and original blend remain unchanged.
- Run identical **Dry**, **existing native Fluid Flow**, and **Hydrodynamics** over an initial **30 frames** at 30 FPS; then a full 250-frame run only after short validation.
- For each garment report wetted area, local velocity, relative speed, triangle normal/tangential load, force vector, contact reaction if available, world-space PC2 motion, body-relative displacement, RMS and max wet/dry change, noise/repeatability, edge stretch and penetration.
- **PASS:** actual native hydrodynamic FLIP-driven solve, physical loading and observable response independently verified. Wet/dry difference must exceed numerical repeat scatter **where contact and flow justify an effect**. The .001 garment need not react when its surface is outside the water.
- A successful PC2 export alone is **not** PASS.

### G9 — Actual Blender artist lifecycle

- Installed add-on must expose compatible source selection, validation, pressure option, meaningful force diagnostics and accurate preparation/encoding/upload/build/simulation/fetch/import progress.
- Verify real interactive operator Bake, Re-bake, Cancel, Resume, Save/Reload, ownership-safe PC2 cleanup and meaningful errors on Windows.
- **PASS:** repeatable interactive tests with real external solver and valid pre-existing caches preserved on failed bakes; old profiles still function.

### G10 — Bounded resource behavior

- Stream/chunk source preparation; track actual peak RAM/VRAM if measurable, per-stage wall time and payload sizes. No hidden resolution reduction.
- Respect current 1 GiB PARAM budget or introduce separately verified bounded transport. Reject oversize work safely and keep previous caches.
- **PASS:** full NEON production fixture completes on an explicitly declared supported hardware profile, with validated output and no stranded processes/files on finish, cancel or failure. Document throughput and scaling rather than inventing a performance target.

## P1 — optional, **only** mandatory when advertised in release scope

- **G11 hydrostatics/buoyancy:** pressure/depth and two-sided loads with realistic displaced volume, no blanket single-sided load on submerged thin cloth.
- **G12 FLIP pressure export:** pressure grid provenance, units, gauge/reference convention, pressure difference treatment; cached FLIP flow did not respond to cloth, so real cloth-induced stagnation pressure cannot be claimed. No overlapping pressure/drag force counted twice.
- **G13 textiles/wetness:** measured or calibrated permeability, porous flow resistance, fluid absorption, drainage, changed wet mass/stiffness, adhesion as justified. No uncalibrated physical claims.

## Release decisions

| Gate | Minimum evidence | Decision |
| --- | --- | --- |
| **Experimental GO** | G1/G2 implemented on actual native CPU solver with real velocity + wetting; verified short force and contact cases | Explicitly experimental only |
| **Beta GO** | G1–G7 and G8 short NEON test; G9 interactive Blender verified; essential regression/lifecycle evidence | Public beta, no automatic stable promotion |
| **Stable GO** | All G1–G10, including complete NEON 250, CPU/CUDA certification, shipping/installer regression and no critical unresolved stability failures | Release only on separate maintainer authorization |

No automatic version bump, tag, public release, solver bundling, or channel change follows from passing these gates. Follow **docs/RELEASE_POLICY.md**.

## Explicitly OUT OF SCOPE

- Bidirectional FLIP ↔ Cloth feedback
- Cloth modifying FLIP velocity/pressure/free surface
- Coupled time-stepping/feedback convergence
- Re-baking FLIP iteratively from simulated cloth
- Cosmetic movement amplification presented as physical accuracy

## Evidence and historical context

- [GAIA 0.26 integration results](../GAIA026_INTEGRATION_REPORT.md)
- [GAIA 0.26 production-scale test report](../GAIA026_PRODUCTION_SCALE_REPORT.md)
- [GAIA 0.26 upstream backlog](../GAIA026_UPSTREAM_BACKLOG.md)
- [Solver parameter mapping](../PPF_PARAMETER_MAPPING.md)
- [Release policy](../RELEASE_POLICY.md)

The experimental hydrodynamic-pressure CPU checkout and FLIP surface transport investigation were discussed on 15 Oct 2026 but are not certified by the public GitHub baseline referenced above; attach new native evidence before checking off G1–G10.
