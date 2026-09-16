# Cloth NeXt 2.4.10 Dev

Cloth NeXt now includes a persistent Superhive Username field in Preferences
and a simple first-run prompt. Any reasonable non-empty username is accepted;
the add-on does not check accounts or purchases and shows no purchase status.

A best-effort background HTTPS heartbeat sends the configured username, random
Installation ID, installed version, and selected release channel approximately
every 120 seconds. The server observes the source IP through the connection.
Network failures are silent and do not block startup, Preferences, or simulation.

Identity persists across Blender restarts and add-on updates. The installed
runtime copy is excluded from distributed packages. No hardware fingerprint,
credentials, purchaser list, scene data, or unrelated analytics are included.
See docs/PRIVACY.md for the data collection description.

This is an experimental Dev build. The external PPF Contact Solver remains
separate and is not modified or bundled.
