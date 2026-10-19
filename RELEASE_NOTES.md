# Cloth NeXt 2.9.29

GAIA Bakes with the backend set to CUDA work again. In 2.9.27 and 2.9.28 an explicit CUDA choice stopped scene validation with CNX-E132 ("CUDA is not available in the selected solver release"), because backend detection expected a CPU server that the CUDA-only GAIA package does not have. CUDA is still probed on the GPU before each Bake; without a working NVIDIA GPU the message now says that the release is CUDA only.

The Solver Engine section is reorganised: the active engine with its Backend, Reproducible Bake and status comes first, then one row per engine (AETHER, GAIA) with its installed version, its install or update action and the GAIA account state. Release, protocol, schema and source sit in a Details section that starts closed. The engine still never switches by itself. Neither solver is bundled.
