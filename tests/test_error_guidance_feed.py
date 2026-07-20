# SPDX-License-Identifier: GPL-3.0-or-later
"""The public errors.json feed is generated from the shipped catalogue and
must stay parseable by the Companion's online-guidance client."""

import json

from cloth_next.core.error_codes import ERROR_CODES
from companion.error_guidance import parse_guidance
from tools.build_error_guidance import build_document, render


def test_feed_covers_every_catalogue_code_with_matching_action():
    document = build_document()
    assert document["schema"] == 1
    assert document["source"] == "cloth_next/core/error_codes.py"
    feed = {item["code"]: item for item in document["errors"]}
    assert set(feed) == set(ERROR_CODES)
    for code, info in ERROR_CODES.items():
        assert feed[code]["action"] == info.action
        assert feed[code]["cause"] == info.cause
        assert feed[code]["stage"] == info.stage


def test_rendered_feed_round_trips_through_the_companion_parser():
    guidance = parse_guidance(render().encode("utf-8"))
    # Every catalogue action survives the Companion's own validation/indexing.
    assert guidance == {code: info.action for code, info in ERROR_CODES.items()}


def test_intersection_and_instability_guidance_no_longer_points_at_drivers():
    # Regression for the misleading advice: E162 now owns mid-run intersections
    # and no longer says "first Bake frame"; E164 leads with the real cause.
    assert "Pressure" in ERROR_CODES["CNX-E162"].action
    assert "first Bake frame" not in ERROR_CODES["CNX-E162"].action
    assert ERROR_CODES["CNX-E164"].action.lower().index("cause") < \
        ERROR_CODES["CNX-E164"].action.lower().index("gpu")


def test_feed_render_is_valid_json():
    json.loads(render())
