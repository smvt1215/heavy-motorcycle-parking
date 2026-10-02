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

## Entrance accessibility
Heavy-motorcycle accessibility for an entrance is tri-state. The database may store this as a nullable boolean (`TRUE` = confirmed accessible, `FALSE` = confirmed inaccessible, `NULL` = unknown) or an equivalent explicit enum, but the API/domain state MUST preserve `ALLOWED` / `NOT_ALLOWED` / `UNKNOWN` without coercion.

## Rate types
`FREE`, `HOURLY`, `PER_ENTRY`, `TIME_BLOCK`, `PROGRESSIVE`, `FLAT`, `DAILY`, `MONTHLY`, `CUSTOM`.
Rate rules support `ALL`, `WEEKDAY`, `WEEKEND`, `HOLIDAY`, `SPECIAL`, start/end time, minute ranges, amount, unit_minutes, max_amount.

## Raw data/provenance
Preserve original payloads and raw rate text. Parsed rate status: `PARSED`, `PARTIALLY_PARSED`, `RAW_ONLY`, `INVALID`.
All normalized rule/rate/realtime/entrance records must retain source references and relevant timestamps.

## Realtime availability status
Availability condition and freshness are separate dimensions.

Stored/current observation status is limited to:
- `AVAILABLE`
- `FULL`
- `UNKNOWN`
- `CLOSED`

`STALE` is **not** an availability status. Aging data retains its last observed availability status while freshness is derived separately from timestamps and source policy.

Examples:
- Last observation `AVAILABLE`, now too old -> availability status remains `AVAILABLE`, freshness = `STALE`.
- Last observation `FULL`, fetch time missing -> availability status remains `FULL`, freshness = `UNKNOWN`.

Do not translate generic car availability into heavy-motorcycle availability unless the source/rule explicitly supports that interpretation.

Realtime is zone-scoped when the source supports zone-level facts. Store source timestamps needed to derive freshness (`source_updated_at`, `fetched_at`, and source reference). Freshness thresholds are source configuration and are evaluated by the service layer; do not overwrite raw timestamps or the last observed availability status with a derived freshness label.

### Realtime numeric integrity
Normalized `available_spaces` and `total_spaces` are nullable because upstream data can be incomplete, but whenever values are present they must satisfy database/application validation:
- integer counts only
- `available_spaces >= 0`
- `total_spaces >= 0`
- when both are non-null, `available_spaces <= total_spaces`

Use database `CHECK` constraints where practical for nonnegative counts and `available <= total` while preserving NULL for unknown source values. Invalid upstream records remain available in raw ingestion storage but must not be normalized into trustworthy realtime facts without validation/error handling.

Status/count consistency is enforced by the domain/service validation used for current-availability and aggregate claims:
- `AVAILABLE` requires confirmed `available_spaces > 0`
- `FULL` requires confirmed `available_spaces = 0`
- `CLOSED` requires confirmed `available_spaces = 0`
- `UNKNOWN` is never a trustworthy numeric aggregate contributor

A normalized row may preserve `total_spaces = NULL` when capacity is genuinely unknown, but such a row cannot contribute to a COMPLETE lot-level numeric aggregate. COMPLETE coverage requires a confirmed valid `available_spaces` and `total_spaces` for every contributing returned `ALLOWED` zone.

For current-availability claims and COMPLETE lot-level aggregates, the API requires `freshness.status = FRESH`; stale or unknown freshness cannot qualify even if the last observed availability status was `AVAILABLE`.

Lot-level availability summaries are derived API projections, not independent source facts. Numeric lot totals may be emitted only when every returned `ALLOWED` zone has trustworthy fresh numeric realtime coverage with known valid totals. Partial/no coverage must not be persisted or presented as a complete lot total.

## Migrations
Alembic only. Every schema PR must test upgrade and downgrade from the supported baseline.
