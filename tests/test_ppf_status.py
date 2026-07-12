# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from cloth_next.core.state import ApplicationState
from cloth_next.ppf.status import WireStatus, application_state_hint, parse_status


@pytest.mark.parametrize("wire,expected", [
    (WireStatus.NO_DATA, None), (WireStatus.NO_BUILD, None),
    (WireStatus.BUILDING, ApplicationState.BUILDING),
    (WireStatus.READY, ApplicationState.READY),
    (WireStatus.RESUMABLE, ApplicationState.PAUSED),
    (WireStatus.FAILED, ApplicationState.ERROR),
    (WireStatus.BUSY, ApplicationState.SIMULATING),
    (WireStatus.SAVE_AND_QUIT, ApplicationState.CANCELLING),
])
def test_wire_state_mapping(wire, expected):
    assert application_state_hint(wire) is expected


def test_status_parser_requires_protocol():
    with pytest.raises(ValueError):
        parse_status({"status": "READY"})

