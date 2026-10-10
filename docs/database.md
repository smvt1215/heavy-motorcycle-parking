# Database Model v1.2

## Core relations
`ParkingLot -> ParkingZone -> {ParkingRule, ParkingRate, ParkingRealtime}`; `ParkingRate -> ParkingRateRule`; `ParkingLot -> ParkingEntrance`.

## Required tables
- parking_lots
- parking_zones
- parking_rules
- parking_rates
- parking_rate_rules
- parking_rate_sources
- parking_realtime
- parking_entrances
- parking_facilities
- data_sources
- raw_import_batches
- raw_parking_records
- users
- user_vehicles
- favorites
- user_reports
- report_photos
- access_tokens (M8; SHA-256 token digests only)

## Spatial rules
`parking_lots.location` and `parking_entrances.location` use `GEOGRAPHY(POINT,4326)` with GiST indexes.

## Space type enum
`HEAVY_ONLY`, `MOTO_SHARED`, `CAR_SHARED`, `LIGHT_MOTO_ONLY`.

## Tri-state permissions
`normal_heavy_allowed`, `large_heavy_allowed` are nullable class-level booleans. NULL means unknown. Historical plate/car permission columns remain source evidence and do not decide class permission.

Effective API compatibility is `ALLOWED`, `NOT_ALLOWED`, or `UNKNOWN`; database NULL must remain distinguishable from confirmed false.

## Parking-rule precedence metadata
`parking_rules` must carry enough normalized metadata for deterministic rule resolution rather than relying on row order, recency, or confidence.

Required metadata:
- `parking_id`
- optional `zone_id` (`NULL` = lot-wide)
- tri-state vehicle permission columns
- `rule_kind`: `BASELINE` or `EXCEPTION`
- `authority_priority`: integer, larger = more authoritative under explicit adapter/source policy
- `effective_from` / `effective_to`
- optional schedule constraints
- `source_id`
- source/verification timestamps
- confidence as descriptive metadata only

Adapters assign `rule_kind` and `authority_priority` from explicit source policy. Authority MUST NOT be inferred from recency or confidence.

### Effective-rule precedence
For a selected zone, vehicle, and `evaluation_at`:
1. Ignore inactive/out-of-window/out-of-schedule rules.
2. Prefer zone-specific rules over lot-wide rules.
3. Within the winning scope, prefer `EXCEPTION` over `BASELINE`.
4. Within the winning kind, keep only the highest `authority_priority`.
5. Evaluate the selected-vehicle permission across **all** rules in that highest-precedence tier:
   - every value non-null and all TRUE -> `ALLOWED`
   - every value non-null and all FALSE -> `NOT_ALLOWED`
   - any TRUE/FALSE conflict -> `UNKNOWN`
   - any mixture of NULL plus known TRUE/FALSE -> `UNKNOWN`
   - all NULL -> `UNKNOWN`
6. Do not fall back to a lower-precedence tier because the winning tier is missing/conflicting; the conservative result remains `UNKNOWN`.

`source_updated_at`, `fetched_at`, ingestion order, database ID, and confidence MUST NOT break a legality conflict. Duplicate versions of the same logical upstream rule should be deduplicated/upserted during ingestion; if contradictory highest-precedence facts remain, return `UNKNOWN`.

## Entrance accessibility
Heavy-motorcycle accessibility is tri-state. Store as nullable boolean or equivalent enum, but preserve `ALLOWED` / `NOT_ALLOWED` / `UNKNOWN` in the domain/API.

## Rate types
`FREE`, `HOURLY`, `PER_ENTRY`, `TIME_BLOCK`, `PROGRESSIVE`, `FLAT`, `DAILY`, `MONTHLY`, `CUSTOM`.
Rate rules support `ALL`, `WEEKDAY`, `WEEKEND`, `HOLIDAY`, `SPECIAL`, time windows, minute ranges, amount, unit_minutes, max_amount.

## Raw data/provenance
Preserve original payloads and raw rate text. Parsed rate status: `PARSED`, `PARTIALLY_PARSED`, `RAW_ONLY`, `INVALID`.
All normalized rule/rate/realtime/entrance records retain source references and relevant timestamps.

## Realtime availability status
Availability condition and freshness are separate dimensions.

Observation status: `AVAILABLE`, `FULL`, `UNKNOWN`, `CLOSED`.
`STALE` is not an availability status. Aging retains the last observed status while freshness is derived independently.

Examples:
- last AVAILABLE, now too old -> availability AVAILABLE + freshness STALE
- last FULL, fetch time missing -> availability FULL + freshness UNKNOWN

Do not translate generic car availability into heavy-motorcycle availability unless explicitly supported by source/rule semantics.

Realtime is zone-scoped when supported. Store `source_updated_at`, `fetched_at`, and source reference. Freshness thresholds are service/source configuration.

### Realtime numeric integrity
Normalized `available_spaces` and `total_spaces` are nullable, but when present:
- integer counts only
- `available_spaces >= 0`
- `total_spaces >= 0`
- if both non-null, `available_spaces <= total_spaces`

Use DB CHECK constraints where practical. Invalid upstream rows remain in raw ingestion evidence but are not trustworthy normalized facts.

Status/count consistency:
- AVAILABLE requires confirmed `available_spaces > 0`
- FULL requires confirmed `available_spaces = 0`
- CLOSED requires confirmed `available_spaces = 0`
- UNKNOWN never contributes to trustworthy numeric aggregation

A row may retain `total_spaces=NULL`, but cannot contribute to a COMPLETE lot-level numeric aggregate. COMPLETE coverage requires valid known available and total counts for every returned ALLOWED zone plus FRESH freshness and non-null fetched_at.

Lot-level availability summaries are derived API projections, not independent source facts. Partial/no coverage must not be persisted or presented as complete totals.

## Migrations
Alembic only. Every schema PR tests upgrade and downgrade from the supported baseline.

### M1 implementation
Revision `002_core_schema` follows `001_bootstrap` and creates the 17 tables above.
SQLAlchemy models live in `backend/app/models/`; importing `app.models` registers
every table for Alembic. The migration declares its own schema and enum labels
without importing the models, so later model changes cannot rewrite migration history.
Downgrading to `001_bootstrap` removes M1 tables and native enum types while
retaining PostGIS. Downgrading to `base` also removes the bootstrap extension.
Alembic pins its transaction search path to `public`, including autogeneration,
so visible Tiger/Topology extension tables are never treated as application tables.

Identifiers are integer primary keys (`BIGINT` for raw records and realtime
observations). Absolute timestamps use `TIMESTAMPTZ`; monetary amounts use
`NUMERIC(10,2)`. Local rate time windows use `TIME` and allow overnight ranges.
Time and duration ranges are half-open `[start, end)`. NULL/NULL local times mean
no time restriction; equal endpoints and PostgreSQL's `24:00` are rejected.
Use `00:00` as the next-day endpoint of an overnight window.
`created_at` and `updated_at` default to database `now()`; ORM updates refresh
`updated_at`, while future Core/bulk ingestion writers must update it explicitly.

| Relation | Stored identity and integrity |
| --- | --- |
| Lots / zones | Optional lot `(source_id, external_id)` is unique; zones have a required parent lot and nullable nonnegative capacity. |
| Rules / reports | Composite `(zone_id, parking_id)` foreign keys prohibit linking a zone from another lot; NULL `zone_id` means lot-wide. |
| Rules | Five nullable permissions have no boolean defaults. `rule_kind` and `authority_priority` must be supplied explicitly. Equal-tier facts are not unique. |
| Rates | Required `zone_id`, optional `vehicle_type` (NULL = unspecified applicability), parser status and raw text. Unclassified raw-only rates may have NULL type and monetary fields. `PARSED` requires a rate type; every other parser status requires raw text. |
| Rate rules / evidence | Duration ranges, local time windows and day types belong to a rate; `parking_rate_sources` retains additional independent source evidence. |
| Realtime | Zone-scoped observation history with source reference, nullable counts and fetch time; status/count consistency and count bounds are checked in PostgreSQL. |
| Entrances | Separate nullable coordinates and nullable heavy-motorcycle access, with their own provenance. |
| Raw evidence | JSONB payloads retain duplicate IDs and invalid values; composite `(batch_id, source_id)` prevents attributing a record to another source's batch. |
| Users / vehicles / favorites | Unique authentication subject, saved vehicle type, and unique `(user_id, parking_id)` favorites; token validation remains an API responsibility. |
| Reports / photos | Community evidence is separate from normalized facts; photos reference object-storage keys with unique keys and a required report. |

Source references use `RESTRICT` on deletion; raw records also prevent deletion
of their import batch. Lot/zone/rate child facts cascade with their parent.
Deleting a user removes vehicles, favorites and access tokens but leaves report
authorship and moderation attribution NULL. Migration `004_user_features` aligns the
report vocabulary with the v1 API and adds roles, preferred vehicle and access tokens;
see [user features](user-features.md).
Deleting an individual zone referenced by a report is blocked; deleting its lot
cascades both zones and reports.

Schedules are optional JSONB on rules/rates and rate rules. M2 defines the
[rule schedule contract and Asia/Taipei evaluation](compatibility-engine.md);
rate evaluation belongs to subsequent domain-service milestones.
An unspecified rate vehicle never confirms a selected vehicle's price. Explicit
source evidence covering all vehicles can be normalized into per-vehicle rates.
Database integer storage does not replace strict upstream type validation:
PostgreSQL can cast numeric input before applying checks. Adapters/services must
reject fractional counts and booleans before normalization and retain their raw
payloads. No freshness flag, compatibility result, comparison price, or lot-level
availability aggregate is persisted by M1.

`backend/tests/fixtures/parking.py` provides reusable equal-authority conflicts,
NULL permissions, mixed realtime observations, missing totals/fetch times, and
invalid numeric/status inputs for later domain and API tests. Live PostGIS tests
verify constraints, geospatial round trips, migration upgrade/downgrade and
absence of model/migration drift.

### M4 ingestion identity and raw envelopes

Revision `003_ingestion_identity` adds nullable zone `(source_id, external_id)`
identity with a unique constraint, a restricted source FK, and the same
external-ID-requires-source check as lots. Existing/manual zones remain valid
with both columns NULL. This lets source upserts preserve zone IDs without
deduplicating unrelated providers or legality rules by display name.
Nullable `source_active` marks source-owned zone tombstones without converting
absence to a NOT_ALLOWED legality fact or affecting manual zones.

`data_sources.freshness_uses_source_timestamp` defaults to false for existing
sources. Taipei realtime sets it true: freshness requires valid fetch/update
instants and uses the older instant, preserving actual fetched_at provenance
while preventing frozen upstream data from appearing fresh after polling.

Import batches add nullable `feed_kind`, JSONB `raw_payload`, `source_updated_at`
and `fetched_at`. Complete source envelopes and malformed-JSON byte evidence are
retained independently of normalized transactions. No new tables or enum labels
are added; downgrading restores the M1 schema while retaining existing rows.

## Development vehicle contract (005)

005 adds NULL class permissions, preserves historical plate evidence and archives old enum values in legacy columns. It leaves saved preferences/vehicles unset rather than migrating user data. Existing plate-specific rates remain vehicle-NULL until reimport; internal CAR source tariffs stay CAR. Up/down preserves preexisting rows. New enum labels: NORMAL_HEAVY, LARGE_HEAVY, internal CAR (never public).
