# Cloth NeXt 2.3.3 Dev

Cloth NeXt 2.3.3 fixes the recurring CNX-E140 failure where the solver process
remained alive but its control connection stopped responding. This is a Dev
release for validation before the next Beta.

## Solver transport reliability

- Owned local PPF servers now write stdout and stderr to unique real log files
  instead of Python-owned Windows pipes. This removes a documented source of
  Tokio worker backpressure while preserving bounded live activity and contact
  diagnostics.
- Log tails use robust UTF-8 replacement, bounded line and memory limits, and
  safe Cloth NeXt-owned cleanup after the complete process tree is reaped.
- Connect timeouts, read timeouts, refused connections, resets, malformed
  responses, and other transport phases are reported separately.

## Bounded reconnect behavior

- A transient status interruption during build or simulation enters a bounded
  reconnect state with three retries and increasing delays.
- Recovery continues the same project, process, and simulation. Cloth NeXt does
  not resend `start`, duplicate frame imports, restart the solver, or discard an
  active project because of a single failed status request.
- Cancellation interrupts reconnect backoff immediately. A known process exit
  and malformed server response remain fatal without misleading retries.
- Persistent loss still reports CNX-E140 with request counts, latency, failure
  phase, last-valid-status age, process/job PIDs, and bounded server-log tails.

## Included validation

- The normal repository suite passes 1,647 tests, with external prerequisites
  skipped honestly and built-artifact tests reserved for the publication job.
- Focused tests cover one and three transient failures, persistent loss,
  exactly-once simulation start, cancellation, process exit classification,
  malformed transport data, heavy output bursts, invalid UTF-8, handles, files,
  sockets, and thread cleanup.
- Real official PPF 0.18/schema 2 health, ownership, single-object, and
  multi-object simulations pass. A measured run completed 75 status requests
  with zero failures and 141 ms maximum observed latency.

The external PPF Contact Solver is unchanged, remains a separate installation,
and is not bundled with Cloth NeXt.
