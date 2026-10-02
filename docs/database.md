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

## Rate types
`FREE`, `HOURLY`, `PER_ENTRY`, `TIME_BLOCK`, `PROGRESSIVE`, `FLAT`, `DAILY`, `MONTHLY`, `CUSTOM`.
Rate rules support `ALL`, `WEEKDAY`, `WEEKEND`, `HOLIDAY`, `SPECIAL`, start/end time, minute ranges, amount, unit_minutes, max_amount.

## Raw data/provenance
Preserve original payloads and raw rate text. Parsed rate status: `PARSED`, `PARTIALLY_PARSED`, `RAW_ONLY`, `INVALID`.
All normalized rule/rate/realtime/entrance records must retain source references and relevant timestamps.

## Realtime status
`AVAILABLE`, `FULL`, `UNKNOWN`, `CLOSED`, `STALE`.
Do not translate generic car availability into heavy-motorcycle availability unless the source/rule explicitly supports that interpretation.

## Migrations
Alembic only. Every schema PR must test upgrade and downgrade from the supported baseline.
