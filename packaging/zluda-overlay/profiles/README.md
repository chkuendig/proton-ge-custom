# Admitted base profiles

`ge-11-7` and `valve-11-0` select separate source gitlinks and locks. Each profile pins its off-mode stock archive and both CUDA stub hashes; candidate bytes are never the reference oracle.

Valve stock reference: run 41851039781, artifact 13350027288. Published d32 (run 41847768267) remains the stock/builtin/seven-ROCm comparison; its hosted artifact expires November 18, 2026.

Valve uses final-fix backend `3289e30e`. Its libcuda is a separate native control, never published-GE bytes. `native_control_seed=true` builds the pinned backend and full native CPU harness without assembling a tool. `native_control` pins the verified native hash and artifact from seed run 42027535134; a separate full workflow must reproduce those bytes. Assembly refuses an unpinned or mismatched control. The retained native harness includes its SDK loader closure; only three CPU cases run here. The Deck-validated predecessor `617e7ecc` built native hash prefix `7e3d2b84` in run 41951177178; that evidence does not qualify final `3289e30e` or Valve.

Headless Valve launcher and cross-tool cases run the real `waitforexitandrun` setup, then capture the preloader's environment, cwd and stock Wine/steam.exe command. The fixture promotes the recorded target past steam.exe and replays it with the captured preloader, Wine loader, environment and cwd; every receipt records that substitution. Steam's client handshake remains pending Deck qualification. No Steam provider is downloaded.

The Valve `runinprefix` helper retains stock `nvcuda=disabled`: no override, stub root selected, backend isolated and tool payload unchanged. The pinned probe must fail its LoadLibrary check with exactly error 126. Fresh-prefix PhysX 9.23 (BL2/Mafia), d3dx9 and d3dcompiler_43 helper installs remain a Deck follow-up under prerequisite decision 3.

Historical CI-built A1 is run 40906607864 at `0fb8d56a`, artifact 12914079963; the hosted copy expires **November 12, 2026 at 16:48:30 UTC**. It is separate from the Deck-installed tool. The exact ZIP digest is in `inputs.valve-11-0.lock.json`.

For replay, set `A1_CONTROL_DIR` to a verified archive directory containing `API.json` and `control.zip` before running `historical-a1.sh`. The saved path and digests are recorded in the private evidence `THIN-STOCK-REFERENCE.json`. Archive/source/digest admission remains mandatory. Durable ELF64/PE32 upgrade fixtures also run independently of historical artifact retention.
