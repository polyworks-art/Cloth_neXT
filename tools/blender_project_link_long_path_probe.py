# SPDX-License-Identifier: GPL-3.0-or-later
"""Native Blender long-path filesystem check and CNX ownership regression."""
from pathlib import Path
import os
import sys
import tempfile

repo = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
sys.path.insert(0, str(repo))
from cloth_next.ppf.project_links import ProjectLink, _io_path, inspect_project_link

root = Path(tempfile.mkdtemp(prefix="cnx-link-long-path-"))
# A short parent can still produce a long mkstemp child. Verify that boundary,
# not just the easier case where the parent itself already needs a prefix.
boundary = root / ("m" * (245 - len(str(root)) - 1))
boundary.mkdir()
descriptor, allocated = tempfile.mkstemp(dir=_io_path(boundary, reserved_length=64),
                                        prefix=".ownership-", suffix=".tmp")
os.close(descriptor); os.unlink(allocated)
print("BLENDER_TEMPFILE_LONG_CHILD_BOUNDARY_PASS")
owned = root / ("a" * 100) / ("b" * 100)
_io_path(owned).mkdir(parents=True)
probe = owned / "long-path-diagnostic.lock"
assert len(str(probe)) > 260
try:
    with probe.open("xb") as stream:
        stream.write(b"test")
    print("NORMAL_LONG_PATH_SUPPORTED")
    probe.unlink()
except OSError as exc:
    print("NORMAL_LONG_PATH_FAILURE", repr(exc), "length", len(str(probe)))
with _io_path(probe).open("xb") as stream:
    stream.write(b"test")
_io_path(probe).unlink()
link = ProjectLink(root / "aliases" / "longpath", owned / "longpath",
                   owned_root=owned, project_name="longpath")
try:
    link.ensure(); link.ensure(); link.remove(); link.remove()
except Exception:
    print("LINK_DIAGNOSTIC", str(link.path), str(link.target), inspect_project_link(link.path))
    raise
link.validate_target_ownership()
print("BLENDER_PROJECT_LINK_LONG_PATH_PASS")
from cloth_next.bake.pc2 import StreamingPc2Writer, read_header, partial_frame_count, Pc2Header
from cloth_next.core.safe_delete import delete_owned
final = owned / "longpath" / "result.pc2"
partial = owned / "longpath" / "partial.pc2"
writer = StreamingPc2Writer(final, vertex_count=1, frame_count=2, resume_path=partial)
writer.write_frame(((0, 0, 0),)); writer.preserve()
assert partial_frame_count(partial, Pc2Header(1, 0.0, 1.0, 2)) == 1
writer = StreamingPc2Writer(final, vertex_count=1, frame_count=2, resume_path=partial)
writer.write_frame(((0, 0, 1),)); writer.finalize()
assert read_header(final).frame_count == 2
outcome = delete_owned(final.parent, root=owned, ownership_authenticated=True,
                       recursive=True, lifecycle_stage="LONG_PATH_PROBE", artifact_type="fixture")
assert outcome.success and not _io_path(final.parent).exists()
print("BLENDER_PC2_LONG_PATH_RESUME_FINALIZE_CLEANUP_PASS")
