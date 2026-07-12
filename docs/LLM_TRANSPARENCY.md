# Cloth NeXt – LLM Transparency

This document explains how large language models and AI-assisted development
tools are used in Cloth NeXt.

The purpose of this disclosure is to provide clear context about how the
codebase, documentation, tests, and project decisions are produced.

## Development model

Cloth NeXt is developed with extensive assistance from LLM-based coding and
writing tools, including tools such as OpenAI Codex and ChatGPT.

These tools are used for tasks including:

- implementation
- refactoring
- test generation
- debugging
- architecture review
- documentation
- release preparation
- codebase analysis
- Blender API research
- UI and workflow iteration

LLMs are treated as development tools, not as autonomous project maintainers.

The maintainer defines the project direction, feature scope, architecture,
workflow, user experience, acceptance criteria, release policy, and final
technical decisions.

## Codebase

A substantial portion of Cloth NeXt's implementation is produced or modified
with the assistance of coding agents working from detailed human-written
requirements.

Generated changes are reviewed and validated before publication through a
combination of:

- automated unit and integration tests
- Blender runtime smoke tests
- manual Blender testing
- release-policy validation
- extension structure validation
- artifact scanning
- solver compatibility checks
- direct inspection of security-sensitive and lifecycle-sensitive code

Special attention is given to areas involving:

- external process management
- downloads and installation
- file-system access
- network communication
- solver ownership
- Blender registration and reload behavior
- update handling
- release packaging
- compatibility and version validation

Lower-risk UI glue code and repetitive test scaffolding may rely more heavily on
automated validation than on line-by-line manual inspection.

The maintainer remains responsible for all code and design decisions published
as part of Cloth NeXt, regardless of whether the original keystrokes were
produced by a human or an AI-assisted tool.

## Documentation

The README and project documentation may be drafted, rewritten, proofread, or
restructured with the assistance of an LLM.

The maintainer provides the intended meaning, technical facts, tone, project
positioning, and required constraints, and reviews the resulting text before it
is published.

LLM-generated documentation is not treated as an independent technical source.
Claims about Blender, the PPF Contact Solver, licensing, compatibility, or
release behavior must be checked against the project implementation or an
appropriate primary source.

## Testing and verification

LLM-generated code is not considered correct merely because it appears
plausible or passes a limited test.

Cloth NeXt uses tests and explicit validation gates to reduce the risk of:

- fabricated APIs
- invalid Blender behavior
- unsafe process handling
- incorrect release metadata
- accidental solver redistribution
- stale registration callbacks
- version mismatches
- unverified update behavior

Passing tests does not remove the need for human judgment, particularly for
user-facing behavior, security boundaries, licensing, and release decisions.

## Project responsibility

LLMs do not own, maintain, release, or make final decisions for Cloth NeXt.

The maintainer remains responsible for:

- deciding what is built
- accepting or rejecting generated changes
- reviewing project behavior
- verifying releases
- maintaining licensing boundaries
- correcting errors
- responding to reported issues

The use of LLMs changes how parts of the project are implemented and written.
It does not change who is responsible for the resulting software.