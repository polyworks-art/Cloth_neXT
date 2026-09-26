# Cloth NeXt 2.8.5

Cloth NeXt 2.8.5 removes automatic full-scene validation from idle interaction. Assigning Cloth NeXt, changing a property, moving an object, or receiving a dependency-graph update now performs only a lightweight dirty-state update.

Topology hashing, pin membership scans, evaluated-mesh snapshots, and collider validation now run only when Validate, Bake, or Rebake explicitly needs an authoritative scene snapshot. This prevents periodic UI pauses on dense meshes while preserving strict validation before simulation.

Published to the configured private repository. The external PPF Contact Solver is not bundled.
