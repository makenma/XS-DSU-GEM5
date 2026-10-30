# Native L2 warm checkpoint acceptance

`validation.json` binds the final build to source hashes and summarizes the
checks. `directed.json` contains real guest save/continue/restore results for
dirty/coherent memory, L1-hit LR/SC, L2-miss LR/SC with a live guard, and
Sv39/PMP/instret. Each pair finishes at the same absolute tick.
`rnf-regression.json` records the original RNF native/legacy LR/SC checks.

`linux-save-and-restore.json` records cold Linux boot, warm hierarchy audit,
save, restore, root login and unchanged source snapshot hashes. Its binary
SHA-256 is
`456013647d93cf230eb7e919ee91140cfea027d8be282ff74cee35684eaf9d13`.
After include-order/spacing cleanup, the final rebuild SHA-256 is
`ea8717f3c22f1907a2b0806f80b736ea252d738859208bb5dc8449f25e2dfae6`.
The final binary passed all directed/RNF checks and independently restored
the same Linux snapshot; `linux-final-restore.json` records actual root shell
execution and unchanged snapshot files. Both restorations start at the saved
tick `3256000430000` and finish at `3551000430000`.

Console logs remove CR characters and trailing whitespace for readable diffs.
Their original raw
SHA-256 values are retained in `raw-console-sha256.json`. Firmware, backing
memory, DTB and checkpoints remain in ignored `m5out` directories. The
configuration/driver commands and absolute artifact paths are in the reports.

The final build and repository style hook both passed. Local build details
are retained in `m5out/native-l2-checkpoint/build-final-checked.log`; its hash
is in `validation.json`. No commit hook bypass is needed for this change.
