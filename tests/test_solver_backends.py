from types import SimpleNamespace

from cloth_next.simulation.backends import (BackendId, MappingKind,
                                             backend_spec, default_backend)


def test_ppf_is_deterministic_default_and_unknown_values_fail_closed():
    from cloth_next.blender import solver_backends
    assert default_backend() is BackendId.PPF
    assert solver_backends.active_backend_id(None) is BackendId.PPF
    scene = SimpleNamespace(
        cloth_next_solver=SimpleNamespace(backend="UNKNOWN"))
    assert solver_backends.active_backend_id(scene) is BackendId.PPF


def test_backend_capabilities_report_verified_cloth_next_scope_only():
    ppf = backend_spec(BackendId.PPF)
    newton = backend_spec(BackendId.NEWTON)
    assert ppf.capabilities.recovery is True
    assert newton.capabilities.cloth is True
    assert newton.capabilities.live_preview is False
    assert newton.capabilities.animated_colliders is True
    assert newton.capabilities.rods is False
    assert newton.capabilities.soft_bodies is True
    assert newton.capabilities.rigid_bodies is True
    assert newton.capabilities.mixed_simulation is True


def test_material_mapping_is_formal_and_retains_unsupported_values():
    ppf = backend_spec("PPF")
    newton = backend_spec("NEWTON")
    assert ppf.mapping_for("sideways_response").kind is MappingKind.EXACT
    assert newton.mapping_for("sideways_response").kind is MappingKind.APPROXIMATE
    assert newton.mapping_for("stretch_limit").kind is MappingKind.UNSUPPORTED


def _object(role, *, pressure=False, sewing=False, force_type="GRAVITY"):
    return SimpleNamespace(cloth_next=SimpleNamespace(
        enabled=True, role=role,
        pressure=SimpleNamespace(enable_inflate=pressure,
                                 sewing_enabled=sewing),
        force=SimpleNamespace(force_type=force_type)))


def test_newton_preflight_accepts_verified_dynamic_roles():
    from cloth_next.blender import solver_backends
    scene = SimpleNamespace(
        cloth_next_solver=SimpleNamespace(backend="NEWTON"),
        objects=(_object("SOFT_BODY"),))
    assert solver_backends.unsupported_reason(scene) == ""
    scene.objects = (_object("CLOTH", pressure=True),)
    assert "Pressure" in solver_backends.unsupported_reason(scene)
    scene.objects = (_object("CLOTH"), _object("FORCE", force_type="WIND"))
    assert "only Gravity" in solver_backends.unsupported_reason(scene)


def test_legacy_newton_selection_is_migratable_without_changing_default():
    from cloth_next.blender import solver_backends
    legacy = SimpleNamespace(
        cloth_next_newton_preview=SimpleNamespace(bake_backend="NEWTON"))
    assert solver_backends.active_backend_id(legacy) is BackendId.NEWTON
    canonical = SimpleNamespace(
        cloth_next_solver=SimpleNamespace(backend="PPF"),
        cloth_next_newton_preview=SimpleNamespace(bake_backend="NEWTON"))
    assert solver_backends.active_backend_id(canonical) is BackendId.PPF
