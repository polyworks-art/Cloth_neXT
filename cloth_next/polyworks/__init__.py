# SPDX-License-Identifier: GPL-3.0-or-later
"""PolyWorks account and licence integration (frozen add-on contract v1).

Only the Companion executable talks to the PolyWorks API (``api``/``service``).
Blender imports the pure readers (``entitlements``, ``state``, ``status``) and
never imports ``api`` or ``service``. Nothing here is used by, or gates, a bake.
"""
