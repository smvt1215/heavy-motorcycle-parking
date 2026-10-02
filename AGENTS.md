# AGENTS.md

## Project
Heavy Motorcycle Parking (重機停車通)

## Source of truth
- `docs/product-spec.md`
- `docs/architecture.md`
- `docs/database.md`
- `docs/api.md`
- `docs/data-sources.md`
- `docs/design-system.md`
- GitHub Issues #1–#10

## Delivery rules
1. Work one milestone issue at a time, in numeric order unless explicitly told otherwise.
2. One issue = one feature branch/worktree = one pull request.
3. Do not merge your own PR.
4. Add or update tests with every behavior change.
5. Keep business logic out of Flutter widgets and FastAPI routers.
6. Do not let the mobile app call government APIs directly.
7. Do not infer legality, price, realtime availability, or entrance accessibility from ambiguous source data.
8. Preserve `UNKNOWN`/`NULL` states; never coerce unknown parking permission or entrance accessibility to false or true.
9. For v1 nearby search, `ALLOWED` is included, `NOT_ALLOWED` is excluded, and `UNKNOWN` is excluded by default unless `include_unknown=true`. Returned unknown zone states must remain explicitly `UNKNOWN` end-to-end.
10. Lot-level nearby compatibility is derived from returned zones: `ALLOWED` if any returned zone is ALLOWED; otherwise `UNKNOWN` if an UNKNOWN zone was explicitly returned. Known NOT_ALLOWED zones are filtered before lot rollup and never cause a nearby lot-level NOT_ALLOWED summary.
11. Do not expose `LIGHT_MOTO_ONLY` or generic "show prohibited" controls as normal YELLOW/RED parking-search filters. Known `NOT_ALLOWED` locations stay outside legal parking search/ranking; any future prohibited-location browsing must be a separately specified discovery mode.
12. Selected-vehicle endpoints require explicit `vehicle` request context. Do not silently infer a vehicle from authentication state or client defaults.
13. Preserve provenance and freshness separately for each fact family: compatibility/rules, rates, realtime availability, and entrances. Never replace component-level provenance with one generic lot-level source.
14. Availability and rates are zone-scoped. Never use counts or prices from a non-matching zone to describe a compatible heavy-motorcycle option.
15. Realtime availability status and freshness are separate dimensions. Availability status is `AVAILABLE` / `FULL` / `UNKNOWN` / `CLOSED`; `STALE` belongs only to freshness and must never overwrite the last observed availability status.
16. `available_only=true` is conservative: only `ALLOWED` zones with availability status `AVAILABLE`, `available > 0`, confirmed numeric counts, and `freshness.status = FRESH` qualify. Missing, stale, unknown, zero-count, or nonmatching data does not qualify.
17. Lot-level availability totals require complete fresh coverage of all returned `ALLOWED` zones. Only trustworthy `AVAILABLE` / `FULL` / `CLOSED` observations may count toward coverage; realtime status `UNKNOWN` never contributes even if numeric fields/timestamps are present. Partial coverage must remain `UNKNOWN` with null totals and explicit coverage metadata.
18. COMPLETE aggregate provenance must include every contributor (deduplicated), never an empty/missing contributor list for a numeric aggregate.
19. Price filters may use only confirmed deterministic comparison values. Never derive an hourly comparison from progressive, per-entry, custom, conflicting schedule, or partially parsed rates by assumption.
20. Nearby pagination uses opaque keyset cursors. Clients must not parse cursors; servers must not silently restart pagination when a cursor is invalid or belongs to a different query.
21. Parking lot center and parking entrance coordinates are distinct. Entrance accessibility is tri-state (`ALLOWED` / `NOT_ALLOWED` / `UNKNOWN`). Navigation prefers a confirmed `ALLOWED` entrance and must not present an unknown entrance as confirmed accessible.
22. Do not expand MVP scope into payments, in-app navigation, chat/social feed, AI recommendations, CarPlay, Android Auto, or prohibited-location discovery mode.

## Required PR body
- Summary
- Architecture impact
- Database impact
- API impact
- Mobile impact
- Tests
- Manual verification
- Known limitations
- Follow-up work

## Quality gates
Backend: lint + pytest + migration tests.
Mobile: `flutter analyze` + `flutter test`.
Infrastructure: Docker build / compose validation where relevant.
CI must pass before merge.

## Domain invariants
Parking space types: `HEAVY_ONLY`, `MOTO_SHARED`, `CAR_SHARED`, `LIGHT_MOTO_ONLY`.
Vehicle permissions are tri-state: TRUE / FALSE / NULL.
Effective compatibility is tri-state: `ALLOWED` / `NOT_ALLOWED` / `UNKNOWN`.
Nearby lot-level compatibility is a deterministic rollup of returned zone compatibility, not an independent source fact.
Entrance heavy-motorcycle accessibility is tri-state: `ALLOWED` / `NOT_ALLOWED` / `UNKNOWN`.
Legality filtering happens before recommendation ranking.
Parking lot and parking zone are separate entities.
Rates are first-class domain data, not a single hourly-price field.
Realtime availability belongs to a parking zone when zone-scoped data exists.
Realtime observation status is `AVAILABLE` / `FULL` / `UNKNOWN` / `CLOSED`; freshness is independently `FRESH` / `STALE` / `UNKNOWN`.
Realtime status `UNKNOWN` is never valid complete numeric coverage.
Lot-level realtime summaries may expose numeric totals only with complete fresh zone coverage and complete contributor provenance.
Parking lot center and parking entrance are separate coordinates.
