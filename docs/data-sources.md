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
worker. Confirmed ordinary motorcycle zones support NORMAL_HEAVY.
[Reviewed twin-city policies and New Taipei roadside ingestion](twin-city-policy.md)
add LARGE_HEAVY permission only inside verified scope; all other permissions stay UNKNOWN.
The [twin-city data gap report](twin-city-data-gaps.md) records the 2026-10-10 source checks, the rerunnable roster reconciliation and what remains UNKNOWN.

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

## Reference materials (not ingested)

Reference materials help locate gaps and candidates for verification. They are
never ingested, never assigned a `data_source`, and never confirm legality,
rates, availability or entrance access. A lead becomes a fact only after the
matching official/operator source is verified and normalized (see
[community verification plan](community-verification-plan.md) §2).

| Material | Nature | Allowed use |
| --- | --- | --- |
| [Alan大重停車記事](https://www.google.com/maps/d/viewer?mid=1ORD5DnL6yqrCrtQJYB9TeTgOOlvo-Yc)（Google My Maps） | Rider-submitted map curated by its author via a Google Form; about 1,321 placemarks in 9 layers (free/car-rate/heavy-bay/motorcycle-rate lots, roadside bays, heavy-friendly shops, prohibited, unconfirmed); free-text notes; no license stated. Checked 2026-10-10 (map changelog last updated 2026-10-06). | Twin-city gap and conflict leads for #33, e.g. lots we lack or hold as UNKNOWN, and "禁停重機" entries to cross-check against our data. No bulk copying, republication or redistribution of its content without the author's permission. |

## Licensing
Repository source code is Apache-2.0. External datasets remain under their original terms. Taiwan government open data attribution must be retained as required by the applicable source license. Google Maps/Places content is governed by Google terms and is not relicensed under Apache-2.0.
