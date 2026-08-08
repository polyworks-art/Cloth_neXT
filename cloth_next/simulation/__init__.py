# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Simulation orchestration placeholder; no implementation in Phase 1."""
from .backends import (BackendId, FieldMapping, MappingKind, SolverBackendSpec,
                       SolverCapabilities, backend_spec, default_backend)

__all__ = ("BackendId", "FieldMapping", "MappingKind", "SolverBackendSpec",
           "SolverCapabilities", "backend_spec", "default_backend")
