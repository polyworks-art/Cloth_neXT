# Known PDRD mixed-scene limitation

When an enabled PDRD Rigid Body is present, the external solver routes the
complete mixed scene through its reduced rigid solve. Until upstream provides a
hybrid PDRD/Schwarz path, Cloth, Rod and Soft Body vertices therefore use the
less effective block-Jacobi preconditioner in that solve.

Cloth NeXt 2.1.3 mitigates this by switching the existing Low, Medium, High and
Extreme buttons to PDRD-safe Time Step, Newton and PCG values whenever the scene
contains an enabled Rigid Body. Adding the first PDRD object or removing the last
one automatically remaps a recognized preset while deliberate Custom values are
left unchanged.

The upstream solver developer has been informed. This is a convergence
mitigation, not a replacement for the upstream hybrid-preconditioner fix.
