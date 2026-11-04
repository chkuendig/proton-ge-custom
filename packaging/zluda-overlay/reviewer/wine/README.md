Reviewer-only prepared-Wine source contracts. The owner-authored token auditor
was retained from ZLUDA 42b557a23c9c7868ee1fb736849451361a6e8893; its Wine-derived
contracts remain under Wine terms (see LICENSE.md). It audits the separately
prepared Wine source, outside the production ZLUDA graph. All callback, thread
shutdown, TLS and loader-lock body checks remain required. Production receives
only the digest-pinned result and matching SDK binary/provider identities.
