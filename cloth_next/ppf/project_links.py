# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""One ownership authority for solver project aliases (never their targets)."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import shutil
import tempfile
import uuid

from ..core.errors import ClothNextError, ErrorCategory, ErrorRecord
from ..core.filesystem_paths import io_path as _io_path, resolved_path as _resolved_path


@dataclass(frozen=True)
class LinkState:
    kind: str
    target: str = ""
    tag: int = 0


def inspect_project_link(path: Path) -> LinkState:
    """lstat sees broken aliases; exists alone deliberately is not used."""
    try:
        info = _io_path(path).lstat()
    except FileNotFoundError:
        return LinkState("absent")
    tag = getattr(info, "st_reparse_tag", 0)
    if tag == 0xA0000003:
        kind = "junction"
    elif stat.S_ISLNK(info.st_mode) or tag == 0xA000000C:
        kind = "symlink"
    elif getattr(info, "st_file_attributes", 0) & 0x400:
        return LinkState("unexpected-reparse", tag=tag)
    else:
        return LinkState("directory" if stat.S_ISDIR(info.st_mode) else "file")
    try:
        target = str(_resolved_path(path))
    except (OSError, RuntimeError):
        target = "<unresolvable>"
    return LinkState(kind, target, tag)


def _create_link(path: Path, target: Path) -> None:
    if os.name != "nt":
        path.symlink_to(target, target_is_directory=True)
        return
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = ("New-Item -ItemType Junction -Path " + quote(path)
              + " -Target " + quote(target) + " -ErrorAction Stop | Out-Null")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                    "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()],
                   capture_output=True, check=True, timeout=30,
                   creationflags=subprocess.CREATE_NO_WINDOW)


class ProjectLink:
    """Durable ownership is rooted in the caller's authenticated CNX cache.

    No adoption of preexisting aliases, files, or even empty directories.
    Transactions retain both the old target and replacement paths so a crash
    between Windows renames can be reconciled without trusting path names.
    """
    def __init__(self, canonical: Path, target: Path, *, owned_root: Path,
                 project_name: str):
        if (not re.fullmatch(r"[A-Za-z0-9_-]+", project_name)
                or not canonical.is_absolute() or not target.is_absolute()
                or canonical.name != project_name or target.name != project_name):
            raise ValueError("invalid project alias identity")
        self.path = _resolved_path(canonical.parent) / project_name
        self.root = _resolved_path(owned_root)
        self.target = _resolved_path(target)
        if self.root not in self.target.parents:
            raise ValueError("project target is outside authenticated CNX root")
        self.name = project_name
        self.records = self.root / ".project-links"
        self.marker = self.records / (project_name + ".json")
        self._authority_record = None

    def _fail(self, message: str, *, owned=False, exception=None):
        try:
            state = inspect_project_link(self.path)
        except OSError:
            state = LinkState("inspection-failed")
        record = self._authority_record or {}
        proven = bool(owned and record.get("phase") in {"PREPARING", "COMMITTED"}
                      and state.kind in {"junction", "symlink"}
                      and state.target in {record.get("target"), record.get("previous_target")})
        return ClothNextError(ErrorRecord.create(
            category=ErrorCategory.CACHE,
            user_message="The solver project link could not be prepared safely.",
            technical_message=message,
            recommended_action="Keep the existing files. Inspect the project-link diagnostics before retrying.",
            recoverable=True, exception=exception,
            context={"expected_link": str(self.path), "expected_target": str(self.target),
                     "observed_type": state.kind, "observed_target": state.target,
                     "ownership_proven": proven,
                     "ownership_record_valid": bool(self._authority_record)}))

    @contextmanager
    def _lock(self):
        _io_path(self.records).mkdir(parents=True, exist_ok=True)
        if (_resolved_path(self.records).parent != self.root
                or inspect_project_link(self.records).kind != "directory"):
            raise self._fail("ownership marker directory escapes authenticated root")
        lock = self.records / (self.name + ".lock")
        if inspect_project_link(lock).kind not in {"absent", "file"}:
            raise self._fail("unexpected link at lifecycle lock path")
        if _io_path(lock).exists() and _io_path(lock).stat().st_nlink != 1:
            raise self._fail("lifecycle lock is hardlinked to another file")
        with _io_path(lock).open("a+b") as stream:
            try:
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b"0"); stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise self._fail("another lifecycle operation owns this project link", exception=exc) from exc
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def _read(self):
        if inspect_project_link(self.marker).kind not in {"absent", "file"}:
            raise self._fail("ownership marker is not an ordinary file")
        if _io_path(self.marker).exists() and _io_path(self.marker).stat().st_nlink != 1:
            raise self._fail("ownership marker is hardlinked to another file")
        try:
            value = json.loads(_io_path(self.marker).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise self._fail("invalid ownership marker", exception=exc) from exc
        if (not isinstance(value, dict) or value.get("version") != 1 or value.get("canonical") != str(self.path)
                or value.get("root") != str(self.root) or value.get("project") != self.name
                or value.get("phase") not in {"PREPARING", "COMMITTED", "REMOVED"}):
            raise self._fail("ownership marker identity mismatch")
        for key in ("target", "previous_target"):
            item = value.get(key)
            if (key == "target" and not isinstance(item, str)) or (
                    item is not None and (not isinstance(item, str)
                    or not Path(item).is_absolute()
                    or self.root not in _resolved_path(Path(item)).parents)):
                raise self._fail("ownership marker target escapes authenticated root")
        nonce = value.get("nonce", "")
        if not re.fullmatch(r"[a-f0-9]{32}", nonce):
            raise self._fail("invalid ownership transaction identity")
        for key in ("temporary", "backup"):
            expected = self.path.parent / (".cnx-link-" + nonce + "." + key)
            if value.get(key) != str(expected):
                raise self._fail("ownership transaction path mismatch")
        self._authority_record = value
        return value

    def _write(self, value):
        descriptor, temporary = tempfile.mkstemp(
            dir=_io_path(self.records, reserved_length=64), prefix=".link-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, sort_keys=True); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, _io_path(self.marker))
            self._authority_record = value
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _matches(path, target):
        state = inspect_project_link(path)
        return state.kind in {"junction", "symlink"} and state.target == target

    def _unlink(self, path, target):
        state = inspect_project_link(path)
        if state.kind == "absent":
            return
        if not self._matches(path, target):
            raise self._fail("refusing to remove an alias whose type or target changed", owned=True)
        # Do NOT resolve here. rmdir/unlink removes only the reparse object.
        if state.kind == "junction":
            _io_path(path).rmdir()
        else:
            _io_path(path).unlink()

    def _reconcile(self, record):
        if record["phase"] != "PREPARING":
            return record
        temporary, backup = Path(record["temporary"]), Path(record["backup"])
        target, old = record["target"], record.get("previous_target")
        state = inspect_project_link(self.path)
        if self._matches(self.path, target):
            pass
        elif state.kind == "absent":
            if not self._matches(temporary, target):
                if inspect_project_link(temporary).kind != "absent":
                    raise self._fail("unknown replacement artifact", owned=True)
                _create_link(temporary, Path(target))
            os.replace(temporary, self.path)
        elif old and self._matches(self.path, old):
            if inspect_project_link(backup).kind != "absent":
                raise self._fail("unexpected stale-link backup collision", owned=True)
            if not self._matches(temporary, target):
                if inspect_project_link(temporary).kind != "absent":
                    raise self._fail("unknown replacement artifact", owned=True)
                _create_link(temporary, Path(target))
            os.replace(self.path, backup)
            os.replace(temporary, self.path)
        else:
            raise self._fail("canonical path changed during ownership transaction", owned=True)
        if not self._matches(self.path, target):
            raise self._fail("replacement did not validate", owned=True)
        if old:
            self._unlink(backup, old)
        self._unlink(temporary, target)
        record = {**record, "phase": "COMMITTED", "previous_target": None}
        self._write(record)
        return record

    def ensure(self):
        try:
            return self._ensure()
        except ClothNextError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise self._fail(str(exc), owned=bool(self._authority_record), exception=exc) from exc

    def _ensure(self):
        with self._lock():
            try:
                record = self._read()
                if record:
                    record = self._reconcile(record)
                state = inspect_project_link(self.path)
                if record and record["phase"] == "COMMITTED":
                    if not self._matches(self.path, record["target"]) and state.kind != "absent":
                        raise self._fail("existing alias does not match its ownership record", owned=True)
                    if record["target"] == str(self.target) and self._matches(self.path, str(self.target)):
                        _io_path(self.target).mkdir(parents=True, exist_ok=True)
                        return state
                elif state.kind != "absent":
                    raise self._fail("existing filesystem object has no active CNX ownership record")
                if not record and os.path.lexists(_io_path(self.target)):
                    raise self._fail("existing target has no CNX link ownership record")
                _io_path(self.target).mkdir(parents=True, exist_ok=True)
                _io_path(self.path.parent).mkdir(parents=True, exist_ok=True)
                nonce = uuid.uuid4().hex
                previous = record["target"] if record and state.kind != "absent" else None
                value = {"version": 1, "project": self.name, "canonical": str(self.path),
                         "root": str(self.root), "target": str(self.target), "nonce": nonce,
                         "phase": "PREPARING", "previous_target": previous,
                         "temporary": str(self.path.parent / (".cnx-link-"+nonce+".temporary")),
                         "backup": str(self.path.parent / (".cnx-link-"+nonce+".backup"))}
                self._write(value)
                self._reconcile(value)
                return inspect_project_link(self.path)
            except ClothNextError:
                raise
            except (OSError, subprocess.SubprocessError) as exc:
                raise self._fail(str(exc), owned=bool(self._authority_record), exception=exc) from exc

    def remove(self):
        try:
            self._remove()
        except ClothNextError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise self._fail(str(exc), owned=bool(self._authority_record), exception=exc) from exc

    def _remove(self):
        with self._lock():
            record = self._read()
            if not record:
                if inspect_project_link(self.path).kind == "absent":
                    return
                raise self._fail("refusing alias removal without ownership metadata")
            if record["phase"] == "REMOVED":
                if inspect_project_link(self.path).kind != "absent":
                    raise self._fail("unknown artifact appeared after completed cleanup")
                return
            if record["phase"] == "PREPARING":
                # Abort a partial transaction without ever creating a link
                # during cleanup. Both possible canonical targets are owned.
                state = inspect_project_link(self.path)
                targets = {record["target"], record.get("previous_target")}
                if state.kind != "absent":
                    if state.target not in targets:
                        raise self._fail("unknown canonical artifact during transaction cleanup")
                    self._unlink(self.path, state.target)
                self._unlink(Path(record["temporary"]), record["target"])
                backup = Path(record["backup"])
                if inspect_project_link(backup).kind != "absent":
                    if not record.get("previous_target"):
                        raise self._fail("unknown backup during transaction cleanup")
                    self._unlink(backup, record["previous_target"])
            else:
                self._unlink(self.path, record["target"])
            self._write({**record, "phase": "REMOVED"})

    def validate_target_ownership(self):
        """Alias absence alone never authorizes deletion of an existing target."""
        with self._lock():
            record = self._read()
            if not record or record["target"] != str(self.target):
                raise self._fail("no matching ownership record for project target")


def ensure_project_link(canonical, target, *, owned_root, project_name):
    return ProjectLink(canonical, target, owned_root=owned_root, project_name=project_name).ensure()


def canonical_project_path(executable: Path, project_name: str, *, environment=None) -> Path:
    """Official normal data-directory ABI, before the first project query.

    Preparing the alias first avoids adopting an ordinary directory that a
    status query would otherwise create. The server-reported path is checked
    against this prediction after the handshake; no debug root override is used.
    """
    repository = executable.parent.parent.parent
    stamp = repository / ".git" / "branch_name.txt"
    branch = stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else ""
    if not branch and os.path.lexists(repository/'.git'):
        try:
            git=shutil.which('git',path=environment.get('PATH','')) if environment is not None else 'git'
            if git is None:raise FileNotFoundError('git is absent from solver environment')
            result = subprocess.run([git, "-C", str(repository), "branch", "--show-current"],
                                    capture_output=True, text=True, timeout=10,env=environment)
            branch = result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            branch = ""
    branch = branch or "unknown"
    if (branch.startswith(("/", "\\")) or ".." in branch.replace("\\", "/").split("/")
            or ":" in branch):
        raise ValueError("unsafe official data-directory branch identity")
    base = (repository / "local" / "share" / "ppf-cts" if os.name == "nt"
            else Path(os.environ.get("HOME") or Path.home()) / ".local" / "share" / "ppf-cts")
    return (base / ("git-" + branch) / project_name).absolute()


def remove_link_for_owned_target(owned_root: Path, target: Path, project_name: str,
                                *, required: bool = False) -> None:
    """Recovery cleanup uses the same authority, not its own alias deletion.

    The caller must first authenticate the target with owned_project_root.
    The durable alias record then proves this separate link resource's identity.
    """
    marker = _resolved_path(owned_root) / ".project-links" / (project_name + ".json")
    try:
        value = json.loads(_io_path(marker).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("canonical"), str):
            raise ValueError("invalid alias ownership record")
        link = ProjectLink(Path(value["canonical"]), _resolved_path(target),
                           owned_root=owned_root, project_name=project_name)
        record = link._read()
        if record["target"] != str(_resolved_path(target)):
            raise ValueError("alias record belongs to a different recovery target")
        link.remove()
    except FileNotFoundError as exc:
        if not required:
            return
        raise ClothNextError(ErrorRecord.create(
            category=ErrorCategory.CACHE,
            user_message="The saved project link ownership record is missing.",
            technical_message=f"project={project_name}; marker={marker}; target={target}",
            recommended_action="Keep the recovery files and inspect the missing ownership record.",
            recoverable=True, exception=exc)) from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise ClothNextError(ErrorRecord.create(
            category=ErrorCategory.CACHE,
            user_message="The saved project link ownership could not be verified.",
            technical_message=f"project={project_name}; marker={marker}; target={target}; error={exc}",
            recommended_action="Keep the recovery files and inspect the ownership record.",
            recoverable=True, exception=exc)) from exc
