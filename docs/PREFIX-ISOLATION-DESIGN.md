# Shared-prefix builtin isolation

Wine's setup writer can truncate an existing builtin through a symlink or
hardlink. Steam's shared helper prefix allows a later tool to encounter module
aliases from another installation. Prefix isolation therefore requires private
inodes before any Wine entry point.

The pinned Proton launcher defaults PROTON_DLL_COPY to `*` before session setup,
honors it during initial template population, and copies ICU files. Before Wine
runs, it atomically replaces recognized foreign builtin aliases and ICU aliases
with private files under the prefix lock. Dangling recognized aliases are removed.
Native game overrides remain intact. Linked Windows directories or linked
pfx/drive_c/windows ancestors fail before Wine can write through them.

The common preflight covers ordinary setup, helpers and runinprefix. Copy or walk
failure aborts the launch. The tool root is resolved before ownership comparison;
regular hardlinks with multiple names also require detachment.

| Prefix condition | Default behavior |
|---|---|
| Foreign builtin symlink or hardlink | Private inode before Wine |
| Foreign ICU alias | Private copy before ICU setup |
| Linked Windows directory or ancestor | Refuse launch |
| Fresh prefix | Copy modules from initial population |
| Native override | Preserve the override |
| Existing own-root alias | Preserve the alias |

Only recognized Wine builtins/placeholders and ICU aliases are detached. The
walk covers both architectures and WinSxS. Registry, tracked_files, fonts and
drive mappings retain Proton's existing behavior. Copy-all increases prefix
storage and copying; filesystems may share physical extents while keeping
separate inodes. No startup performance claim is made.

An explicit non-`*` PROTON_DLL_COPY value is honored and can admit inbound aliases.
The default affects this process and its children, not Steam's environment.
A launcher cannot revoke aliases in prefixes it has never opened before an
unpatched writer runs. Distinct installation directories and migration of old
aliases are required for protection of existing installations. Own-root aliases
and stock tools retain that boundary.

The prefix lock coordinates Proton setup. It does not protect against concurrent
pathname replacement, bind mounts, direct Wine invocations or unpatched writers.

Unit checks execute functions extracted from the actual baseline and selected
launchers. They cover both architectures, WinSxS, ICU, native overrides, missing
targets, hardlinks, parent refusal and failed-copy cleanup.

The main hosted Wine CPU job also runs 14 shared-prefix cases with the shipped
Wine writer, two writable temporary module trees and a shared prefix. Each case
requires an actual update from distinct valid donor bytes to expected module
bytes. The receipt records launcher and executable hashes, per-case outcomes and
server cleanup. This is a module-tree regression; game qualification remains a
separate gate.
