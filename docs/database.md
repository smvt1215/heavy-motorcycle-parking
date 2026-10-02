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

## Spatial rules
`parking_lots.location` and `parking_entrances.location` use `GEOGRAPHY(POINT,4326)` with GiST indexes.

## Space type enum
`HEAVY_ONLY`, `MOTO_SHARED`, `CAR_SHARED`, `LIGHT_MOTO_ONLY`.

## Tri-state permissions
`green_plate_allowed`, `white_plate_allowed`, `yellow_plate_allowed`, `red_plate_allowed`, `car_allowed` are nullable booleans. NULL means unknown.

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
