# SPDX-License-Identifier: GPL-3.0-or-later
"""GAIA Water Flow switches on only with GAIA access, then with pressure."""
import sys
from types import SimpleNamespace as NS


def _settings(enabled):
    return NS(water_flow_enabled=enabled, water_hydrodynamic_pressure=False)


def _run(blender_env, monkeypatch, *, access, pressure_available, enabled=True):
    props = blender_env.object_properties
    gaia_account = sys.modules['cloth_next.blender.gaia_account']
    monkeypatch.setattr(gaia_account, 'access_granted', lambda: access)
    monkeypatch.setattr(props, '_hydrodynamic_pressure_available', lambda: pressure_available)
    shared = []
    monkeypatch.setattr(props, '_on_shared_water_update', lambda s, _c: shared.append(s.water_flow_enabled))
    settings = _settings(enabled)
    props._on_water_flow_enabled_update(settings, NS())
    return settings, shared


def test_without_gaia_access_water_flow_switches_back_off(blender_env, monkeypatch):
    settings, shared = _run(blender_env, monkeypatch, access=False, pressure_available=True)
    assert not settings.water_flow_enabled and not settings.water_hydrodynamic_pressure
    assert shared == []


def test_with_access_water_flow_starts_with_pressure(blender_env, monkeypatch):
    settings, shared = _run(blender_env, monkeypatch, access=True, pressure_available=True)
    assert settings.water_flow_enabled and settings.water_hydrodynamic_pressure
    assert shared == [True]


def test_switching_off_never_needs_access(blender_env, monkeypatch):
    settings, shared = _run(blender_env, monkeypatch, access=False, pressure_available=True, enabled=False)
    assert not settings.water_flow_enabled and shared == [False]


def _gaia(monkeypatch, *, access, local):
    gaia_account = sys.modules['cloth_next.blender.gaia_account']
    monkeypatch.setattr(gaia_account, 'snapshot', lambda: gaia_account.GaiaState(access=access))
    monkeypatch.setattr(gaia_account, 'local_entitlement_valid', lambda: local)
    return gaia_account


def test_unknown_online_state_falls_back_to_the_stored_signed_entitlement(blender_env, monkeypatch):
    # Offline, or the start-up check was skipped: the token the TETHYS Engine verifies counts.
    assert _gaia(monkeypatch, access=None, local=True).access_granted()
    assert not _gaia(monkeypatch, access=None, local=False).access_granted()


def test_an_online_decision_beats_the_stored_entitlement(blender_env, monkeypatch):
    assert _gaia(monkeypatch, access='granted', local=False).access_granted()
    for decided in ('declined', 'pending'):
        assert not _gaia(monkeypatch, access=decided, local=True).access_granted()


def test_skipped_startup_check_is_retried(blender_env, monkeypatch):
    gaia_account = sys.modules['cloth_next.blender.gaia_account']
    blender_env.bpy.app.background = False
    monkeypatch.setattr(gaia_account, 'refresh', lambda: False)  # a Bake was running
    assert gaia_account._initial_check() == gaia_account.INITIAL_RETRY_SECONDS
    monkeypatch.setattr(gaia_account, 'refresh', lambda: True)
    assert gaia_account._initial_check() is None


def test_local_entitlement_check_verifies_the_stored_token(blender_env, monkeypatch, tmp_path):
    from tests.test_tethys import write_gaia_account
    gaia_account = sys.modules['cloth_next.blender.gaia_account']
    root = write_gaia_account(tmp_path / 'account')
    monkeypatch.setenv('CLOTH_NEXT_ACCOUNT_ROOT', str(root))
    monkeypatch.setitem(gaia_account._local, 'key', None)
    from cloth_next.polyworks import trust
    # Signed with the test key: only valid where that key is trusted.
    assert gaia_account.local_entitlement_valid() is False
    monkeypatch.setattr(trust, 'load_trust', lambda: trust.TrustStore({'tethys-test': __import__(
        'cryptography.hazmat.primitives.asymmetric.ed25519', fromlist=['x']).Ed25519PrivateKey
        .from_private_bytes(bytes(range(32))).public_key().public_bytes_raw()}, frozenset()))
    monkeypatch.setitem(gaia_account._local, 'key', None)
    assert gaia_account.local_entitlement_valid() is True
