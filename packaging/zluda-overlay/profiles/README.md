# Admitted base profiles

`ge-11-7` and `valve-11-0` select separate source gitlinks and locks. Each profile pins its off-mode stock archive and both CUDA stub hashes; candidate bytes are never the reference oracle.

Valve stock reference: run 41851039781, artifact 13350027288. The current GE native control is the published d32 product (run 41847768267); its hosted artifact expires November 18, 2026.

Historical CI-built A1 is run 40906607864 at `0fb8d56a`, artifact 12914079963; the hosted copy expires **November 12, 2026 at 16:48:30 UTC**. It is separate from the Deck-installed tool. The exact ZIP digest is in `inputs.valve-11-0.lock.json`.

For replay, set `A1_CONTROL_DIR` to a verified archive directory containing `API.json` and `control.zip` before running `historical-a1.sh`. The saved path and digests are recorded in the private evidence `THIN-STOCK-REFERENCE.json`. Archive/source/digest admission remains mandatory. Durable ELF64/PE32 upgrade fixtures also run independently of historical artifact retention.
