# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable public Bake error codes and deterministic cause classification.

The companion exposes only these codes.  Classification deliberately uses
bounded, local text and typed error categories; it never sends diagnostics or
scene data anywhere.  Broad ``x00`` codes remain compatibility fallbacks while
the more specific codes identify a useful first recovery action.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .errors import ErrorCategory, ErrorRecord


@dataclass(frozen=True, slots=True)
class ErrorCodeInfo:
    code: str
    stage: str
    cause: str
    action: str


_ROWS = (
    ("CNX-E100", "Scene validation", "Unclassified scene validation failure", "Correct the highlighted scene setting and retry."),
    ("CNX-E101", "Scene validation", "No enabled deformable object", "Enable Cloth NeXt on at least one supported Mesh or Curve."),
    ("CNX-E102", "Scene validation", "Invalid or inconsistent Bake range", "Use a valid common Bake range on all deformables."),
    ("CNX-E103", "Scene validation", "Unsupported or changing topology", "Apply topology-changing modifiers or keep evaluated topology constant."),
    ("CNX-E104", "Scene validation", "Invalid material or solver-quality values", "Correct the highlighted material or quality value."),
    ("CNX-E105", "Scene validation", "Invalid Pin group or Pin animation", "Check the Pin vertex group, mode, and animated target topology."),
    ("CNX-E106", "Scene validation", "Incompatible multi-object settings", "Give all deformables a compatible range and supported configuration."),
    ("CNX-E107", "Scene validation", "Object disappeared during preparation", "Restore the named object and do not delete it while Bake is starting."),
    ("CNX-E108", "Scene validation", "Invalid Force configuration", "Use a supported Force on an Empty and verify its animated values."),
    ("CNX-E109", "Scene validation", "Non-finite or malformed geometry", "Repair NaN/Inf coordinates, degenerate geometry, or invalid transforms."),
    ("CNX-E110", "Companion startup", "Unclassified Companion startup failure", "Close stale Bake windows and retry."),
    ("CNX-E111", "Companion startup", "Companion bundle missing or failed integrity validation", "Repair or reinstall the Cloth NeXt extension."),
    ("CNX-E112", "Companion startup", "Local Companion transport could not start", "Close stale Bake windows and retry; check local security software."),
    ("CNX-E113", "Companion startup", "Companion process launch failed", "Check Windows execution permissions, then repair or reinstall the extension."),
    ("CNX-E114", "Companion startup", "Companion readiness handshake timed out", "Close the existing Bake window and retry."),
    ("CNX-E115", "Companion startup", "Companion window was not visible or topmost", "Restore normal desktop/window access and retry."),
    ("CNX-E116", "Companion startup", "Companion authentication or protocol mismatch", "Close all Bake windows and reinstall the matching extension build."),
    ("CNX-E120", "Bake preparation", "Unclassified preparation or export failure", "Check evaluated geometry and the cache folder, then retry."),
    ("CNX-E121", "Bake preparation", "Cache/work directory is not writable", "Choose a writable cache location and check free disk space."),
    ("CNX-E122", "Bake preparation", "Evaluated geometry export failed", "Check modifiers, transforms, and evaluated object geometry."),
    ("CNX-E123", "Bake preparation", "PPF scene or parameter encoding failed", "Check geometry, materials, Pins, Forces, and finite numeric values."),
    ("CNX-E124", "Bake preparation", "Animated Pin target capture failed", "Keep pinned topology and objects unchanged throughout the Bake range."),
    ("CNX-E125", "Bake preparation", "Insufficient disk space", "Free disk space or move the cache to a larger writable volume."),
    ("CNX-E126", "Bake preparation", "Bake worker could not start", "Retry after other heavy jobs finish; restart Blender if thread creation keeps failing."),
    ("CNX-E127", "Bake preparation", "Interrupted or stale partial Bake state", "Clear the partial result or Rebake; the last complete cache remains safe."),
    ("CNX-E130", "Solver startup", "Unclassified solver startup failure", "Run the solver health check and repair the managed solver."),
    ("CNX-E131", "Solver startup", "Solver executable or runtime dependency missing", "Repair the managed solver installation."),
    ("CNX-E132", "Solver startup", "Solver protocol, schema, or package is incompatible", "Install the solver version required by this Cloth NeXt release."),
    ("CNX-E133", "Solver startup", "Solver health check failed", "Repair the managed solver and inspect its startup diagnostics."),
    ("CNX-E134", "Solver startup", "Solver process exited during startup", "Inspect the solver stderr tail; update GPU drivers or repair dependencies."),
    ("CNX-E135", "Solver startup", "Solver execution permission denied", "Allow the signed solver executable and retry."),
    ("CNX-E140", "Scene upload", "Unclassified scene upload failure", "Check the local solver connection and retry."),
    ("CNX-E141", "Scene upload", "Solver connection or response timed out", "Retry and check whether security software blocks localhost traffic."),
    ("CNX-E142", "Scene upload", "Solver connection closed or broke", "Retry after the solver health check succeeds."),
    ("CNX-E143", "Scene upload", "Solver rejected or did not acknowledge upload", "Inspect the diagnostic log and verify the matching solver version."),
    ("CNX-E144", "Scene upload", "Uploaded payload hash or identity mismatch", "Repair the solver installation and retry with a fresh Bake."),
    ("CNX-E145", "Scene upload", "Malformed or oversized solver response", "Repair or update the solver to the supported protocol version."),
    ("CNX-E150", "Project build", "Unclassified solver project build failure", "Inspect scene geometry and the solver diagnostic log."),
    ("CNX-E151", "Project build", "Solver rejected project build", "Inspect geometry, materials, Pins, and Forces in the diagnostic log."),
    ("CNX-E152", "Project build", "Project build timed out", "Simplify the scene or increase stability/performance headroom, then retry."),
    ("CNX-E153", "Project build", "Contact or geometry initialization failed", "Repair intersections, degenerate faces, and invalid collision geometry."),
    ("CNX-E154", "Project build", "Solver project was unexpectedly busy", "Wait for the owned solver to stop, then retry."),
    ("CNX-E160", "Simulation", "Unclassified simulation failure", "Inspect the failing frame and solver diagnostic log."),
    ("CNX-E161", "Simulation", "Constraint solver did not converge", "Lower Friction first. If it still fails, reduce Pressure and Collision Gap, increase animated Collider sampling, then try a smaller Time Step."),
    ("CNX-E162", "Simulation", "Intersection blocked the simulation from advancing", "Separate intersecting geometry. If it fails while inflating or self-colliding, lower Pressure, add clearance between layers, or raise solver quality with a smaller Time Step."),
    ("CNX-E163", "Simulation", "Simulation stalled or timed out", "Inspect the last frame, reduce scene complexity, and retry."),
    ("CNX-E164", "Simulation", "Solver process crashed or exited", "Inspect the solver stderr tail for the underlying cause (intersection, non-finite, or convergence) before checking GPU/driver stability."),
    ("CNX-E165", "Simulation", "Non-finite simulation result", "Reduce Time Step and extreme Forces/stiffness; repair invalid input geometry."),
    ("CNX-E166", "Simulation", "RAM safety limit reached", "Lower scene complexity or raise the RAM Auto Cancel threshold cautiously."),
    ("CNX-E167", "Simulation", "Solver completed without every requested frame", "Keep the diagnostic log and retry after a solver health check."),
    ("CNX-E168", "Simulation", "Force or parameter instability", "Reduce animated Force magnitude and extreme material parameters."),
    ("CNX-E169", "Simulation", "Unexpected solver state", "Stop other solver work and retry with a fresh owned project."),
    ("CNX-E170", "Result transfer", "Unclassified result transfer failure", "Check disk space and the local solver connection."),
    ("CNX-E171", "Result transfer", "Result transfer timed out", "Check disk performance and localhost security software, then retry."),
    ("CNX-E172", "Result transfer", "Connection broke during result transfer", "Retry after a successful solver health check."),
    ("CNX-E173", "Result transfer", "Invalid or missing solver output map", "Keep the diagnostic log and repair/update the solver."),
    ("CNX-E174", "Result transfer", "Requested result frame is missing", "Retry; report the diagnostic log if the same frame is missing again."),
    ("CNX-E175", "Result transfer", "Result frame is corrupt or cannot be decoded", "Check disk integrity and repair/update the solver."),
    ("CNX-E176", "Result transfer", "Result size or vertex count mismatch", "Restore the original topology and Rebake."),
    ("CNX-E180", "Playback cache", "Unclassified playback-cache failure", "Check cache access and target topology."),
    ("CNX-E181", "Playback cache", "Playback cache could not be written", "Choose a writable cache folder with sufficient free space."),
    ("CNX-E182", "Playback cache", "PC2 finalization failed", "Check disk space and filesystem reliability, then Rebake."),
    ("CNX-E183", "Playback cache", "Object topology changed before import", "Restore the topology used when the Bake began and Rebake."),
    ("CNX-E184", "Playback cache", "Target object no longer exists", "Restore the target object and Rebake."),
    ("CNX-E185", "Playback cache", "Playback attachment failed", "Remove conflicting cache modifiers or animation and retry."),
    ("CNX-E186", "Playback cache", "Cache integrity validation failed", "Do not use the partial cache; Rebake to create a verified result."),
    ("CNX-E187", "Playback cache", "Multi-object cache set is incomplete or inconsistent", "Keep every target object unchanged and Rebake the full set."),
    ("CNX-E188", "Playback cache", "Rod Curve playback could not be applied", "Restore the original Curve topology and remove conflicting Curve animation."),
    ("CNX-E190", "Cleanup", "Unclassified cancellation cleanup failure", "Wait for cleanup; restart Blender only if the owned process remains stuck."),
    ("CNX-E191", "Cleanup", "Cancellation did not complete in time", "Wait briefly, then restart Blender if the owned solver remains active."),
    ("CNX-E192", "Cleanup", "Owned solver process could not be stopped", "Close Blender after preserving diagnostics, then retry."),
    ("CNX-E193", "Cleanup", "Temporary or partial files could not be removed", "Close programs using the cache folder and clear the partial result."),
    ("CNX-E198", "Internal", "Bake worker stopped without a terminal result", "Preserve diagnostics and restart Blender before retrying."),
    ("CNX-E199", "Internal", "Unexpected internal failure", "Preserve the diagnostic log and report the code and full Blender error."),
)

ERROR_CODES = {row[0]: ErrorCodeInfo(*row) for row in _ROWS}

STAGE_FALLBACKS = {
    "PREPARING": "CNX-E100", "STARTING_COMPANION": "CNX-E110",
    "WAITING_FOR_COMPANION": "CNX-E110", "COMPANION_READY": "CNX-E120",
    "STARTING_RUN": "CNX-E120", "EXPORTING": "CNX-E120",
    "STARTING_SOLVER": "CNX-E130", "UPLOADING": "CNX-E140",
    "BUILDING": "CNX-E150", "SIMULATING": "CNX-E160",
    "FETCHING": "CNX-E170", "IMPORTING": "CNX-E180",
    "CANCELLING": "CNX-E190",
}

# First match wins.  Patterns are intentionally concrete to avoid inventing a
# precise diagnosis from an unrelated exception containing a common word.
_RULES = tuple((re.compile(pattern, re.IGNORECASE), code) for pattern, code in (
    (r"worker (?:stopped|died).*without|no terminal message", "CNX-E198"),
    (r"ram .*?(?:limit|threshold)|auto.?cancel.*ram|memory safety", "CNX-E166"),
    (r"linear solver failed to converge|could not converge|non.?conver", "CNX-E161"),
    # Intersection failures at any frame, not only frame 0: the solver's mid-run
    # "Intersection detected: advance failed at frame N (... intersection_free=false)"
    # and CCD-failure wrappers must beat the generic crash/fallback below.
    (r"init(?:ialization)? intersection|initial intersection|intersection detected|"
     r"intersection.?free\s*=\s*false|continuous collision detection failed", "CNX-E162"),
    (r"\b(?:nan|infinity|non.?finite)\b.*(?:result|simulation|position)", "CNX-E165"),
    # Explosive parameter/force instability (e.g. extreme Pressure or Force):
    # the solver reports a numerical/BVH overflow rather than a clean NaN.
    (r"numerical overflow|overflow detected|bvh.*stack overflow|"
     r"parameter instability|unstable (?:force|parameter)", "CNX-E168"),
    (r"simulation stalled|no new frame within|simulation timed out", "CNX-E163"),
    (r"finished without producing every frame|without every requested frame", "CNX-E167"),
    (r"process (?:crashed|exited|terminated).*simulat|solver.*(?:crashed|exited)", "CNX-E164"),
    (r"build timed out|no READY status within", "CNX-E152"),
    (r"unexpectedly busy|status (?:BUSY|SAVE_AND_QUIT)", "CNX-E154"),
    (r"(?:contact|geometry) (?:initialization|build).*fail|degenerate|self.?intersection", "CNX-E153"),
    (r"hash mismatch|different payloads|identity mismatch", "CNX-E144"),
    (r"did not acknowledge the upload|did not confirm the upload|rejected the upload", "CNX-E143"),
    (r"response.*(?:malformed|invalid|too large)|invalid response schema", "CNX-E145"),
    (r"(?:connect|response|upload).*timed out|did not respond in time", "CNX-E141"),
    (r"connection.*(?:broke|closed|reset)|closed the connection", "CNX-E142"),
    (r"protocol|schema|package version.*incompat|version mismatch", "CNX-E132"),
    (r"health check.*fail|never became ready", "CNX-E133"),
    (r"solver executable.*(?:missing|not a file)|runtime dependency|dll.*not found", "CNX-E131"),
    (r"permission denied|access is denied", "CNX-E135"),
    (r"companion manifest|companion (?:hash|size|identity|version) mismatch|bundle.*missing", "CNX-E111"),
    (r"companion.*transport|bake window transport", "CNX-E112"),
    (r"companion.*(?:process|launch)|could not start.*window", "CNX-E113"),
    (r"handshake.*timeout|readiness.*timeout|did not become ready", "CNX-E114"),
    (r"not become visible or topmost|window.*not visible", "CNX-E115"),
    (r"invalid session token|authentication|companion protocol", "CNX-E116"),
    (r"no .*deformable|at least one.*(?:cloth|deformable)", "CNX-E101"),
    (r"bake range|frame (?:range|start|end)", "CNX-E102"),
    (r"multi.object(?! playback)|all deformables need the same", "CNX-E106"),
    (r"pin.*(?:topology|group|target|capture)|animated pin", "CNX-E105"),
    (r"force.*(?:invalid|unsupported|empty)|wind|gravity", "CNX-E108"),
    (r"object .*no longer exists|object disappeared", "CNX-E107"),
    (r"multi.object playback cache|missing cache for|cache set", "CNX-E187"),
    (r"rod .*playback|original curve|curve topology", "CNX-E188"),
    (r"topology.*changed|vertex count.*(?:changed|mismatch)", "CNX-E183"),
    (r"pc2.*(?:final|short .*write)|playback cache.*final", "CNX-E182"),
    (r"cache.*(?:hash|integrity|changed between write|damaged|corrupt|"
     r"metadata|missing/invalid fields)", "CNX-E186"),
    (r"cache.*(?:permission|writ)|short PC2.*write", "CNX-E181"),
    (r"output map.*(?:invalid|missing|no entry)|surface map", "CNX-E173"),
    (r"frame .*?(?:missing|not found|no result)", "CNX-E174"),
    (r"frame.*(?:corrupt|decode|decompress|invalid payload)", "CNX-E175"),
    (r"result.*(?:size|vertex count).*mismatch|expected_count", "CNX-E176"),
    (r"disk space|no space left|disk full", "CNX-E125"),
    (r"worker could not be started|thread.*start", "CNX-E126"),
    (r"partial (?:bake|cache)|stale.*(?:bake|cache)", "CNX-E127"),
    (r"scene.*encod|parameter.*encod|cbor", "CNX-E123"),
    (r"evaluated.*geometry|to_mesh|scene export", "CNX-E122"),
    (r"material|quality|time step|substep", "CNX-E104"),
    (r"non.?finite|singular matrix|malformed geometry", "CNX-E109"),
    (r"cancel.*timed out|cancellation.*timeout", "CNX-E191"),
    (r"process.*(?:could not|did not).*stop|reader thread did not stop", "CNX-E192"),
    (r"temporary.*(?:remove|delete)|partial files.*remove", "CNX-E193"),
))


def classify_error(stage: object, summary: str = "", details: str = "",
                   record: ErrorRecord | None = None) -> str:
    """Return one stable code without throwing, even for malformed errors."""
    stage_name = str(getattr(stage, "value", stage) or "").upper()
    pieces = [str(summary or "")[:4096], str(details or "")[:16384]]
    if record is not None:
        pieces.extend((record.user_message[:4096],
                       record.technical_message[:16384]))
    haystack = "\n".join(pieces)
    for pattern, code in _RULES:
        if pattern.search(haystack):
            return code
    if record is not None:
        category_fallback = {
            ErrorCategory.SCENE_VALIDATION: "CNX-E100",
            ErrorCategory.USER_INPUT: "CNX-E100",
            ErrorCategory.SOLVER_INSTALLATION: "CNX-E130",
            ErrorCategory.PROTOCOL_COMPATIBILITY: "CNX-E132",
            ErrorCategory.SOLVER_CONNECTION: "CNX-E140",
            ErrorCategory.SIMULATION: "CNX-E160",
            ErrorCategory.CACHE: "CNX-E180",
            ErrorCategory.INTERNAL: "CNX-E199",
        }.get(record.category)
        if category_fallback:
            return category_fallback
    return STAGE_FALLBACKS.get(stage_name, "CNX-E199")


def valid_error_code(value: str) -> bool:
    return value in ERROR_CODES
