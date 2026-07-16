import pytest

from cloth_next.updater.channel_policy import (
    allowed_release_channels,
    publication_targets,
    release_visible_in,
)


def test_cumulative_channel_visibility_matrix():
    assert allowed_release_channels("stable") == {"stable"}
    assert allowed_release_channels("beta") == {"stable", "beta"}
    assert allowed_release_channels("dev") == {"stable", "beta", "dev"}
    assert release_visible_in("stable", "stable")
    assert release_visible_in("stable", "beta")
    assert release_visible_in("stable", "dev")
    assert release_visible_in("beta", "beta")
    assert release_visible_in("beta", "dev")
    assert not release_visible_in("beta", "stable")
    assert release_visible_in("dev", "dev")
    assert not release_visible_in("dev", "beta")
    assert not release_visible_in("dev", "stable")


def test_publication_targets_are_inverse_visibility_matrix():
    assert publication_targets("stable") == ("stable", "beta", "dev")
    assert publication_targets("beta") == ("beta", "dev")
    assert publication_targets("dev") == ("dev",)
    with pytest.raises(ValueError):
        publication_targets("nightly")
