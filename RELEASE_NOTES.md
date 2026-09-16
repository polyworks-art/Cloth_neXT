# Cloth NeXt 2.5.1 Dev

This repair delivers the corrected update-channel rules as a Dev release. The
shipped 2.4.10 Dev updater rejects the Beta 2.5.0 target, even when that target
is offered through the Dev feed. It accepts 2.5.1 through the same Dev feed,
without changing the selected channel or repository identity.

After installing 2.5.1, Dev accepts Stable, Beta, and Dev releases; Beta accepts
Stable and Beta; Stable accepts only Stable. Update installation continues
through Blender's native Extension Manager.

The username preference, first-run prompt, installation identity storage, and
periodic installation heartbeats remain removed. The external PPF Contact Solver
remains separate and is not modified or bundled.

The candidate Dev index is checked using the actual shipped 2.4.10 updater before
publication. Testing new updater code with an old installed version number does
not establish bootstrap compatibility.
