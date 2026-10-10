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
8. Preserve `UNKNOWN`/`NULL`; never coerce unknown parking permission or entrance accessibility to true/false.
9. Nearby v1: ALLOWED included, NOT_ALLOWED excluded, UNKNOWN excluded unless `include_unknown=true`; returned unknown stays explicitly UNKNOWN.
10. Lot nearby compatibility is derived from returned zones: any ALLOWED => lot ALLOWED; otherwise returned UNKNOWN => lot UNKNOWN; known NOT_ALLOWED zones are filtered first.
11. Parking-rule resolution is deterministic: applicable zone-specific > lot-wide; EXCEPTION > BASELINE; highest configured authority_priority; then evaluate all tied highest-tier rules. Any TRUE/FALSE conflict, any NULL mixed with a known value, or all NULL => UNKNOWN. Never break legality conflicts with recency, row order, IDs, or confidence, and never fall back to a lower tier.
12. Do not expose LIGHT_MOTO_ONLY in LARGE_HEAVY search. NORMAL_HEAVY may search confirmed applicable ordinary motorcycle zones. Generic show-prohibited controls are excluded from both classes; prohibited-location browsing is a separate future mode.
13. Selected-vehicle endpoints require explicit `vehicle`; do not infer it from authentication/profile state.
14. Scheduled compatibility/rates use explicit `evaluation_at`. Nearby cursors pin it. Detail/rates/realtime accept optional `at` and expose resolved `evaluation_at`. MVP local schedule evaluation uses Asia/Taipei. Realtime freshness remains current-data freshness.
15. User-scoped endpoints use Bearer access-token authentication. Identity comes from the validated token; missing/invalid credentials => 401, insufficient permission => 403.
16. Preserve provenance independently for compatibility/rules, rates, realtime, and entrances.
17. Availability and rates are zone-scoped. Every selected-vehicle parking API uses the documented common zone base (`zone_id`, name, space_type, capacity, compatibility, rate_summary, availability). Specialized endpoints may add fields but may not omit the base fields or substitute cross-zone facts.
18. Realtime availability status is AVAILABLE/FULL/UNKNOWN/CLOSED; freshness is FRESH/STALE/UNKNOWN. STALE never replaces the observed availability status.
19. FRESH realtime requires non-null fetched_at. A FRESH example/fixture without fetched_at is invalid.
20. `available_only=true` requires ALLOWED + AVAILABLE + integer available>0 + FRESH + fetched_at. total may be NULL, but if supplied must be nonnegative integer with available<=total.
21. Trustworthy numeric realtime requires nonnegative integer counts and available<=total when total is known. COMPLETE lot coverage requires known valid total for every contributing ALLOWED zone.
22. Lot numeric availability totals require complete fresh coverage of returned ALLOWED zones. UNKNOWN realtime never contributes; PARTIAL/NONE => UNKNOWN summary with null totals.
23. COMPLETE aggregate provenance covers every contributor and is never empty for a numeric aggregate.
24. Price filters use only confirmed deterministic comparison values at evaluation_at; never guess from ambiguous/non-normalizable rates.
25. Confirmed ALLOWED ranking uses only returned ALLOWED-zone facts; UNKNOWN-zone facts cannot alter the confirmed score.
26. `sort_version=1` ranking formulas/bands/rounding in `docs/api.md` are normative. Confirmed order is `(group=0, score DESC, distance ASC, parking_id ASC)`; unknown-only order is `(group=1, distance ASC, parking_id ASC)`. Any scoring semantic change requires a new sort_version.
27. Nearby pagination uses opaque keyset cursors bound to query, evaluation_at, sort_version, and exact sort keys. Never silently restart on invalid/mismatched/unsupported cursor.
28. Lot center and entrance coordinates are distinct. Entrance accessibility is ALLOWED/NOT_ALLOWED/UNKNOWN; navigation prefers confirmed ALLOWED entrance and never labels UNKNOWN as confirmed.
29. Do not expand MVP into payments, in-app navigation, chat/social feed, AI recommendations, CarPlay, Android Auto, or prohibited-location discovery.

## Git hygiene
After a PR is merged, and before starting the next stage (the next issue, PR or follow-up task), clean up its git state:
- Fast-forward local `main` to `origin/main` and run `git fetch --prune`.
- Delete the merged branch locally and on the remote (if GitHub did not already); keep only branches with open PRs.
- Remove its worktree with `git worktree remove`, then run `git worktree prune`.
- Delete temporary files and backup branches created for that PR once their content is verified to be in `main` or published on GitHub.
- Before deleting anything not obviously merged, confirm its content is already in `main` (merged PR, `git cherry`, or an empty diff); otherwise ask first.

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
Parking space types: HEAVY_ONLY, MOTO_SHARED, CAR_SHARED, LIGHT_MOTO_ONLY.
Vehicle permissions are tri-state TRUE/FALSE/NULL.
Effective compatibility is ALLOWED/NOT_ALLOWED/UNKNOWN.
Highest-precedence rule tiers containing any unknown/conflict remain UNKNOWN conservatively.
Nearby lot compatibility is a deterministic zone rollup, not an independent source fact.
Parking lot and zone are separate entities.
Selected-vehicle API zones have one common base wire schema.
Rates are first-class domain data.
Realtime is zone-scoped when source data supports it.
Realtime status and freshness are independent.
Realtime UNKNOWN is never complete numeric coverage.
Lot totals require complete fresh valid coverage and complete contributor provenance.
User-scoped resources are authorized from bearer-token identity.
Scheduled compatibility/rate evaluation is pinned to an absolute instant and interpreted in Asia/Taipei for v1.
Realtime compatibility may be pinned by evaluation_at while realtime freshness remains current.
Nearby scoring and cursor key order are deterministic and sort-versioned.
Parking lot center and entrance are separate coordinates.
