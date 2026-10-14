from types import SimpleNamespace as NS
import importlib

from cloth_next.updater.solver_registry import SolverRegistry,load_registry
from tests.test_solver_registry import installation


def test_unhealthy_selection_reports_reason_and_preserves_registry(blender_env,monkeypatch,tmp_path):
    m=importlib.import_module('cloth_next.blender.preferences')
    old=installation(tmp_path,'old','0.23','2')
    candidate=installation(tmp_path,'candidate','0.26','2',healthy=False)
    from dataclasses import replace
    candidate=replace(candidate,error='Previous Windows validation failed')
    registry=SolverRegistry((old,candidate),old.installation_id)
    monkeypatch.setattr(m,'_read_registry',lambda:(registry,None))
    monkeypatch.setattr(m,'_solver_session_active',lambda:False)
    monkeypatch.setattr(m.ManagedSolverPaths,'default',lambda:NS(registry_json=tmp_path/'registry.json'))
    writes=[]
    monkeypatch.setattr(m,'write_registry',lambda *args:writes.append(args))
    m._solver_enum_update(NS(selected_solver_installation_id=candidate.installation_id),None)
    assert not writes
    assert 'Previous Windows validation failed' in m._session.worker_error
    assert 'healthy verified compatible' in m._session.worker_error


def test_healthy_selection_persists_and_clears_previous_error(blender_env,monkeypatch,tmp_path):
    m=importlib.import_module('cloth_next.blender.preferences')
    old=installation(tmp_path,'old','0.23','2')
    candidate=installation(tmp_path,'candidate','0.26','2')
    registry=SolverRegistry((old,candidate),old.installation_id)
    path=tmp_path/'registry.json'
    monkeypatch.setattr(m,'_read_registry',lambda:(registry,None))
    monkeypatch.setattr(m,'_solver_session_active',lambda:False)
    monkeypatch.setattr(m.ManagedSolverPaths,'default',lambda:NS(registry_json=path))
    m._session.worker_error='Previous failure'
    m._solver_enum_update(NS(selected_solver_installation_id=candidate.installation_id),None)
    assert load_registry(path).selected_installation_id==candidate.installation_id
    assert m._session.worker_error is None


def test_selection_write_failure_is_visible(blender_env,monkeypatch,tmp_path):
    m=importlib.import_module('cloth_next.blender.preferences')
    candidate=installation(tmp_path,'candidate','0.26','2')
    registry=SolverRegistry((candidate,),candidate.installation_id)
    monkeypatch.setattr(m,'_read_registry',lambda:(registry,None))
    monkeypatch.setattr(m,'_solver_session_active',lambda:False)
    monkeypatch.setattr(m.ManagedSolverPaths,'default',lambda:NS(registry_json=tmp_path/'registry.json'))
    def write(*args):
        raise OSError('Registry is read-only')
    monkeypatch.setattr(m,'write_registry',write)
    m._solver_enum_update(NS(selected_solver_installation_id=candidate.installation_id),None)
    assert 'Registry is read-only' in m._session.worker_error


def test_missing_selection_is_visible(blender_env,monkeypatch):
    m=importlib.import_module('cloth_next.blender.preferences')
    monkeypatch.setattr(m,'_read_registry',lambda:(SolverRegistry(),None))
    monkeypatch.setattr(m,'_solver_session_active',lambda:False)
    m._solver_enum_update(NS(selected_solver_installation_id='removed-installation'),None)
    assert 'installation is missing' in m._session.worker_error
