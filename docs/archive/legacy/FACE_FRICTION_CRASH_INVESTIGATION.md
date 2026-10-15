# Per-Face Friction native-crash investigation

Date: 2026-07-26

Solver under test:

```text
%LOCALAPPDATA%\ClothNeXt\solver\versions\
2026-07-13-21-05\target\release\ppf-cts-server.exe
```

## Result

No isolated Per-Face Friction mapping defect was reproduced. The observed
frame-zero native exits coincide with Malwarebytes removing the solver worker.
Malwarebytes recorded a successful quarantine of:

```text
ppf-contact-solver.exe
SHA-256 8A1B6146BC8623E3E3AD3CD0EAFDF0A1F5890AA6F338E046A4B6291C962B20EF
Detection Malware.Ransom.Agent.Generic
Detection time 2026-07-26T16:59:49Z
```

There was no Cloth NeXt or PPF entry in the Malwarebytes exclusion file.
The server executable remained available, so upload and build could complete;
the separately launched native worker then disappeared before writing frame 1.

## Isolated payload

The scene payload remained the PPF schema-v1 envelope:

```text
{
  version: 1,
  kind: "Scene",
  payload: [
    {
      type: "SHELL",
      object: [{
        name, uuid, vert, transform, face,
        face_friction  # present only for Per-Face Friction
      }]
    },
    {
      type: "STATIC",
      object: [{name, uuid, vert, transform, face}]
    }
  ]
}
```

For every Per-Face test the local decoder confirmed:

- `len(face_friction) == len(face) == 200`;
- every value was finite and within `[0, 1]`;
- triangle indices were valid and every triangle had non-zero area;
- the decoded face order and Friction order matched the encoded order.

## Controlled comparisons

Fixture: one 121-vertex/200-triangle Cloth, one static UV-sphere Collider,
no Forces, no animated Collider, and a short frame range.

Successful isolated runs included:

- global Friction `0.50`;
- global Friction `0.25`;
- constant Per-Face Friction `0.25`;
- constant Per-Face Friction `0.50`;
- two contiguous regions (`0.05` and `0.95`);
- alternating regions (`0.05` and `0.95`);
- three repeated global six-frame runs;
- three repeated constant-Per-Face six-frame runs;
- three repeated constant-Per-Face three-frame runs.

Every repeated comparison completed all requested frames. The non-constant
Per-Face integration case also completed all seven solver frames and reached
contact without a native exit.

Two frame-zero exits occurred during the first tightly sequenced matrix.
They had no Rust panic, no solver stderr, no terminal outcome, and no completed
frame. Subsequent byte-equivalent reruns succeeded. Malwarebytes logged process
inspection and communication failures at the same times. Because the exits
were not reliably coupled to any Friction payload, they are not evidence of a
Per-Face mapping or native Friction-kernel defect.

Peak worker memory and a native worker exit code were unavailable: the removed
worker wrote neither a terminal status nor stderr before Malwarebytes
intervention. The server reported the child as stopped after frame 0.

## Product handling

The Friction schema, mapping, ordering, values, and encoder remain unchanged.
Cloth NeXt now verifies that `ppf-contact-solver.exe` exists before starting
the server and checks again when an owned solver connection fails. A missing
worker is reported as a likely quarantine with instructions to restore or
reinstall the verified solver and allow its installation folder.

No damaged PC2 was published in any failed run. Session cleanup retained the
existing atomic PC2 publication rules and removed the failed temporary solver
project through the normal controlled cleanup path.
