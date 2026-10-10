# Data Sources and Ingestion

## Initial sources
Taipei City and New Taipei City parking/open-data services. Exact endpoints and field mappings are documented inside each adapter once implemented.

## Pipeline
Source API -> Downloader -> Raw storage -> City Adapter -> Normalizer -> Validator -> Rule mapping -> PostgreSQL -> Redis -> API.

The mobile app must never call city government APIs directly.

M4 implements the [Taipei V2 mapping and worker](taipei-ingestion.md), including
verified endpoints, independent static/realtime sources, attribution, strict
category evidence, replay commands and record-level failure inspection.

Google Places API (New) is a destination geocoding source only (M6, see
[destination search](destination-search.md)). It is called by the backend, never
stored in parking tables, and never used for legality, rates or availability.
Device-local recent searches cache Google coordinates for at most 30 days.

M7 adds the [New Taipei paged feeds](new-taipei-ingestion.md) through the same
worker. New Taipei publishes no heavy-motorcycle counts, so its lots stay UNKNOWN
for LARGE_HEAVY; conventional motorcycle zones may confirm NORMAL_HEAVY.

## Adapter contract
Create `BaseParkingAdapter`, then `TaipeiParkingAdapter` and `NewTaipeiParkingAdapter`. City-specific parsing stays outside core domain logic.

## Required ingestion properties
- raw payload retention
- idempotent upsert
- timeout/retry/exponential backoff
- schema validation
- source timestamps and fetch timestamps
- observable partial-record failures
- no guessed legality or guessed prices

## Provenance levels
`GOVERNMENT`, `OPERATOR`, `COMMUNITY`, `MANUAL`.
Community reports never silently overwrite official data; they remain separately attributable until a verification workflow promotes a correction.

## Licensing
Repository source code is Apache-2.0. External datasets remain under their original terms. Taiwan government open data attribution must be retained as required by the applicable source license. Google Maps/Places content is governed by Google terms and is not relicensed under Apache-2.0.
