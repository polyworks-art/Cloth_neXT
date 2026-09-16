# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Minimal installation presence; deliberately independent of Blender and PPF."""
import json
from pathlib import Path
import threading
import uuid
import urllib.error
import urllib.request

ENDPOINT = "https://tinytrouble.de/cloth-next/api/heartbeat.php"
INTERVAL = 120.0
TIMEOUT = 4.0
MAX_USERNAME_LENGTH = 128


def normalize_username(value):
    value = value.strip()
    if not value or len(value) > MAX_USERNAME_LENGTH or any(ord(c) < 32 for c in value):
        raise ValueError("Enter a Superhive username of 1–128 characters.")
    return value


class IdentityStore:
    def __init__(self, path, replica=None):
        self.path = Path(path)
        self.replica = Path(replica) if replica is not None else None

    def read(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.replace(self.path)
        self.restore_replica(value)

    def restore_replica(self, value=None):
        """An optional installed copy; user config remains authoritative on updates."""
        if self.replica is None:
            return
        try:
            text = json.dumps(self.read() if value is None else value)
            if self.replica.is_file() and self.replica.read_text(encoding="utf-8") == text:
                return
            self.replica.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.replica.with_suffix(".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(self.replica)
        except OSError:
            pass  # A read-only installed extension remains fully functional.

    def username(self):
        try:
            return normalize_username(self.read().get("superhive_username", ""))
        except (ValueError, AttributeError):
            return ""

    def set_username(self, value):
        value = normalize_username(value)
        data = self.read()
        data["superhive_username"] = value
        self.write(data)

    def installation_id(self):
        data = self.read()
        try:
            identifier = uuid.UUID(data.get("install_id", ""))
            if identifier.version != 4:
                raise ValueError("Expected random UUID4")
        except (ValueError, AttributeError, TypeError):
            identifier = uuid.uuid4()
            data["install_id"] = str(identifier)
            self.write(data)
        return str(identifier)


def payload(username, install_id, version, channel):
    channel = channel.lower()
    if channel not in {"stable", "beta", "dev"}:
        raise ValueError("Unknown release channel")
    return dict(superhive_username=normalize_username(username),
                install_id=install_id, version=version, channel=channel)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def send_heartbeat(data):
    """Ignore all responses and failures; default TLS validation stays enabled."""
    try:
        request = urllib.request.Request(
            ENDPOINT, data=json.dumps(data).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=TIMEOUT):
            pass
    except urllib.error.HTTPError as exc:
        exc.close()
    except Exception:
        pass


class Service:
    """One bounded daemon request at a time; shutdown never waits for the network."""
    def __init__(self):
        self.worker = None
        self.active = False

    def submit(self, data):
        if not self.active or (self.worker is not None and self.worker.is_alive()):
            return
        self.worker = threading.Thread(target=send_heartbeat, args=(dict(data),),
                                       name="ClothNextPresence", daemon=True)
        self.worker.start()

    def stop(self):
        self.active = False
