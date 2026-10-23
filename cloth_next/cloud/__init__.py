# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloud Bakes: package, transfer and re-import solver runs (no ``bpy``).

A Cloud Bake is the local Bake up to and including export. The exported
solver inputs become an immutable, content-addressed package plus an Import
Snapshot; the raw solver output that comes back runs through the same
extraction, Sewing correction, coordinate transform and PC2 writer as a local
Bake (``ppf.results`` and ``bake.result_import``).

Modules:

``privacy``        fail-closed scan for paths, user names and secrets
``package_store``  immutable SHA-256 store outside temp and cache folders
``snapshot``       versioned Import Snapshot (``RunPlan`` subset) + digest
``package``        package manifest from exported solver inputs
``transport``      backend interface (the ``/v1/cloud/`` contract is pending)
``registry``       persistent local job cache with operation intents
``jobs``           upload / submit / reconcile / download state machine
``results``        raw-output archive per job, outside Clear Result's reach
``importer``       raw output + snapshot -> PC2 caches via the shared path
"""
