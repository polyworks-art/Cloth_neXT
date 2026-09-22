# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Wire behavior shared by independently verified solver releases."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ProtocolAdapter:
    id: str
    supported_schema: str
    legacy_ccd: bool = False

    def adapt_scene_params(self, scene: dict, quality: object) -> None:
        if self.legacy_ccd:
            from .schema.params import float32_wire
            scene["ccd-reduction"] = float32_wire(quality.ccd_reduction)
            scene["ccd-max-iter"] = int(quality.ccd_max_iter)


ADAPTERS = MappingProxyType({
    "legacy-schema2": ProtocolAdapter("legacy-schema2", "2", True),
    "modern-schema2": ProtocolAdapter("modern-schema2", "2"),
})

# Historical schema-1 encoder fixtures remain available for import and tests;
# the runtime compatibility manifest still permits only verified schema 2.
PARAM_ENCODER_COMPATIBILITY = MappingProxyType({("0.13", "1"): "legacy-schema2"})
LEGACY_PARAM_PROTOCOL = "0.13"
