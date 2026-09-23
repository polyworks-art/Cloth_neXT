# Windows x86_64 solver backend certification

Audit machine: Windows x86_64, NVIDIA GeForce RTX 4070 SUPER (driver 616.92),
AMD Radeon(TM) Graphics APU, Blender 5.2.2 LTS. This is measured evidence for
the official Gaia `2026-09-21-21-32` (package `0.1.0`, protocol `0.22`, schema
`2`) and Lumen `2026-08-12-15-47` (package `0.1.0`, protocol `0.18`, schema
`2`) Windows assets. Cloth NeXt does not redistribute either runtime.

## Three different claims

| Backend | Upstream release contents | Runtime on this machine | Cloth NeXt Bake evidence | Product support policy |
| --- | --- | --- | --- | --- |
| Gaia CUDA / NVIDIA | `target/cuda/release` server and worker | `--probe` named RTX 4070 SUPER; worker appeared in `nvidia-smi` | 30-frame Cloth + collider + gravity Bake, PC2, attachment and playback passed repeatedly | Verified NVIDIA CUDA |
| Gaia CPU | `target/cpu/release` server and worker | `--probe` reported host CPU | Equivalent 30-frame Bake passed | Verified CPU |
| Gaia ROCm / AMD | `target/rocm/release` server and worker | `--probe` named AMD Radeon(TM) Graphics APU | A light 5-frame Bake, PC2, attachment and playback passed with status `rocm` | AMD support remains in development; this APU result does not certify the wider AMD device range |
| Lumen CUDA / NVIDIA | `target/release` server and worker plus `libsimbackend_cuda.dll`; no CPU or ROCm build in the installed release | Lumen worker appeared in `nvidia-smi` on RTX 4070 SUPER | Equivalent 30-frame Bake passed | Verified NVIDIA CUDA |

Gaia's `target/local` directory contains auxiliary files, not a fourth backend.
Lumen protocol `0.18` does not offer `solver_backend` or `solver_target_dir`;
its `--backend` and `--probe` arguments are unsupported. The Lumen GPU claim
uses its exact worker path, CUDA runtime DLL, and live `nvidia-smi` process
sample during the completed Bake. It is not inferred from GPU presence alone.

For Gaia, the test pinned `CARGO_TARGET_DIR` to the selected build. Every Gaia
Bake checked the protocol `0.22` status fields against both the selected
backend and its exact target directory before uploading scene data. An empty,
unknown, or mismatched status fails the Bake. Production automatic selection
tries verified CUDA, then CPU. The packaged ROCm build is not selected
automatically while general AMD support remains in development.
Preferences nevertheless offers an explicit ROCm choice for users who wish to
test the packaged build. It displays the missing-general-verification notice,
requires a successful device probe, and rejects a different backend at server
startup. Explicit CUDA and CPU follow the same no-fallback rule.

## Switching evidence

The sequence CUDA → CPU → CUDA → Lumen CUDA → Gaia CUDA completed as five
normal 30-frame Bakes in one Blender process. The selection was written to
the test registry and read back before each run. Every run used that selected
installation, built and simulated its scene, fetched frames 1–29,
created a PC2 cache, attached a Mesh Cache modifier, evaluated playback at
frame 29, and stopped its owned server. The server PIDs were respectively
`45588`, `36152`, `40660`, `27628`, and `43832`; each launch ID differed.
Gaia status changed `cuda` → `cpu` → `cuda` → `cuda` with matching
`solver_target_dir` values. Lumen's worker path and NVIDIA process sample
identified its CUDA run. These results reject stale server, generation,
target-directory, and backend reuse for this sequence.

The first 30–35-frame attempt at the default `dt=0.001` produced solver output
but did not complete the fetch/PC2 stage in the allotted observation period;
it is not counted as a pass. The certification scene used `dt=0.01`. An
initial Lumen attempt was denied write access to its managed build cache by
the local sandbox; the permitted rerun passed. An initial ROCm Blender probe
could not load its server DLL until the release's `bin` directory was placed
on `PATH`; the pinned rerun passed. These are test-environment findings, not
evidence of broader hardware support.

## AMD follow-up procedure

On each proposed compatible AMD GPU, record the exact GPU and driver, run the
official ROCm worker's `--backend` and `--probe`, and then pin the ROCm server
and `CARGO_TARGET_DIR=target/rocm` for a Cloth NeXt Bake. Require status
`solver_backend=rocm` and the exact `solver_target_dir`, successful build and
simulation, every frame fetched, PC2 creation, Mesh Cache attachment, playback,
and clean owned-server shutdown. Repeat after a CUDA/CPU switch where those
backends exist. Reject any silent backend substitution. Expand the public AMD
support claim only after representative AMD hardware and workloads pass.
