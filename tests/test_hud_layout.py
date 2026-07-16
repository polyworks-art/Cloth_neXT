# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from cloth_next.telemetry.hud_layout import (RamAutoCancelGuard, ResourceHistory,
                                             build_resource_card)
from cloth_next.telemetry.snapshot import GpuTelemetry, SystemTelemetrySnapshot


def _telemetry(updated_at=1.0):
    return SystemTelemetrySnapshot(
        (GpuTelemetry(1, "RTX", 73, 6 << 30, 12 << 30, 60, 200),),
        25, 8 << 30, 32 << 30, updated_at=updated_at)


def test_resource_card_has_only_cpu_ram_and_vram():
    card = build_resource_card(_telemetry())
    assert [metric.key for metric in card.metrics] == ["cpu", "ram", "vram"]
    assert [metric.fraction for metric in card.metrics] == [.25, .25, .5]
    text = " ".join(metric.value for metric in card.metrics)
    assert "Solver" not in text and "Frame" not in text and "PID" not in text


def test_layout_anchors_stay_inside_viewport():
    for anchor in ("TOP_LEFT", "TOP_RIGHT", "BOTTOM_LEFT", "BOTTOM_RIGHT"):
        card = build_resource_card(_telemetry(), anchor=anchor,
                                   viewport_width=800, viewport_height=600)
        assert 0 <= card.x <= 800 - card.width
        assert 0 <= card.y <= 600 - card.height


def test_history_adds_one_point_per_telemetry_sample():
    history = ResourceHistory(length=3)
    assert history.sample(_telemetry(1.0)) is True
    assert history.sample(_telemetry(1.0)) is False
    assert history.sample(_telemetry(2.0)) is True
    assert list(history.series["cpu"]) == [.25, .25]
    assert len(history.series["ram"]) == 2
    assert len(history.series["vram"]) == 2


def test_ram_limit_is_clamped_and_exposed_to_graph():
    assert build_resource_card(
        _telemetry(),ram_limit_percent=90).ram_limit_fraction == .9
    assert build_resource_card(
        _telemetry(),ram_limit_percent=None).ram_limit_fraction is None


def test_ram_auto_cancel_requires_two_distinct_over_limit_samples():
    guard=RamAutoCancelGuard(90,2)
    high=lambda stamp: SystemTelemetrySnapshot(
        ram_used_bytes=91,ram_total_bytes=100,updated_at=stamp)
    assert guard.observe(high(1)) is False
    assert guard.observe(high(1)) is False
    assert guard.observe(high(2)) is True


def test_ram_auto_cancel_resets_after_safe_or_stale_sample():
    guard=RamAutoCancelGuard(90,2)
    sample=lambda used,stamp,stale=False: SystemTelemetrySnapshot(
        ram_used_bytes=used,ram_total_bytes=100,updated_at=stamp,stale=stale)
    assert guard.observe(sample(95,1)) is False
    assert guard.observe(sample(80,2)) is False
    assert guard.observe(sample(95,3)) is False
    assert guard.observe(sample(95,4,True)) is False
    assert guard.observe(sample(95,5)) is False
