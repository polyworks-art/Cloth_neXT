# Deterministic self-contact — profiling report (2026-10-17)

Reference kept unchanged: worker `f3a071af…`, CUDA backend `9f4f156c…` (copied to
`.hydro-test-tmp/reproducibility-20261017/reference-deterministic/`).

## Measurements

Solver phase timers, full NEON 130–159 (`blender-corrected-20261017/fair-runtime.json`, `runtime-ablation.json`):

| case | asm_contact total | per Newton step | whole run |
|---|---|---|---|
| unordered research worker | 3.2 s | ~11 ms | 50 s |
| only filtered contact | 239.7 s | ~0.8 s | 282 s |
| all five opt-ins | ~171 s + queued work | ~0.9 s | 334 s |

Contact count is ~21,300 mean / ~24,700 max in all variants; per-step contact cost grows from ~140 ms to >560 ms with the contact count.

Nsight Systems, ordered reference, first 3 frames (`profile-20261017/ordered3_*`): 62.7 s GPU kernel time, of which

| kernel group | GPU s | share |
|---|---|---|
| `contact_*_traverse_entry_serial` (edge-edge 37.3, point-face 14.4, point-edge 4.5) | 56.2 | 89.7 % |
| serial dynamic CSR fill/transpose | 4.4 | 7.0 % |
| contact select/probe passes (parallel, scratch) | 0.68 | 1.1 % |
| serial stitch / fixed-CSR push | 0.40 | 0.6 % |
| scratch span copies | 0.14 | 0.2 % |

Host side, 92 % of CUDA API time is `cudaStreamSynchronize` waiting on those serial kernels.

## Cause

The reference contact ordering runs the original traversal body in a single GPU thread
(`<<<1,1>>>`, 32 queries per launch) for every contributing query, including the BVH
traversal and narrow phase. The parallel probe that selects those queries is cheap. The
cost is therefore not the ordering principle but the serial execution of ~10^4 traversals
per assembly on one thread.

## What has to stay identical

Inside a traversal the order-sensitive writes are the float atomics into
`out_vertex_force` and `out_fixed_value` and the slot claims on `dyn_claim` (slot index
decides where `dyn_row/column/block` land and later the CSR fill order). Integer counters
(`assembled`, contact statistics) are order-independent; `out_overlap[k]` is per query.
`AtomicFloatSlot` offers no load, so no body reads a value another query accumulates.

## Planned implementation (opt-in `PPF_REPRO_FAST_CONTACT=1`, reference path kept)

1. Count pass, parallel, on the existing scratch copies: per query the number of float
   atomic contributions and of dynamic slot claims (`dyn_claim` pointed at a per-query
   counter; float atomics counted, not executed).
2. Exclusive prefix sums give each query its log offset and its dynamic slot base
   (`current dyn_claim + prefix`), i.e. exactly the slots the serial reference assigns.
3. Live pass, all queries in parallel, original body: slot claims go to the per-query
   seeded counter; float atomics are written to a log `(address, value)` at the query's
   offset in body order.
4. Stable radix sort of the log by address, then one thread per address applies its
   contributions with the same `atomicAdd` in (query, body) order, i.e. the serial
   reference order. `dyn_claim` is advanced by the total.

Expected result: bit-identical to the reference, not merely deterministic. The float
atomic primitive gains a branch on a device-global record context that is null outside
this pass. No formula, threshold, guard or parameter changes. The serial CSR fill
(7 %) is the next candidate after this.

## Work-in-progress checkpoint (paused 2026-10-17 late evening)

- Source: `.hydro-test-tmp/reproducibility-20261017/fast-source` (copy of `native-source`, reference untouched). Diff against the reference source: `docs/validation/gaia026/reproducibility/native-fast-contact-wip.patch` (apply after the two existing patches). Build: `build-fast-cuda.cmd` → `fast-cuda-build/lib/libsimbackend_cuda.dll` (`f6f91151…`).
- 3-frame NEON control (`fast-control3/`, recipe `recipes/run-fast-contact-control.py`): the hook-compiled DLL WITHOUT `PPF_REPRO_FAST_CONTACT` is byte-identical to the reference (7/7 outputs), so the hook is inert when off.
- WITH `PPF_REPRO_FAST_CONTACT=1` the worker aborts at the first contact assembly (C++ exception through the FFI, "panic in a function that cannot unwind"). Not yet diagnosed; compute-sanitizer 13.2 could not attach. Next: print the exception text in `repro_cuda_check`/launcher, check arena `resolve` inside the new seed/finish kernels and the CUB temp-storage path, then rerun the 3-frame byte comparison.
- Pitfall found and fixed in the control recipe: a backend DLL next to the worker exe wins over PATH on Windows (`fast-control3-invalid-dll-search/` loaded the reference DLL). The recipe now asserts the loaded module directory per case.
