# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated Newton Live Preview lifecycle."""

from __future__ import annotations

from enum import Enum


class PreviewState(str, Enum):
    DISABLED = "DISABLED"
    STARTING_WORKER = "STARTING_WORKER"
    CAPTURING_SCENE = "CAPTURING_SCENE"
    BUILDING_MODEL = "BUILDING_MODEL"
    READY = "READY"
    PLAYING = "PLAYING"
    CATCHING_UP = "CATCHING_UP"
    PAUSED = "PAUSED"
    RESETTING = "RESETTING"
    STALE = "STALE"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


_ALLOWED = {
    PreviewState.DISABLED: {PreviewState.CAPTURING_SCENE},
    PreviewState.CAPTURING_SCENE: {PreviewState.STARTING_WORKER,
                                   PreviewState.FAILED,
                                   PreviewState.STOPPING},
    PreviewState.STARTING_WORKER: {PreviewState.BUILDING_MODEL,
                                   PreviewState.FAILED,
                                   PreviewState.STOPPING},
    PreviewState.BUILDING_MODEL: {PreviewState.READY, PreviewState.FAILED,
                                  PreviewState.STOPPING},
    PreviewState.READY: {PreviewState.PLAYING, PreviewState.CATCHING_UP,
                         PreviewState.PAUSED,
                         PreviewState.RESETTING, PreviewState.STALE,
                         PreviewState.STOPPING, PreviewState.FAILED},
    PreviewState.PLAYING: {PreviewState.CATCHING_UP, PreviewState.PAUSED,
                           PreviewState.READY, PreviewState.RESETTING,
                           PreviewState.STALE, PreviewState.STOPPING,
                           PreviewState.FAILED},
    PreviewState.CATCHING_UP: {PreviewState.PLAYING, PreviewState.PAUSED,
                               PreviewState.READY, PreviewState.RESETTING,
                               PreviewState.STALE, PreviewState.STOPPING,
                               PreviewState.FAILED},
    PreviewState.PAUSED: {PreviewState.PLAYING, PreviewState.CATCHING_UP,
                          PreviewState.RESETTING, PreviewState.STALE,
                          PreviewState.STOPPING, PreviewState.FAILED},
    PreviewState.RESETTING: {PreviewState.READY, PreviewState.PLAYING,
                             PreviewState.PAUSED, PreviewState.FAILED,
                             PreviewState.STOPPING},
    PreviewState.STALE: {PreviewState.STOPPING, PreviewState.CAPTURING_SCENE,
                         PreviewState.FAILED},
    PreviewState.STOPPING: {PreviewState.DISABLED, PreviewState.FAILED},
    PreviewState.FAILED: {PreviewState.STOPPING, PreviewState.DISABLED,
                          PreviewState.CAPTURING_SCENE},
}


def transition(current: PreviewState, target: PreviewState) -> PreviewState:
    current, target = PreviewState(current), PreviewState(target)
    if target == current:
        return current
    if target not in _ALLOWED[current]:
        raise ValueError(f"invalid Newton preview transition: {current.value} -> {target.value}")
    return target


def status_label(state: PreviewState, *, current_frame=0, target_frame=0) -> str:
    state = PreviewState(state)
    labels = {
        PreviewState.DISABLED: "Newton unavailable",
        PreviewState.STARTING_WORKER: "Starting Newton",
        PreviewState.CAPTURING_SCENE: "Exporting Preview",
        PreviewState.BUILDING_MODEL: "Building Preview",
        PreviewState.READY: "Live",
        PreviewState.PLAYING: "Live",
        PreviewState.PAUSED: "Paused",
        PreviewState.RESETTING: "Resetting Preview",
        PreviewState.STALE: "Scene Changed",
        PreviewState.STOPPING: "Resetting Preview",
        PreviewState.FAILED: "Preview Error",
    }
    if state is PreviewState.CATCHING_UP:
        return f"Calculating Frame {current_frame} / Target {target_frame}"
    return labels[state]
