# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Public ThreadMark interfaces; watermark technology stays private."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class DetectionStatus(str, Enum):
    DETECTED = "DETECTED"
    LIKELY = "LIKELY"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_DETECTED = "NOT_DETECTED"


@dataclass(frozen=True, slots=True)
class DecodedSignal:
    payload_bits: str = ""
    ecc_valid: bool = False
    confidence: float = 0.0
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ThreadMarkDetectionResult:
    status: DetectionStatus
    confidence: float
    payload_valid: bool
    schema_version: int | None
    regions_tested: int
    regions_matched: int
    decoded_payload: str = ""
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "confidence": self.confidence,
            "payload_valid": self.payload_valid,
            "schema_version": self.schema_version,
            "regions_tested": self.regions_tested,
            "regions_matched": self.regions_matched,
            "decoded_payload": self.decoded_payload,
            "diagnostics": dict(self.diagnostics),
        }


class ThreadMarkEncoder(ABC):
    @abstractmethod
    def encode(self, image, payload_bits: str): ...


class ThreadMarkDecoder(ABC):
    @abstractmethod
    def decode(self, image) -> DecodedSignal: ...
