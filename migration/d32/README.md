# d32 migration proof

Removable release proof against the immutable d32 product. These files are not
packaging runtime or source-capsule inputs. Remove this directory and the
mandatory migration steps from the overlay workflow together in an explicit commit after publication.
The private control ZIP and complete proof receipts remain in the evidence archive.

The prepared Wine archive declares its head through Git archive metadata. Its
source members are compared individually; the raw prepared head object is not
verified. The baseline receipt verifies the actual raw baseline commit against
the independently established tree and selected source inputs in BASELINE.json.
