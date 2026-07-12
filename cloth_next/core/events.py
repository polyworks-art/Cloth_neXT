# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure state-machine inputs and emitted application commands."""

from enum import Enum, auto


class Event(Enum):
    INSTALL_REQUESTED = auto()
    INSTALL_SUCCEEDED = auto()
    INSTALL_FAILED = auto()
    START_REQUESTED = auto()
    START_SUCCEEDED = auto()
    START_FAILED = auto()
    TRANSFER_REQUESTED = auto()
    TRANSFER_SUCCEEDED = auto()
    TRANSFER_FAILED = auto()
    BUILD_REQUESTED = auto()
    BUILD_SUCCEEDED = auto()
    BUILD_FAILED = auto()
    SIMULATION_REQUESTED = auto()
    RESUMABLE_STATE_SAVED = auto()
    FETCH_REQUESTED = auto()
    FETCH_COMPLETED = auto()
    CANCEL_REQUESTED = auto()
    CANCEL_COMPLETED = auto()
    STOPPED = auto()
    UPDATE_REQUESTED = auto()
    UPDATE_COMPLETED = auto()
    OPERATION_FAILED = auto()
    RECOVER_TO_STOPPED = auto()
    RECOVER_TO_READY = auto()


class SideEffectCommand(Enum):
    INSTALL_SOLVER = auto()
    START_BACKEND = auto()
    TRANSFER_SCENE = auto()
    REQUEST_BUILD = auto()
    START_SIMULATION = auto()
    FETCH_FRAMES = auto()
    CANCEL_OPERATION = auto()
    APPLY_UPDATE = auto()

