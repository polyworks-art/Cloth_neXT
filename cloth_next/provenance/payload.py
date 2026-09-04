# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Versioned, non-personal ThreadMark payloads."""

from __future__ import annotations
from dataclasses import dataclass

PRODUCT_ID = 0x434E  # ASCII "CN"; Cloth NeXt product provenance only.
SCHEMA_VERSION = 1
FORMAT_VERSION = 1
PAYLOAD_BITS = 40


def _crc12(value: int, width: int) -> int:
    """CRC-12/3GPP polynomial over exactly ``width`` most-significant bits."""
    crc = 0
    for shift in range(width - 1, -1, -1):
        feedback = ((crc >> 11) & 1) ^ ((value >> shift) & 1)
        crc = (crc << 1) & 0xFFF
        if feedback:
            crc ^= 0x80F
    return crc


@dataclass(frozen=True, slots=True)
class ThreadMarkPayloadV1:
    """40-bit BCH_SUPER application payload.

    Layout: product 16, schema 4, format 4, reserved 4, CRC-12 12.
    No field can represent a person, account, machine, licence, or filename.
    """

    product_id: int = PRODUCT_ID
    schema_version: int = SCHEMA_VERSION
    format_version: int = FORMAT_VERSION
    reserved: int = 0

    def to_bits(self) -> str:
        if not (
            0 <= self.product_id <= 0xFFFF
            and 0 <= self.schema_version <= 0xF
            and 0 <= self.format_version <= 0xF
            and 0 <= self.reserved <= 0xF
        ):
            raise ValueError("ThreadMark payload field is out of range")
        head = (
            (self.product_id << 12)
            | (self.schema_version << 8)
            | (self.format_version << 4)
            | self.reserved
        )
        return f"{head:028b}{_crc12(head, 28):012b}"

    @classmethod
    def from_bits(cls, bits: str) -> "ThreadMarkPayloadV1":
        if len(bits) != PAYLOAD_BITS or set(bits) - {"0", "1"}:
            raise ValueError("ThreadMark V1 payload must contain exactly 40 bits")
        raw = int(bits, 2)
        head, checksum = raw >> 12, raw & 0xFFF
        if _crc12(head, 28) != checksum:
            raise ValueError("ThreadMark payload integrity check failed")
        payload = cls(
            product_id=(head >> 12) & 0xFFFF,
            schema_version=(head >> 8) & 0xF,
            format_version=(head >> 4) & 0xF,
            reserved=head & 0xF,
        )
        if payload.product_id != PRODUCT_ID:
            raise ValueError("payload belongs to a different product")
        if payload.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported ThreadMark schema")
        if payload.format_version != FORMAT_VERSION or payload.reserved:
            raise ValueError("unsupported ThreadMark payload format")
        return payload
