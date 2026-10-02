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
9. For v1 nearby search, `ALLOWED` is included, `NOT_ALLOWED` is excluded, and `UNKNOWN` is excluded by default unless `include_unknown=true`. Returned unknown results must remain explicitly `UNKNOWN` end-to-end.
10. Do not expose `LIGHT_MOTO_ONLY` or generic "show prohibited" controls as normal YELLOW/RED parking-search filters. Known `NOT_ALLOWED` locations stay outside legal parking search/ranking; any future prohibited-location browsing must be a separately specified discovery mode.
11. Selected-vehicle endpoints require explicit `vehicle` request context. Do not silently infer a vehicle from authentication state or client defaults.
12. Preserve provenance and freshness separately for each fact family: compatibility/rules, rates, realtime availability, and entrances. Never replace component-level provenance with one generic lot-level source.
13. Availability and rates are zone-scoped. Never use counts or prices from a non-matching zone to describe a compatible heavy-motorcycle option.
14. Realtime availability status and freshness are separate dimensions. Availability status is `AVAILABLE` / `FULL` / `UNKNOWN` / `CLOSED`; `STALE` belongs only to freshness and must never overwrite the last observed availability status.
15. `available_only=true` is conservative: only `ALLOWED` zones with availability status `AVAILABLE`, `available > 0`, confirmed numeric counts, and `freshness.status = FRESH` qualify. Missing, stale, unknown, zero-count, or nonmatching data does not qualify.
16. Lot-level availability totals require complete fresh coverage of all returned `ALLOWED` zones. Partial coverage must remain `UNKNOWN` with null totals and explicit coverage metadata.
17. Price filters may use only confirmed deterministic comparison values. Never derive an hourly comparison from progressive, per-entry, custom, conflicting schedule, or partially parsed rates by assumption.
18. Nearby pagination uses opaque keyset cursors. Clients must not parse cursors; servers must not silently restart pagination when a cursor is invalid or belongs to a different query.
19. Parking lot center and parking entrance coordinates are distinct. Entrance accessibility is tri-state (`ALLOWED` / `NOT_ALLOWED` / `UNKNOWN`). Navigation prefers a confirmed `ALLOWED` entrance and must not present an unknown entrance as confirmed accessible.
20. Do not expand MVP scope into payments, in-app navigation, chat/social feed, AI recommendations, CarPlay, Android Auto, or prohibited-location discovery mode.

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
Entrance heavy-motorcycle accessibility is tri-state: `ALLOWED` / `NOT_ALLOWED` / `UNKNOWN`.
Legality filtering happens before recommendation ranking.
Parking lot and parking zone are separate entities.
Rates are first-class domain data, not a single hourly-price field.
Realtime availability belongs to a parking zone when zone-scoped data exists.
Realtime observation status is `AVAILABLE` / `FULL` / `UNKNOWN` / `CLOSED`; freshness is independently `FRESH` / `STALE` / `UNKNOWN`.
Lot-level realtime summaries may expose numeric totals only with complete fresh zone coverage.
Parking lot center and parking entrance are separate coordinates.
