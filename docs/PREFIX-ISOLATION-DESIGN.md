# Shared-prefix builtin isolation prototype

Base: `bridge/w1-full-b-20261102` at `d013499f64f73f22e27ede7437a6fd511974c36a`.
This changes only Proton policy; Wine, bridge, backend and release bytes are not rebuilt.
No Deck access or publishing is part of this prototype.

## Mechanism and decision

GE's `pfx_copy` can make absolute builtin symlinks. Wine's existing
`dlls/setupapi/fakedll.c:create_dest_file` opens a recognized existing Wine
builtin/placeholder and truncates the reached inode before writing. Steam's
per-tool d3ddriverquery helper shares compatdata/0. Switching the updating tool
can therefore rewrite the previous supplier's installation. Hardlinks also
share that inode; a linked Windows directory defeats leaf-only replacement.
The incident and complete artifact comparison are recorded in
`evidence/w1-bridge-plan-20261030/refreeze-full-b-20261102/SHARED-PREFIX-BUILTIN-HAZARD.md`
and `evidence/zluda32-route-20261031/REPORT.md` section 10 in the host evidence tree.
Registry and tracked_files updates after a mapping switch are normal Proton
behavior and are outside this fix.

Use GE's existing `PROTON_DLL_COPY=*` mode by default, before constructing the
session. Honor it during initial template copying too, which previously had a
short-lived symlink stage. Copy the six ICU files (three names, both arches)
instead of linking them. Before prefix setup or any Wine entry point, privately
copy recognized foreign Wine-module aliases and ICU aliases in the launched
prefix's Windows tree. Replace only the prefix leaf, atomically after a successful
copy; drop a dangling recognized alias. Preserve native game DLL overrides.
Run the pass under the existing prefix lock. Refuse linked Windows directories
and linked pfx/drive_c/windows ancestors rather than writing through them.

The common main-path call also protects runinprefix and helper launches: those
paths can execute Wine while skipping ordinary prefix setup. There is no second
helper-specific hook. A source fixture checks that the call precedes both
bootstrap and session setup, regardless of verb.

| Prefix state and next writer | Old policy | Prototype default |
|---|---|---|
| Foreign builtin symlink, our normal/helper launch | Reaches foreign tool | Private copy before Wine |
| Foreign builtin hardlink, our launch | Reaches shared tool inode | Private inode before Wine |
| Foreign ICU alias, our launch | Reaches foreign ICU | Private copy, then ordinary ICU setup |
| Linked Windows parent, our launch | Leaf writes reach foreign tree | Refuse launch before Wine |
| Fresh prefix from our template, later unpatched tool | Alias can reach our tool | Copy from first template population |
| Our ICU setup, later unpatched tool | Alias reaches our ICU | Private prefix copies |
| Existing own-root symlink, runinprefix | Historical alias remains | Historical alias remains; legacy boundary below |
| Prefix never launched through us, unpatched tool updates | Can corrupt supplier | Outside this launcher's control |
| Stock GE prefix updated by another stock GE | Can corrupt supplier | Stock tools remain unpatched |

Narrow copies only for current GE byte differences were rejected: even equal
bytes today can diverge on another release, and a retained alias still allows
writes into an installation. A Wine unlink/create patch would fix our writer's
leaf handling but not another installed tool's writer or linked parents.
Combining it with this policy adds a Wine rebuild and patch maintenance without
removing that legacy boundary. The prototype therefore adds no Wine patch.

## Scope, costs and legacy boundary

Only recognized Wine builtins/placeholders (using GE's existing detector) and the
ICU trio are detached. The walk includes WinSxS and both Windows architecture
directories; it does not touch registry, tracked_files, fonts, drive mappings or
ordinary native DLL aliases. Directory symlinks inside the Windows tree are
refused conservatively, including custom layouts; they are not converted.
The tool root is resolved before ownership comparison. Regular hardlinks with
multiple names are detached because their other paths cannot be inferred.

Copy-all increases prefix storage and initial/update copying. GE's existing
copy helper can share extents where the filesystem supports copy_file_range;
private inodes, rather than physical extent allocation, are the requirement.
The pass walks Windows and stats its entries at each launch. It reads module
bytes only for aliases/hardlinks requiring classification/copying. This prototype
has no game-start performance claim.

An explicit non-* PROTON_DLL_COPY environment setting is preserved and can
re-enable unsafe inbound links. This is the inherited GE opt-out, not a safe mode.
The default is local to this Proton process and children; Steam's environment
is not changed. user_settings values do not override an already supplied env key.

A launcher cannot revoke aliases in unknown old prefixes before an unpatched
tool executes. In-place replacement of an old installation can leave those old
prefixes pointing into the new installation. Existing own-root symlinks are
intentionally not detached by the foreign-link pass. Ship a new version in a
distinct tool directory and retire/migrate old aliases before claiming inbound
protection for historical installations; alternatively enforce installation
immutability externally. This patch alone cannot make an existing, same-path
installation universally safe. Unpatched stock GE trees remain vulnerable
through their other prefixes and other unpatched tools. This is a product finding,
not an implemented global migration or stock-tool modification.

The prefix lock serializes cooperating Proton setup. This is not an adversarial
filesystem sandbox, a defense against concurrent pathname replacement, bind
mounts, direct Wine invocations bypassing Proton, or unpatched writers racing
setup. A copy/read/walk failure aborts before Wine. No fallback writes through
an alias. The setup copy helper retains GE's existing permission-error behavior.

## Production delta and validation

`proton`: **45 added / 9 removed lines**, including comments/spacing:

| Piece | Added / removed |
|---|---:|
| Default copy-all plus initial-template handling | 3 / 0 |
| ICU copies using existing try_copy | 4 / 9 |
| Preflight method plus one common invocation | 38 / 0 |

Within the 38-line preflight contribution, linked-parent refusal is 7 lines,
hardlink admission adds no separate branch beyond the 2-line link-count filter,
and helper/runinprefix coverage uses the same 3-line common invocation. The
linked-parent counterfactual reproduces a write into the foreign tree before
requiring refusal. No additional Wine patch or helper wrapper is included.

The temp-directory suite executes functions extracted from the actual old and
new launchers, reproduces same-inode writes, and checks private files, both
arches, WinSxS, ICU, missing targets, native overrides and failed-copy cleanup.
The hosted fixture uses two writable temporary module trees, one shared prefix
and the same pinned unpatched Wine runtime. It requires an actual Wine update
from distinct valid donor bytes to the expected module bytes, preventing a
no-op pass. It tests symlink/hardlink outbound transitions, inbound template
copies and linked-parent refusal on both arches (14 cases). It is a module-tree
regression, not two complete packaged-tool or game qualification runs.

The dedicated manual workflow reuses only the explicitly pinned reference
runtime from run `41422471027`, artifact `13135828280`; it assembles no product.
API ZIP digest:
`85f65edf14bd06986f00ce8f0747eac04c54dc9d2e45888b2e76c5cc2f431ac3`.
Archive SHA256:
`64652968faf9f7eab614e9f64dc888f4b8d226c3f8f534f845121a0e6b980d72`.
The regression records old/new launcher and runtime executable hashes alongside
per-case results and server cleanup checks. Hosted execution is unprivileged,
network disabled and reference tool read-only, with at most three CPUs.
