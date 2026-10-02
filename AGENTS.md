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
8. Preserve `UNKNOWN`/`NULL` states; never coerce unknown parking permission to false or true.
9. For v1 nearby search, `ALLOWED` is included, `NOT_ALLOWED` is excluded, and `UNKNOWN` is excluded by default unless `include_unknown=true`. Returned unknown results must remain explicitly `UNKNOWN` end-to-end.
10. Preserve provenance and freshness separately for each fact family: compatibility/rules, rates, realtime availability, and entrances. Never replace component-level provenance with one generic lot-level source.
11. Availability and rates are zone-scoped. Never use counts or prices from a non-matching zone to describe a compatible heavy-motorcycle option.
12. Price filters may use only confirmed deterministic comparison values. Never derive an hourly comparison from progressive, per-entry, custom, conflicting schedule, or partially parsed rates by assumption.
13. Nearby pagination uses opaque keyset cursors. Clients must not parse cursors; servers must not silently restart pagination when a cursor is invalid or belongs to a different query.
14. Parking lot center and parking entrance coordinates are distinct. Navigation prefers a confirmed heavy-motorcycle-accessible entrance and falls back to the lot center only when no usable entrance coordinate exists.
15. Do not expand MVP scope into payments, in-app navigation, chat/social feed, AI recommendations, CarPlay, or Android Auto.

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
Legality filtering happens before recommendation ranking.
Parking lot and parking zone are separate entities.
Rates are first-class domain data, not a single hourly-price field.
Realtime availability belongs to a parking zone when zone-scoped data exists.
Parking lot center and parking entrance are separate coordinates.
