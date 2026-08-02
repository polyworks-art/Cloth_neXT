# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent external Newton worker. This process never imports ``bpy``."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

from .contracts import (BackendCapabilities, NEWTON_VERSION, PROTOCOL_VERSION,
                        PreviewCreateRequest, WARP_VERSION)
from .protocol import decode_message, encode_message
from .snapshots import SnapshotStore
from .request_artifact import read_request_artifact

_PROCESS_STARTED = time.perf_counter()
_ENVIRONMENT_METRICS = {}


class WorkerSession:
    def __init__(self, request: PreviewCreateRequest):
        started = time.perf_counter()
        import newton
        import numpy as np
        import warp as wp
        self.newton_import_seconds = _ENVIRONMENT_METRICS.get(
            "newton_import_seconds", time.perf_counter() - started)

        if newton.__version__ != request.expected_newton_version:
            raise RuntimeError(f"Newton {request.expected_newton_version} required; found {newton.__version__}")
        if wp.__version__ != request.expected_warp_version:
            raise RuntimeError(f"Warp {request.expected_warp_version} required; found {wp.__version__}")
        # stdout is the authoritative framed protocol. Warp diagnostics belong
        # on the separately captured stderr/log path, never in that channel.
        cuda_started = time.perf_counter()
        wp.config.log_level = wp.LOG_WARNING
        wp.init()
        device = wp.get_device("cuda:0")
        if not device.is_cuda:
            raise RuntimeError("Newton Live Preview requires a supported CUDA device")
        self.newton_cuda_init_seconds = _ENVIRONMENT_METRICS.get(
            "newton_cuda_init_seconds", time.perf_counter() - cuda_started)

        self.newton, self.np, self.wp = newton, np, wp
        self.request = request
        self.current_frame = request.frame_start
        self.target_frame = request.frame_start
        self.paused = False
        self.cancelled = False
        self.frame_times = []
        self.first_frame_seconds = None
        self.last_frame_seconds = None
        self.rewind_seconds = None
        self.result_dir = Path(request.result_directory).resolve()
        self.result_dir.mkdir(parents=True, exist_ok=True)

        builder = newton.ModelBuilder(up_axis=newton.Axis.Z)
        if request.solver == "STYLE3D":
            newton.solvers.SolverStyle3D.register_custom_attributes(builder)
        self.cloth_slices = []
        particle_offset = 0
        for cloth_index, cloth in enumerate(request.cloths):
            material = cloth.material
            flat_indices = [index for tri in cloth.mesh.triangles for index in tri]
            common_cloth = {
                "pos": wp.vec3(0.0, 0.0, 0.0),
                "rot": wp.quat_identity(),
                "scale": 1.0,
                "vel": wp.vec3(0.0, 0.0, 0.0),
                "vertices": cloth.mesh.vertices,
                "indices": flat_indices,
                "density": material.surface_density,
                "tri_kd": material.stretch_damping,
                "edge_kd": material.bend_damping,
                "particle_radius": material.particle_radius,
                "validate_mesh": True,
                "label": f"Cloth NeXt Live Preview {cloth.identifier}",
            }
            if request.solver == "STYLE3D":
                from newton.solvers import style3d
                panel_verts = _style3d_panel_coordinates(
                    np, cloth.mesh.vertices, cloth.mesh.triangles)
                style3d.add_cloth_mesh(
                    builder, **common_cloth, panel_verts=panel_verts,
                    panel_indices=flat_indices,
                    tri_aniso_ke=wp.vec3(
                        material.stretch_stiffness,
                        material.stretch_stiffness,
                        material.shear_stiffness),
                    edge_aniso_ke=wp.vec3(
                        material.bend_stiffness,
                        material.bend_stiffness,
                        material.bend_stiffness),
                    tri_ka=material.shear_stiffness)
            else:
                builder.add_cloth_mesh(
                    **common_cloth,
                    tri_ke=material.stretch_stiffness,
                    tri_ka=material.shear_stiffness,
                    edge_ke=material.bend_stiffness)
            count = len(cloth.mesh.vertices)
            self.cloth_slices.append((cloth.identifier, particle_offset,
                                      particle_offset + count))
            particle_offset += count

        materials = tuple(cloth.material for cloth in request.cloths)
        shape_cfg = newton.ModelBuilder.ShapeConfig()
        shape_cfg.mu = max(material.friction for material in materials)
        collider_started = time.perf_counter()
        self.collider_sources = []
        for collider in request.colliders:
            mesh = newton.Mesh(
                collider.vertices,
                [index for tri in collider.triangles for index in tri],
                compute_inertia=False, is_solid=False)
            builder.add_shape_mesh(body=-1, mesh=mesh, cfg=shape_cfg)
            self.collider_sources.append(mesh)
        self.newton_collider_build_seconds = time.perf_counter() - collider_started

        if request.solver == "VBD":
            builder.color(include_bending=True)
        build_started = time.perf_counter()
        self.model = builder.finalize(device=device)
        self.model.set_gravity(request.gravity)
        self.model.soft_contact_mu = max(material.friction for material in materials)
        self.model.soft_contact_radius = max(material.particle_radius for material in materials)
        self.model.soft_contact_margin = max(material.collision_margin for material in materials)

        flags = self.model.particle_flags.numpy()
        for cloth, (_identifier, start, _end) in zip(
                request.cloths, self.cloth_slices):
            for index in cloth.pin_indices:
                flags[start + index] = (
                    flags[start + index] & ~newton.ParticleFlags.ACTIVE)
        self.model.particle_flags = wp.array(flags, device=device)

        self.pipeline = newton.CollisionPipeline(
            self.model, soft_contact_margin=max(
                material.collision_margin for material in materials))
        self.contacts = self.pipeline.contacts()
        if request.solver == "STYLE3D":
            self.solver = newton.solvers.SolverStyle3D(
                self.model, iterations=request.quality.iterations,
                linear_iterations=request.quality.iterations)
        else:
            self.solver = newton.solvers.SolverVBD(
                self.model, iterations=request.quality.iterations,
                particle_enable_self_contact=request.quality.self_collision,
                particle_self_contact_radius=max(
                    material.particle_radius for material in materials),
                particle_self_contact_margin=max(
                    material.collision_margin for material in materials),
                deterministic=wp.DeterministicMode.RUN_TO_RUN)
        self.state_in = self.model.state()
        self.state_out = self.model.state()
        self.control = self.model.control()
        self.snapshot_store = SnapshotStore(request.quality.maximum_snapshots)
        self.snapshot_store.put(self.current_frame, self._snapshot())
        self.model_build_seconds = time.perf_counter() - build_started
        self.worker_start_seconds = time.perf_counter() - started
        self.initial_result = self.publish_result(self.current_frame)

    def _snapshot(self):
        return (self.state_in.particle_q.numpy().copy(),
                self.state_in.particle_qd.numpy().copy())

    def _restore(self, snapshot) -> None:
        positions, velocities = snapshot
        self.state_in.particle_q.assign(positions)
        self.state_in.particle_qd.assign(velocities)
        self.state_out.particle_q.assign(positions)
        self.state_out.particle_qd.assign(velocities)

    def _simulate_frame(self) -> None:
        dt = (self.request.time_scale / self.request.fps
              / self.request.quality.substeps)
        started = time.perf_counter()
        for substep in range(self.request.quality.substeps):
            if self.cancelled:
                return
            self._update_animated_colliders(
                self.current_frame,
                float(substep + 1) / self.request.quality.substeps, dt)
            self._update_animated_pins(
                self.current_frame,
                float(substep + 1) / self.request.quality.substeps, dt)
            self.state_in.clear_forces()
            self.pipeline.collide(self.state_in, self.contacts)
            self.solver.step(self.state_in, self.state_out, self.control,
                             self.contacts, dt)
            self.state_in, self.state_out = self.state_out, self.state_in
        positions = self.state_in.particle_q.numpy()
        if not self.np.isfinite(positions).all():
            raise RuntimeError("Newton produced non-finite cloth positions")
        self.current_frame += 1
        elapsed = time.perf_counter() - started
        self.last_frame_seconds = elapsed
        if self.first_frame_seconds is None:
            self.first_frame_seconds = elapsed
        self.frame_times.append(elapsed)
        if len(self.frame_times) > 120:
            del self.frame_times[:-120]
        if ((self.current_frame - self.request.frame_start)
                % self.request.quality.snapshot_cadence == 0):
            self.snapshot_store.put(self.current_frame, self._snapshot())

    def _update_animated_colliders(self, frame: int, alpha: float,
                                   dt: float) -> None:
        if not self.request.collider_animations:
            return
        first = self.request.frame_start
        source_index = max(0, min(frame - first,
                                  self.request.frame_end - first))
        target_index = min(source_index + 1,
                           self.request.frame_end - first)
        for animation in self.request.collider_animations:
            source = self.collider_sources[animation.collider_index]
            before = self.np.asarray(
                animation.samples[source_index], dtype=self.np.float32)
            after = self.np.asarray(
                animation.samples[target_index], dtype=self.np.float32)
            positions = before + (after - before) * float(alpha)
            velocities = ((after - before) /
                          max(dt * self.request.quality.substeps, 1.0e-12))
            source.mesh.points.assign(positions)
            source.mesh.velocities.assign(velocities)
            source.mesh.refit()
        self.model.bvh_refit_shapes(self.state_in)

    def _update_animated_pins(self, frame: int, alpha: float,
                              dt: float) -> None:
        if not self.request.pin_animations:
            return
        first = self.request.frame_start
        source_index = max(0, min(frame - first,
                                  self.request.frame_end - first))
        target_index = min(source_index + 1,
                           self.request.frame_end - first)
        positions_in = self.state_in.particle_q.numpy()
        velocities_in = self.state_in.particle_qd.numpy()
        positions_out = self.state_out.particle_q.numpy()
        velocities_out = self.state_out.particle_qd.numpy()
        frame_dt = max(dt * self.request.quality.substeps, 1.0e-12)
        for animation in self.request.pin_animations:
            cloth = self.request.cloths[animation.cloth_index]
            _identifier, cloth_start, _cloth_end = self.cloth_slices[
                animation.cloth_index]
            indices = self.np.asarray(
                [cloth_start + index for index in cloth.pin_indices],
                dtype=self.np.int64)
            before = self.np.asarray(
                animation.samples[source_index], dtype=self.np.float32)
            after = self.np.asarray(
                animation.samples[target_index], dtype=self.np.float32)
            positions = before + (after - before) * float(alpha)
            velocities = (after - before) / frame_dt
            positions_in[indices] = positions
            velocities_in[indices] = velocities
            positions_out[indices] = positions
            velocities_out[indices] = velocities
        self.state_in.particle_q.assign(positions_in)
        self.state_in.particle_qd.assign(velocities_in)
        self.state_out.particle_q.assign(positions_out)
        self.state_out.particle_qd.assign(velocities_out)

    def advance_to(self, frame: int) -> dict:
        frame = max(self.request.frame_start,
                    min(self.request.frame_end, int(frame)))
        self.target_frame = frame
        if frame < self.current_frame:
            rewind_started = time.perf_counter()
            selected = self.snapshot_store.nearest_at_or_before(frame)
            if selected is None:
                raise RuntimeError("Newton initial rewind snapshot is missing")
            self.current_frame, snapshot = selected
            self._restore(snapshot)
            self.rewind_seconds = time.perf_counter() - rewind_started
        while self.current_frame < frame and not self.paused and not self.cancelled:
            self._simulate_frame()
        return self.publish_result(self.current_frame)

    def publish_result(self, frame: int) -> dict:
        positions = self.np.asarray(self.state_in.particle_q.numpy(), dtype="<f4")
        temporary = self.result_dir / f"frame_{frame}.npy.tmp"
        artifact = self.result_dir / f"frame_{frame}.npy"
        with temporary.open("wb") as stream:
            self.np.save(stream, positions, allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, artifact)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        return {
            "event": "result", "protocol_version": PROTOCOL_VERSION,
            "session_id": self.request.session_id,
            "scene_identity": self.request.scene_identity, "frame": frame,
            "vertex_count": len(positions), "artifact": str(artifact),
            "sha256": digest, "complete": True,
        }

    def status(self) -> dict:
        average = (sum(self.frame_times) / len(self.frame_times)
                   if self.frame_times else None)
        snapshot_bytes = sum(q.nbytes + qd.nbytes for q, qd in self.snapshot_store._items.values())
        return {
            "event": "status", "session_id": self.request.session_id,
            "scene_identity": self.request.scene_identity,
            "state": "PAUSED" if self.paused else "READY",
            "current_frame": self.current_frame, "target_frame": self.target_frame,
            "snapshot_count": len(self.snapshot_store),
            "snapshot_bytes": snapshot_bytes,
            "average_frame_seconds": average,
            "newton_worker_start_seconds": self.worker_start_seconds,
            "newton_import_seconds": self.newton_import_seconds,
            "newton_cuda_init_seconds": self.newton_cuda_init_seconds,
            "newton_model_build_seconds": self.model_build_seconds,
            "newton_collider_build_seconds": self.newton_collider_build_seconds,
            "newton_first_frame_seconds": self.first_frame_seconds,
            "newton_average_frame_seconds": average,
            "newton_last_frame_seconds": self.last_frame_seconds,
            "newton_snapshot_count": len(self.snapshot_store),
            "newton_snapshot_bytes": snapshot_bytes,
            "newton_rewind_seconds": self.rewind_seconds,
            "newton_current_frame": self.current_frame,
            "newton_target_frame": self.target_frame,
            "newton_frames_behind": max(0, self.target_frame - self.current_frame),
            "newton_peak_vram_bytes": None,
            "newton_contact_count": None,
            "newton_self_contact_count": None,
            "newton_solver": self.request.solver,
        }


def _style3d_panel_coordinates(np, vertices, triangles):
    """Create a stable 2-D rest panel from an arbitrary planar cloth mesh.

    Style3D requires panel coordinates. Cloth NeXt does not yet export a
    canonical sewing-pattern UV domain, so the experimental adapter projects
    the rest mesh along its dominant aggregate normal. This is exact for the
    planar meshes supported by the current preview slice and fails closed for
    a degenerate projection instead of silently building invalid constraints.
    """
    points = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(triangles, dtype=np.int64)
    normals = np.cross(points[faces[:, 1]] - points[faces[:, 0]],
                       points[faces[:, 2]] - points[faces[:, 0]])
    aggregate = np.abs(normals).sum(axis=0)
    drop_axis = int(np.argmax(aggregate))
    axes = [axis for axis in range(3) if axis != drop_axis]
    panel = points[:, axes].copy()
    signed = np.cross(panel[faces[:, 1]] - panel[faces[:, 0]],
                      panel[faces[:, 2]] - panel[faces[:, 0]]).sum()
    if abs(float(signed)) <= 1.0e-12:
        raise ValueError(
            "Style3D requires a non-degenerate planar cloth rest projection")
    if signed < 0.0:
        panel = panel[:, ::-1]
    return panel.tolist()


def _emit(value: dict) -> None:
    sys.stdout.buffer.write(encode_message(value))
    sys.stdout.buffer.flush()


def run() -> int:
    session = None
    for raw in sys.stdin.buffer:
        try:
            message = decode_message(raw)
            command = message.get("command")
            if command == "health":
                import_started = time.perf_counter()
                import newton
                import warp as wp
                import_seconds = time.perf_counter() - import_started
                cuda_started = time.perf_counter()
                wp.config.log_level = wp.LOG_WARNING
                wp.init()
                cuda_seconds = time.perf_counter() - cuda_started
                cuda = next((device for device in wp.get_devices() if device.is_cuda), None)
                _ENVIRONMENT_METRICS.update(
                    newton_import_seconds=import_seconds,
                    newton_cuda_init_seconds=cuda_seconds)
                _emit({"event": "health", "protocol_version": PROTOCOL_VERSION,
                       "newton_version": newton.__version__, "warp_version": wp.__version__,
                       "cuda_device": cuda.name if cuda else None, "ready": cuda is not None,
                       "newton_worker_start_seconds": time.perf_counter() - _PROCESS_STARTED,
                       "newton_import_seconds": import_seconds,
                       "newton_cuda_init_seconds": cuda_seconds})
            elif command == "capabilities":
                _emit({"event": "capabilities", **BackendCapabilities().__dict__})
            elif command == "create_preview":
                if session is not None:
                    raise RuntimeError("a Newton preview session is already active")
                if "request_artifact" in message:
                    wire = read_request_artifact(
                        message["request_artifact"],
                        message["result_directory"])
                else:
                    wire = message["request"]
                request = PreviewCreateRequest.from_wire(wire)
                session = WorkerSession(request)
                _emit({**session.status(), "event": "created"})
                _emit(session.initial_result)
            elif command == "update_target_frame":
                if session is None:
                    raise RuntimeError("no Newton preview session exists")
                session.paused = False
                _emit(session.advance_to(int(message["frame"])))
                _emit(session.status())
            elif command == "pause":
                if session is not None:
                    session.paused = True
                    _emit(session.status())
            elif command in {"reset", "restore_snapshot"}:
                if session is None:
                    raise RuntimeError("no Newton preview session exists")
                session.paused = False
                _emit(session.advance_to(int(message.get("frame", session.request.frame_start))))
            elif command == "status":
                _emit(session.status() if session else {"event": "status", "state": "IDLE"})
            elif command == "cancel":
                if session is not None:
                    session.cancelled = True
                _emit({"event": "cancelled"})
            elif command == "destroy_preview":
                session = None
                _emit({"event": "destroyed"})
            elif command == "update_parameters":
                raise RuntimeError("hot Newton parameter updates are not enabled; rebuild the preview")
            elif command == "shutdown":
                _emit({"event": "shutdown"})
                return 0
            else:
                raise ValueError(f"unknown Newton worker command: {command}")
        except Exception as exc:  # worker failures are protocol data, never log parsing
            _emit({"event": "error", "error_type": type(exc).__name__,
                   "message": str(exc), "traceback": traceback.format_exc()[-16384:]})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", required=True)
    parser.parse_args()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
