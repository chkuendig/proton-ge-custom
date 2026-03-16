# PhysX GPU Games — Compatibility Tracker

Games with GPU-accelerated PhysX effects, tracked for ZLUDA (CUDA→HIP) compatibility on AMD GPUs.

## ZLUDA Compatibility Status Legend

| Status | Meaning |
|--------|---------|
| Untested | Not yet tested with ZLUDA |
| Menu OK | PhysX GPU options visible/selectable |
| Partial | Some effects work, others crash |
| Working | PhysX GPU effects render correctly |
| Blocked | Known blocker, not yet fixable |

---

## 64-bit Games (PhysX 3.x / APEX)

These are NOT affected by NVIDIA's RTX 50 32-bit CUDA deprecation. Primary targets for ZLUDA since they use the same APEX modules.

| Game | Steam ID | PhysX SDK | APEX Modules | GPU Effects | ZLUDA Status | Notes |
|------|----------|-----------|--------------|-------------|-------------|-------|
| Batman: Arkham Knight | 208650 | 3.x | TurbulenceFS, Particles, Clothing, Destruction | Interactive fog, paper debris, cloth cape, destruction | Menu OK | Crashes in APEX_TurbulenceFS — likely cuGraphicsMapResources (D3D-CUDA interop) not implemented |
| Killing Floor 2 | 232090 | 3.x | PhysX Flex | Blood/bile fluid sim, gibs, particle interaction | Untested | Uses Flex (unified particle solver), different from APEX |
| PlanetSide 2 | 218230 | 3.x | APEX Particles | Particles, explosions, debris | Untested | F2P, easy to test |

### 64-bit — PhysX present but limited/removed

| Game | Steam ID | Notes |
|------|----------|-------|
| The Witcher 3 | 292030 | Has APEX_ClothingGPU_x64.dll but CDPR forced all APEX to CPU; only HairWorks uses GPU (not PhysX CUDA) |
| Warframe | 230410 | Removed PhysX GPU particles in May 2018 (Update 22.20.0), replaced with vendor-neutral GPU particles |

---

## 32-bit Games (PhysX 2.x / 3.x)

These were affected by NVIDIA's RTX 50 series dropping 32-bit CUDA support. NVIDIA backported support for 9 of these in driver 591.44 (Dec 2025). Batman: Arkham Asylum planned for "early 2026."

### NVIDIA 591.44 Backported Titles (marked with *)

| Game | Steam ID | PhysX SDK | GPU Effects | ZLUDA Status | Notes |
|------|----------|-----------|-------------|-------------|-------|
| *Alice: Madness Returns | 19680 | 2.x | SPH fluid sim (10K+ particles), cloth, particles | Untested | |
| *Assassin's Creed IV: Black Flag | 242050 | 3.x | Volumetric smoke, fire particles, ocean spray | Untested | Uses APEX Particles + Turbulence |
| *Batman: Arkham City | 200260 | 2.x | Volumetric smoke/fog (SPH), paper debris, cloth | Untested | |
| *Batman: Arkham Origins | 209000 | 2.8.4 | Turbulence smoke/fog/steam, dynamic snow, paper debris | Untested | APEX Turbulence 1.3 + Particles 1.3 |
| *Borderlands 2 | 49520 | 2.8.4 | Particles, fluid sim, cloth | Untested | |
| *Mafia II | 50130 | 2.x | Cloth (clothing), debris/destruction, SPH smoke/fog | Untested | APEX Destruction + Clothing + Particles |
| *Metro 2033 | 43110 | 2.8.x | Particles, debris, cloth (banners) | Untested | |
| *Metro: Last Light | 43160 | 2.8.3 | SPH smoke/fog, particles/debris, cloth | Untested | |
| *Mirror's Edge | 17410 | 2.x | Glass shattering, cloth, debris | Untested | |
| Batman: Arkham Asylum | 35140 | 2.x | Volumetric smoke/fog, paper debris, cloth, particles | Untested | NVIDIA planned backport "early 2026" |

### Other 32-bit PhysX GPU Titles

| Game | Steam ID | PhysX SDK | GPU Effects | ZLUDA Status | Notes |
|------|----------|-----------|-------------|-------------|-------|
| Borderlands: The Pre-Sequel | 261640 | 2.8.4 | Particles (up to 56K), fluid sim | Untested | |
| Cryostasis: Sleep of Reason | 7850 | 2.x | Particles, fluid, ice shattering | Untested | |
| Dark Void | 45710 | 2.x | Cloth, particles | Untested | |
| Darkest of Days | 27970 | 2.x | Particles | Untested | |
| Gas Guzzlers: Combat Carnage | — | 2.x | Particles, debris | Untested | |
| Hydrophobia: Prophecy | 92000 | 2.x | Fluid simulation | Untested | |
| Mars: War Logs | 232750 | 2.x | Particles | Untested | |
| Rise of the Triad (2013) | 217140 | 2.x | Particles | Untested | |
| Sacred 2: Fallen Angel | 225640 | 2.x | Cloth, particles | Untested | |
| Shattered Horizon | 18110 | 2.x | Particles, debris | Untested | Delisted from Steam |
| The Bureau: XCOM Declassified | 65930 | 2.x | Particles | Untested | |
| Tom Clancy's GRAW 2 | 13510 | 2.x | Particles, debris | Untested | |
| Unreal Tournament 3 | 13210 | 2.x | Cloth, particles, debris | Untested | |

### Redux / Remastered Versions

| Game | Steam ID | PhysX SDK | Notes |
|------|----------|-----------|-------|
| Metro 2033 Redux | 286690 | 3.x | Upgraded to PhysX 3.x with enhanced GPU effects; may be 64-bit |
| Metro: Last Light Redux | 287390 | 3.x | Upgraded to PhysX 3.x with enhanced GPU effects; may be 64-bit |

---

## Benchmarks

| Tool | Arch | PhysX SDK | Effects | ZLUDA Status |
|------|------|-----------|---------|-------------|
| FluidMark (oZone3D) | 32-bit | 2.8.4 | SPH fluid sim (up to 60K particles) | Untested |

---

## Priority Order for Testing

1. **Batman: Arkham Knight** (64-bit, 3.x) — already in progress, Menu OK
2. **Killing Floor 2** (64-bit, 3.x Flex) — different tech path, good coverage
3. **PlanetSide 2** (64-bit, 3.x) — F2P, no purchase needed
4. **Borderlands 2** (32-bit, 2.8.4) — popular, well-documented PhysX effects
5. **Mafia II** (32-bit, 2.x) — dramatic GPU PhysX effects (clothing, destruction)
6. **Metro: Last Light Redux** (possibly 64-bit, 3.x) — upgraded engine
7. **Batman: Arkham City** (32-bit, 2.x) — same franchise, compare behavior

## Technical Notes

- **PhysX 2.x** games use raw CUDA particle/fluid/cloth APIs — no APEX framework
- **PhysX 3.x + APEX** games use the APEX module system (TurbulenceFS, Particles, Clothing, Destruction)
- **PhysX Flex** (Killing Floor 2) is a unified particle solver — different from APEX, may need separate ZLUDA work
- **D3D-CUDA interop** (`cuGraphicsMapResources`, `cuGraphicsSubResourceGetMappedArray`) is used by APEX TurbulenceFS for sharing textures between D3D11 and CUDA. This is NOT implemented in ZLUDA and is the likely crash cause for Batman: AK
- **32-bit games** need 32-bit ZLUDA (libnvcuda.so for i386) — not yet built
- **PhysX 5.x** is open source but no known games use GPU-accelerated PhysX 5 via CUDA
