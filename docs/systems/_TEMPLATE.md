---
subsystem: <name — lower-case, hyphens>
owns: <file globs this subsystem's code lives in, comma-separated>
anchors: <DISTINCTIVE regexes of this subsystem's code (function/table/JS names), comma-separated — they decide when a Claude must read this doc, so no generic words>
test: <real commands that prove it still works; say if it needs a copy/snapshot or the running server>
---
# <Title> — <one-line essence>

## What it does
## Where it lives
## CASCADE — a change here must reach ALL of these
## Rules (invariants)
## Reuse, don't rebuild
## Traps
## Related subsystems

LAST VERIFIED: <date> · <commit> · <what was verified>

<!-- Files starting with "_" are ignored by the hooks and blast_radius. Copy this to <name>.md.
     Budget ~10,000 chars: replace stale lines with the current truth; history goes in git. -->
