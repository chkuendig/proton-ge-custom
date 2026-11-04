# ZLUDA Proton packaging

Complete tool construction and qualification for explicitly selected Proton bases.
Select `ZLUDA_PACKAGING_PROFILE=ge-11-7`, check out the pinned `proton-source`
submodule and its admitted components, then use `.github/workflows/zluda-overlay.yml`.
`SOURCE_ROOT` names Proton; recipes live in this repository. CI uses the pinned SDK,
three build jobs and explicit base admission.

The removable migration/d32 directory contains the d32 release proof and original
source mappings. It is excluded from shipped recipe capsules. Moved code
retains its existing license and author provenance; Wine reviewer contracts have
separate terms under packaging/zluda-overlay/reviewer/wine/. The root license
files are retained from Proton, not a relicensing of permissive component code.

Local full-source builds use `make/full-source.sh` after upstream configuration.
The standalone product entry point remains the overlay workflow, which supplies
the native/ROCm payload as well as the complete bridge and stock tool.
