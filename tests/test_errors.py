# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import FrozenInstanceError

import pytest

from cloth_next.core.errors import ErrorCategory, ErrorRecord


def test_all_required_error_categories_exist():
    assert {item.name for item in ErrorCategory} == {
        "USER_INPUT", "SCENE_VALIDATION", "SOLVER_INSTALLATION",
        "SOLVER_CONNECTION", "PROTOCOL_COMPATIBILITY", "SIMULATION",
        "CACHE", "UPDATE", "DEPENDENCY", "INTERNAL",
    }


def test_error_record_is_immutable_and_captures_exception_type():
    record = ErrorRecord.create(
        category=ErrorCategory.DEPENDENCY,
        user_message="Missing dependency",
        technical_message="module unavailable",
        recommended_action="Install the packaged extension build",
        context={"module": "example"},
        exception=ImportError("example"),
    )
    assert record.original_exception_type == "ImportError"
    assert record.context == (("module", "'example'"),)
    with pytest.raises(FrozenInstanceError):
        record.recoverable = True

