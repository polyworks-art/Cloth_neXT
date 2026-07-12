# PPF parameter mapping audit

Status: verified candidate mappings only. This file does not authorize UI properties
until encode/decode fixtures pass against protocol `0.11`.

Phase 3A proves only this fixed developer-test subset: SHELL
`model=baraff-witkin`, density `1000 kg/m³`, density-normalized `young-mod=1.0`,
`poiss-rat=0.35`, `bend=10.0`, both damping values `0.0`, friction `0.5`,
contact gap `0.001 m`, contact offset `0.0`, and strain limit `0.0`; STATIC
friction/gap/offset are `0.5/0.001/0.0`. Scene values are `dt=0.001 s`, scene
FPS, swapped gravity, and seven produced solver frames. Other rows remain audit
candidates and are not implemented UI mappings.

PPF uses SI-oriented values and swaps Blender coordinates in the official encoder.
Young's modulus requires special care: the solver field `young-mod` is density
normalized (Pa / density) unless the authored value is already normalized.

| Blender-style label | PPF key | Transform/unit | Upstream default/range | Mapping | Limits |
|---|---|---|---|---|---|
| Gravity | `scene.gravity` | axis swap; m/s² | `(0,0,-9.8)` | direct | scene-wide |
| Frame Rate | encoding time base | frames/s | 60, min 24 | direct | not a material parameter |
| Time Step | `scene.dt` | seconds | 0.01; 0.001–0.01 | direct | solver substep |
| Density | `group.density` | kg/m³ | material dependent | direct | positive |
| Young's Modulus | `group.young-mod` | Pa divided by density | material dependent | transformed | label must disclose normalization |
| Poisson Ratio | `group.poiss-rat` | unitless | material dependent | direct | shell supports it |
| Bending | `group.bend` | upstream scalar | material dependent | direct PPF control | not Blender bending stiffness equivalence |
| Tension/Compression/Shear | — | — | — | unsupported | do not invent decomposition from Young/Poisson |
| Damping | `group.deformation-damping` | unitless upstream scalar | material dependent | approximation in UI | deformation damping only |
| Bending Damping | `group.bending-damping` | unitless upstream scalar | material dependent | direct | shell/rod |
| Air Damping | `scene.air-density`, `scene.air-friction` | kg/m³, ratio | 0.001; 0.2 (0–1) | approximation | normal drag always weighted; shell area based |
| Friction | `group.friction` | coefficient | material dependent | direct | pair combination uses scene friction mode |
| Collision Distance | `group.contact-gap` | length; world-scaled | material dependent | approximation | barrier activation gap, not Blender collision distance semantics |
| Collision Thickness | `group.contact-offset` | length; world-scaled | material dependent | approximation | contact skin/offset |
| Self Collision | same global contact system | — | on unless contact disabled | capability mapping | no verified independent self-collision toggle |
| Pressure Strength | `group.pressure` | upstream pressure scalar | 0 when disabled | direct candidate | shell only; requires closed oriented surface validation |
| Strain Limit | `group.strain-limit` | UI percent / 100 | 0 when disabled | direct transformed | disabled by non-unit shrink in official client |
| Pin Group | `param.pin_config[uuid]` | per-vertex records | none | structural mapping | binary/weighted semantics need dedicated fixtures |
| Pin stiffness | pin record/config | upstream-specific | documented by official pin UI | direct candidate | do not expose before schema fixture |
| Collider | group type `STATIC` | mesh + contact gap/offset/friction | — | direct role | static and captured animated variants supported upstream |
| Sewing | `stitch-stiffness` / cross stitch | upstream scalar | material/pair dependent | direct candidate | not dynamic tearing |

Additional verified advanced scene keys include `frames`, `min-newton-steps`,
`air-density`, `air-friction`, `wind`, `cg-max-iter`, `cg-tol`, `precond`, Schwarz
levels, `contact-nnz`, `isotropic-air-friction`, `line-search-max-t`,
`constraint-ghat`, `include-face-mass`, `friction-mode`, and `disable-contact`.
These belong in Advanced PPF/technical preferences, not a Blender-like basic panel.

Verified shell group capabilities include density, normalized Young modulus, Poisson
ratio, friction, stitch stiffness, deformation and bending damping, contact gap and
offset, strain limit, bend, anisotropic shrink, pressure, plasticity, bending
plasticity, velocity schedules and collision windows. “Supported upstream” does not
automatically mean “MVP UI”.

## Pressure and tearing

Uniform shell pressure is real (`pressure`, zero when disabled). The official material
documentation describes inflation for closed shells. Cloth NeXt must validate boundary
edges and give normal-orientation warnings. Target volume, compressibility, gas models
and animated pressure are **not mapped here** because no verified protocol mapping was
established during this audit. Animation must remain disabled until a verified dynamic
parameter exists.

No verified dynamic mesh tearing/breakable-stitch capability was found. `TearMask`,
`TearPreprocessor`, and `BreakableStitchBackend` remain disabled extension interfaces.

## Source

The mapping is derived from the official
[parameter encoder](https://github.com/st-tech/ppf-contact-solver/blob/7193f158e3843597070f66cb29af19efd9bdcff7/blender_addon/core/encoder/params.py)
and [current parameter documentation](https://st-tech.github.io/ppf-contact-solver/blender_addon/workflow/params/index.html).
Cloth NeXt will implement its own encoder from the server schema and test vectors; no
official add-on code or runtime imports will be used.
