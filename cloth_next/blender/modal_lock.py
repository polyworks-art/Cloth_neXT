# SPDX-License-Identifier: GPL-3.0-or-later
"""Single idempotent modal-lock token owned by a ready production Bake."""

_job_id = ""


def acquire(job_id: str, *, companion_ready_job_id: str) -> bool:
    global _job_id
    if not job_id or companion_ready_job_id != job_id:
        return False
    if _job_id and _job_id != job_id:
        return False
    _job_id = job_id
    return True


def release(job_id: str | None = None) -> None:
    global _job_id
    if job_id is None or not _job_id or job_id == _job_id:
        _job_id = ""


def active(job_id: str | None = None) -> bool:
    return bool(_job_id) and (job_id is None or job_id == _job_id)
