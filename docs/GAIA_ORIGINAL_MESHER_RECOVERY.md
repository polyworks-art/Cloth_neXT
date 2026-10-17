> Abschluss und differenzierte Bewertung: `GAIA_RECONSTRUCTION_ACCEPTANCE_RESULT.md`.
> Der30-Frame-Bake ist inzwischen bestanden. Korrektur zur fr�heren pauschalen
> Integrit�tsaussage:die aktuelle Original-Blend unterscheidet sich seit einem
> vor den aktuellen Tests liegenden �nderungszeitpunkt von der eingefrorenen
> Referenz. Referenzkopie und93gepr�fteFLIP-Payloads sind unver�ndert. Details
> und Hashes im Abschlussbericht; keine R�cksetzung der Originaldatei.

# GAIA — Original FLIP mesher recovery and practical acceptance

Stand: 17.10.2026. **Experimental validation in progress; no release approval.**

## Confirmed root cause

Original cache log identifies FLIP 1.8.8 Individual, 2026-07-27 / 759LY.
Installed Individual source archive has matching version/date but different source
build identifier (539NT); compiler15.2 differs from original15.0. No binary identity claim.
Static cached obstacle and all full particles/velocities are available. No saved native
liquid SDF was found. Read-only source bridge calls original meshing without simulation
advance; position/velocity input and output gates are exact. Public update(0) was rejected:
it advances synthetic particles. Never applied to NEON.

Original three-chunk source replay reproduces valid131–133 with identical triangles,
max2.536 / 2.325 / 5.066 µm vertex error, and reproduces defective130/135.
Actual original proximity-pair finder in an isolated shadow copy proves false welds
between DIFFERENT grid edges: three in130, two in135. Defects coincide with original
chunk boundaries global gridY170/340 (worldY≈-0.6571/+0.7054m). They are algorithmic
chunk-join errors, not evidence that separate water bodies are invalid.

Candidate retains original particle radius, 8.0142mm surface lattice, three compute
chunks, original obstacle offset and smoothing. Only cross-chunk identity changes:
vertices combine by original global integer grid edge (origin+axis). No caps, new
triangles, particle removal, radius tuning or simulation update.
All31 reconstructed frames130–160: closed, consistent winding, same triangle and
component counts as source.149/151 each have one fewer vertex; join identity evidence
and independent occupancy control are being checked before full-sequence bake.
157 retains the same four zero-area triangles present in original. These are reported,
not silently removed. A single-chunk mesher is an independent control, not adopted
as production transport; its historical strict geometric rejection remains preserved.

## Error metrics — explicitly separate

2.49165mm in135 is maximum BIDIRECTIONAL NEAREST-VERTEX distance; not Hausdorff,
not triangle distance, not implicit-field residual. Local sampled point-to-triangle
comparisons (10 samples/triangle,30mm expanded region) observe maxima1.03835mm in130
and0.40713mm in135. These are sampled measurements, not certified upper bounds.
Particle radius residuals only establish support and are not geometric error estimates.
Changed vertices above10µm:15 in130,10 in135, confined to seam neighborhoods.

The old10µm connectivity gate remains a reproduction diagnostic. Under the user's
revised instructions, practical physical and visual acceptance is separate. Prospective
criteria: `GAIA_PRACTICAL_RECONSTRUCTION_ACCEPTANCE.md`; raw evidence is untouched.

## Physical sensitivity measured

Valid131–134 transport: all18 velocity/SDF payloads and transforms BIT IDENTICAL
between reconstructed three-chunk candidate and successful reference. Hence identical
force and wetting evaluation at identical native states; not a new trajectory claim.
Fine7.826mm SDF passes all9 exact-reference cloth quadrature occupancy screens.
Coarse15.653mm fails the smaller wet garment in all3frames; fine grid retained.

Five actual unchanged CUDA native solves on copied131–133 scene: two baselines,
SDF±2.5mm and coarse grid; all finish without bounds/refusal/nonfinite errors.
The ±2.5mm offsets are sensitivity experiments ONLY, not an adopted water modification.
Main cloth:±offset max normal-load change0.3522N, RMS displacement<=0.1141mm.
Unchanged repeats already differ up to4.5572mm locally (0.0571mm RMS); offset cases
up to5.2874mm. Do not attribute full maxima to fluid reconstruction. Prospective
short wet-area/load/trajectory/temporal screens pass. Only2force outputs: cannot certify
30-frame continuity or visual quality. Existing strict CPU/CUDA parity blocker stays open.

Independent original single-chunk control on130/135 uses actual particles/velocities,
original grid/radius/obstacle/smoothing, no chunk join: zero occupancy disagreements
in100000 deterministic samples near frozen reference cloth perframe; three-point
cloth wetting exactly matches candidate for all3garments. This is local sampled
validation on frozen131cloth, not a proof over all space or actual130/135trajectories.

## Completed separate Blender test

Regular1–30+real31 CUDA operator passed (~352.5s), fresh-process reload all30PC2samples
PASS; native/PC2 max6.104e-8m; reciprocal collider audit0crossings in30frames. All3cloths,
original materials,186attachments. Minimum SDF output clearance0.234959m; native iterate
guard fully active. Sampled maxRSS6.893GB/private8.713GB; GPU6134MiB system-wide.
This does not substitute130–159. Old tight-quality native references use different
Newton/CG settings; measured~1.48mm RMS/20.45mm max is not an equivalence claim.

## Pending before final acceptance

Short regular Blender bake containing formerly invalid130; then130–159+real160,
PC2 save/reload, collider audit, long temporal force/wetting diagnostics and visual
trajectory review. Production remains disabled. CPU/CUDA strict self-contact parity
19/20 historical pass count is unresolved. Original .blend/cache and official binaries
unchanged; no version change, commit, push, tag or release.

Evidence: `validation/gaia026/hydrodynamics/flip-original/` (geometry sequence,
point-triangle-distance, particle-support, independent-occupancy-control,
reference-transport-comparison, frozen-wet-area-sensitivity, native-sensitivity).
Original replay/candidate logs and staged inputs remain in `.hydro-test-tmp/`.
