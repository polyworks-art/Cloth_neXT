# Cloth NeXt 2.9.30

Installing GAIA from Preferences no longer fails on computers with long user or profile paths. The GAIA runtime contains deeply nested Python files; unpacking them below a long profile path could exceed the Windows path limit of 260 characters. Extraction, verification and installation now use extended-length paths on Windows.

GAIA 0.26.0-dev.20261019 is published for Dev testers (Windows, CUDA 12.8) and installs from Preferences with a PolyWorks account that has GAIA access. Everything from 2.9.29 is unchanged. Neither solver is bundled.
