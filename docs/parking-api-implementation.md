# M3 parking API implementation

The public v1 endpoints are `/parking/nearby`, `/parking/{parking_id}`,
`/parking/{parking_id}/rates` and `/parking/{parking_id}/realtime` under
`/api/v1`. They require explicit NORMAL_HEAVY or LARGE_HEAVY `vehicle`; optional `at` is an
offset-bearing RFC3339 instant. `/health` and `/version` retain their existing
behavior. User-scoped endpoints belong to their later milestones.

## Boundaries and query design

Routers validate Pydantic query models and delegate to
`ParkingDiscoveryService`. `ParkingRepository` returns preloaded snapshots;
compatibility is resolved by M2. Rates, realtime and sort_version=1 ranking
live in separate services. No mobile/government API calls, schema migration,
raw-text rate parsing or UI change is introduced.

Nearby candidates use indexed geography `ST_DWithin`, with radius in meters
and a maximum of 5000m (default 1500m). PostgreSQL calculates distance with
`floor(ST_Distance + 0.5)`, producing the same integer meters for responses and
scoring. There is no application-side full-table distance calculation or
candidate LIMIT before legality/ranking. Eight batched queries load the lots,
zones, all permission evidence, rates, rate rules, additional rate sources,
latest realtime per zone and entrances; the query count is independent of
candidate count. Empty candidates return immediately.

For realtime history, PostgreSQL DISTINCT ON selects the greatest known
`fetched_at` per zone, NULL times last, with observation ID as a stable
timestamp tie-breaker. This selection does not participate in rule legality
or rate conflict resolution. Each source's `freshness_seconds` is used;
absence uses the explicit service default of 120 seconds.

Matching zone facts are filtered before lot projection and scoring. No
car/light/prohibited/UNKNOWN-zone realtime, rate or confidence fact is
substituted for a returned ALLOWED zone. Detail and specialized endpoints
preserve all zones and explicit compatibility, including NOT_ALLOWED/UNKNOWN;
the normal nearby restrictions remain specific to nearby discovery.

## Shared wire contract and evidence

All four selected-vehicle endpoints retain every common zone base member.
`/rates` adds `rates`; `/realtime` adds a conservative lot availability summary.
The common availability member remains nullable when there is no observation.
Lot summary coverage NONE/PARTIAL exposes UNKNOWN with null totals; only
COMPLETE fresh, valid, known-total ALLOWED coverage produces numeric totals.

Compatibility adds `rule_evidence: [{rule_id, provenance}]` to preserve every
tied winner. Its singular `provenance` is populated only when one rule wins;
it is null for classification-only results or multiple winners, so no arbitrary
source is presented as resolving a conflict. Rate evidence uses a primary
`provenance` plus `supporting_sources`; realtime and entrances retain their
own independent provenance. No component borrows a lot's timestamps.

Source timestamps and nullable fields survive serialization. Money values
serialize as integers when integral and as JSON numbers otherwise. Rate
members additionally expose `applicability=MATCH|UNKNOWN`; uncertain evidence
remains available without representing it as a confirmed applicable price.

Entrance coordinates remain separate from the lot center. The backend exposes
tri-state access and coordinates for later navigation selection; it does not
label UNKNOWN as confirmed or implement in-app navigation.

## Conservative rate evaluation

Only exact selected-vehicle records are eligible; NULL vehicle means
unspecified applicability. Effective windows and parent/rule schedules are
evaluated at the pinned instant in Asia/Taipei. Constraints intersect. A
rule's column time window anchors its JSON weekday/date constraints to the
starting day for overnight windows. Parent schedules keep their own schedule
date semantics. No official holiday calendar is bundled: missing coverage
blocks confirmation, as in M2.

PARSED TWD FREE yields zero. Simple HOURLY/TIME_BLOCK rates with actual positive
integer units and no free-minute/duration dependency yield a comparison only
when their hourly equivalent is an exact finite decimal. For example,
15 TWD/45 minutes yields 20 TWD/hour; 50 TWD/120 minutes yields 25 TWD/hour.
Recurring decimal equivalents are left ineligible rather than rounded into
a guessed comparison. This is a comparison value, not a quoted duration bill.
Progressive, per-entry, daily/monthly/custom and raw/partially parsed prices
receive no hourly comparison boost. Raw text is preserved.

All applicable rates and tied matching rules must agree; unknown applicability
blocks confirmation and never falls back to a cheaper/base rate. A confirmed
parent daily cap is independent of hourly eligibility, but requires known
applicability and agreement across all applicable records. Missing rule
coverage or unknown rule applicability blocks the scheduled cap. A rule's
`max_amount` is not assumed to be a daily cap.

## Time, ranking and cursors

Middleware captures request receive time once. Nearby first-page `at` or that
receive time becomes `evaluation_at`. HMAC-SHA256 cursors authenticate the
effective query fingerprint, UTC evaluation instant, cursor version,
sort_version and exact sort tuple. Equivalent offset-bearing instants match.
Malformed/tampered, mismatched and unsupported cursors use the documented
stable error codes and never restart page one. All validation errors use the
shared error envelope; nonexistent lots use PARKING_NOT_FOUND (404).

Ranking follows the normative integer half-up formulas in `docs/api.md`.
Confirmed lots sort by group, descending score, distance and parking ID;
UNKNOWN-only lots follow by distance and ID. Keyset continuation compares
the exact same tuple. UNKNOWN children never change a confirmed score.

Realtime freshness always uses the current request time, even with a past or
future compatibility instant. Cursor pinning does not freeze ingestion or
realtime/ranking inputs: pagination is a keyset view, not a database snapshot.
Live updates may move records across pages. Stable-data ties are tested for
complete traversal with no duplicates/skips.

Set `CURSOR_SIGNING_KEY` to the same secret of at least 32 bytes on every API
worker for persistent cursors. PROD refuses to start without it. DEV/STAGING
without a configured key use a random process key; restart or another worker
then rejects that cursor as INVALID_CURSOR. Keys are stored as SecretStr and
never exposed in API/OpenAPI or documentation examples.

## Verification evidence

Integration tests exercise real PostGIS through HTTP and verify the constant
eight-query loading path. The representative EXPLAIN fixture inserts 20,000
distributed lot centers inside a transaction, runs ANALYZE, and checks the
planner chooses `ix_parking_lots_location` without disabling sequential scans.
The transaction rolls back all seed data.

Local run on PostgreSQL 18/PostGIS 3.6, 500m radius, 2 spatial candidates:

| Measurement | Observed |
| --- | --- |
| Additional representative lot centers | 20,000 |
| Spatial index | ix_parking_lots_location (GiST) |
| EXPLAIN planning | 2.055 ms |
| EXPLAIN spatial execution | 9.769 ms |
| Full API sample count | 20 |
| Full API local P95 (nearest-rank) | 65.278 ms |

These are local evidence, not a production latency guarantee. Dense queries
still load and evaluate all spatial candidates to preserve legality and exact
ranking; load testing against ingested data remains follow-up work. The
repeatable test is `test_explain_spatial_index_on_representative_seed` in
`backend/tests/test_parking_repository.py` (`pytest -s` prints the profile).
