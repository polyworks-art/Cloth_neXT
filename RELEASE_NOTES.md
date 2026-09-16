# Cloth NeXt 2.5.0 Beta

Cloth NeXt 2.5.0 removes the Superhive Username preference and first-run username
prompt introduced in 2.4.10. Installation identity storage, its background
worker, and periodic installation-presence requests have also been removed.

The add-on no longer reads or writes the previous username or Installation ID
files and makes no requests to the installation heartbeat endpoint. There is
no account prompt, purchase matching, or client-side license enforcement.

This Beta retains the existing simulation, Live View, negative Shrink, and
toolbar improvements. Update-channel handling and explicit external-solver
downloads continue through the existing workflows.

Update feeds now follow cumulative visibility: Dev receives all release levels,
Beta receives Stable and Beta, and Stable receives only Stable. This Beta is
offered through both Beta and Dev without requiring Dev users to switch feeds.

The external PPF Contact Solver remains separate and is not modified or bundled.
