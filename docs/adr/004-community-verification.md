# ADR 004: Community verification records

## Context

The next round adds community corroboration, deterministic precheck, manual review and contribution points ([plan](../community-verification-plan.md), issue #32). Existing `user_reports` are free-form feedback with a single moderation status and must keep their meaning. Community input must never become legality, rate, realtime or entrance-access facts by vote count, and rule precedence (AGENTS rule 11) must stay deterministic.

## Decision

- Add separate, additive tables in migration `006_community_verification`: source verifications, participants, cases, append-only revisions, stances, private evidence photos, precheck runs/results, an immutable case timeline, an append-only contribution ledger and idempotency records. Legacy reports are not backfilled.
- Store review status and publication lifecycle separately. Persist `UNPUBLISHED/PUBLISHED/SUSPENDED/WITHDRAWN` only; derive `EXPIRED` at read time from `published_until`, so no scheduler is required for correctness.
- Keep community observations out of the normalized fact tables. Low-risk observations (lighting, rain cover, charging, entrance location) may publish after corroboration; permission, rate and entrance access need a verified source with a configured parser, or a manual decision that still cannot raise rule tier or authority.
- Configure rule tier and authority on `source_verifications`, never on a case decision.
- Classify photo time once at receipt from EXIF `DateTimeOriginal`/`OffsetTimeOriginal`, never assuming a time zone, and re-check the 30-day window independently at automatic publication.
- Identify participants through `community_participants`, which survives user deletion, so recusal and one-stance-per-person remain enforceable.
- Encode invariants in PostgreSQL CHECK/unique/composite-FK constraints and UPDATE-rejecting triggers; keep calculable policies as pure functions in `app/domain/community.py`.
- Raise the default original-photo limit to 15 MB so phones can upload unmodified originals with their metadata.

## Consequences

- Twelve native enums and eleven tables; the contract lives in [community-verification-contract.md](../community-verification-contract.md).
- APIs, workers, mobile and the Web console (#34–#36) build on this schema. Auto-publication ships disabled behind `COMMUNITY_AUTO_PUBLISH_ENABLED`.
- Mobile uploads become larger. EXIF can be edited and different accounts do not prove different people; both remain documented limitations until production identity verification exists.
