# Installation presence

Cloth NeXt may send the configured Superhive username, a persistent random
UUID4 Installation ID, installed Cloth NeXt version, and selected release channel
to `https://tinytrouble.de/cloth-next/api/heartbeat.php`. The server sees the
source IP as part of the network connection; the client does not add it to the payload.
Requests run in the background shortly after initialization, after username
changes, and approximately every 120 seconds while the add-on is enabled.
Failures are silent and do not affect simulation or other functionality.

The username and random ID are stored in `cloth_next/presence.json` within
Blender's user configuration directory, outside project files and the installed
extension. A replaceable copy is kept at `resources/.state/r7.dat` within the
installed add-on. Local builds exclude this per-installation state. The external
configuration copy preserves the identity when add-on files are updated or
the installed copy is removed. Neither copy provides tamper-proof enforcement.
The ID is unrelated to hardware. No hardware fingerprint, credentials,
project or scene information, or usage analytics is sent by this presence system.
The Preferences field accepts a claimed username without checking Superhive.
Any purchaser comparison belongs exclusively to the owner's private server/admin
system. No purchaser information or purchase status is consumed by the client.
