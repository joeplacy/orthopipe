# De-identified historical order template

Copy this directory, replace `EXAMPLE-001` with a non-identifying ID, and add exactly one scan file
named `scan.obj`, `scan.stl`, or `scan.ply` plus the fabricator-approved `reference.stl`.

Do not commit real order directories. Before transfer, follow
[`docs/data-handling.md`](../../docs/data-handling.md), including checking mesh metadata, filenames,
free text, and embossed geometry.

Run one order with:

```bash
python replay.py --orders /approved/path/to/EXAMPLE-001 --out outputs/replay
```

`order.json` must specify the side. `rx.json` may be a bare `FootRx` as shown here or a complete
`Prescription`. Create separate directories for left and right when each has its own scan/reference.
