# REST API v1

Base path: `/api/v1`.

## Endpoints
- `GET /health`
- `GET /version`
- `GET /parking/nearby`
- `GET /parking/{id}`
- `GET /parking/{id}/rates`
- `GET /parking/{id}/realtime`
- `GET /places/autocomplete`
- `GET /places/{place_id}`
- `GET /parking/{id}/reports`
- `POST /reports`
- `GET /favorites`
- `POST /favorites`
- `DELETE /favorites/{parking_id}`
- `GET /me`

## Authentication and authorization
Public parking discovery endpoints are usable without authentication unless explicitly stated otherwise.

Protected user-scoped endpoints:
- `GET /me`
- `GET /favorites`
- `POST /favorites`
- `DELETE /favorites/{parking_id}`
- authenticated report ownership/history operations when introduced

Clients authenticate with:

```http
Authorization: Bearer <access_token>
```

The backend derives identity only from the validated token. Clients MUST NOT choose `/me` or `/favorites` ownership by supplying a user ID.

- missing/malformed/expired/revoked/invalid credentials => HTTP 401, code `UNAUTHENTICATED`
- valid identity without required permission => HTTP 403, code `FORBIDDEN`
- favorites are scoped to the authenticated user
- guest mode may use public parking/search/detail APIs but not protected user endpoints

Protected operations MUST declare the bearer security scheme in OpenAPI.

## Vehicle and evaluation context
Selected-vehicle endpoints require explicit `vehicle`; v1 public/mobile parking flows support `YELLOW` and `RED`.

Required on:
- `GET /parking/nearby`
- `GET /parking/{id}`
- `GET /parking/{id}/rates`
- `GET /parking/{id}/realtime`

The server MUST NOT infer the selected vehicle from login/profile state.

Scheduled parking rules/rates are evaluated at one explicit absolute instant, `evaluation_at`.

- Clients may supply optional `at=<RFC3339 timestamp with offset>` on nearby/detail/rates/realtime requests.
- If `at` is omitted on a nearby first page, the server captures request-received time as `evaluation_at`.
- Every nearby continuation cursor reuses the same pinned `evaluation_at`.
- If `at` is omitted on detail/rates/realtime, that request's receive instant is used.
- Responses from nearby/detail/rates/realtime expose the resolved `evaluation_at` whenever selected-vehicle compatibility is present.
- Supplying `at` that conflicts with a cursor-pinned instant => `CURSOR_QUERY_MISMATCH`.
- MVP weekday/weekend/holiday/time-of-day rule interpretation uses `Asia/Taipei` after converting the absolute instant.

Realtime **freshness** remains current-observation freshness and is not rewound by `evaluation_at`. The pinned instant controls compatibility/scheduled-rule context attached to the realtime response; freshness is still evaluated against current server/source policy.

M4 Taipei realtime enables source-timestamp freshness policy. Its freshness age
uses the earlier of actual `fetched_at` and `source_updated_at`; missing or future
required timestamps yield UNKNOWN freshness. Re-fetching frozen upstream data
does not make it FRESH. This source policy changes input facts, not v1 ranking
formulas or cursor sort keys; other sources retain their configured fetch policy.

## Provenance
Rule/compatibility, rate, realtime, and entrance facts may come from different datasets and MUST retain separate provenance.

```json
{
  "source_id": 12,
  "source_type": "GOVERNMENT",
  "source_updated_at": "2026-10-02T02:00:00Z",
  "fetched_at": "2026-10-02T02:01:00Z",
  "verified_at": null
}
```

Do not substitute unrelated component timestamps. For realtime, non-null `fetched_at` is required before freshness may be `FRESH`.

## Effective parking-rule resolution
Compatibility is resolved deterministically for `(parking_id, zone_id, vehicle, evaluation_at)`.

Applicable rules are filtered by effective date/time and local schedule first. Precedence is:
1. zone-specific rule over lot-wide rule
2. `EXCEPTION` over `BASELINE`
3. highest configured `authority_priority`
4. evaluate **all** rules tied in that highest-precedence tier

For the selected vehicle in the winning tier:
- every permission is non-null and all are `TRUE` => `ALLOWED`
- every permission is non-null and all are `FALSE` => `NOT_ALLOWED`
- any `TRUE`/`FALSE` disagreement => `UNKNOWN`
- **any mixture containing `NULL` and a known value** (for example `[TRUE,NULL]` or `[FALSE,NULL]`) => `UNKNOWN`
- all `NULL` => `UNKNOWN`

`NULL` therefore does not abstain at the winning tier. Do not fall back to lower precedence because a winning-tier value is missing or conflicting.

`source_updated_at`, `fetched_at`, row/database ID, ingestion order, and confidence MUST NOT break a legality conflict. Confidence is descriptive only. Adapters/source policy assign `rule_kind` and `authority_priority`; authority must not be inferred from recency or model confidence.

## Nearby request
`GET /parking/nearby`

Required: `lat`, `lng`, `vehicle`.

Optional:
- `radius`
- `space_type`
- `available_only`
- `hourly_rate_max_twd`
- `daily_max_required`
- `include_unknown`
- `at`
- `limit`
- `cursor`

Defaults:
- `radius=1500`
- `available_only=false`
- `include_unknown=false`
- `limit=20`
- maximum `limit=100`

Supported MVP radius presets: 500m / 1km / 3km / 5km.

### Space type
For YELLOW/RED search, `space_type` may narrow compatible results to `HEAVY_ONLY`, `MOTO_SHARED`, or `CAR_SHARED`.

`LIGHT_MOTO_ONLY` is known `NOT_ALLOWED` for YELLOW/RED and is not a normal v1 search filter. `include_unknown=true` includes compatibility `UNKNOWN`, never known `NOT_ALLOWED` locations. Any future prohibited-location browsing is a separate discovery mode.

## Compatibility
Zone compatibility is `ALLOWED` / `NOT_ALLOWED` / `UNKNOWN`. `UNKNOWN` MUST NOT be coerced to true/false.

Nearby policy:
1. `ALLOWED` zones are eligible for normal results/ranking.
2. `NOT_ALLOWED` zones are excluded.
3. `UNKNOWN` zones are excluded by default.
4. With `include_unknown=true`, UNKNOWN zones may be returned but remain explicitly unverified.
5. Compatibility filtering precedes ranking.

### Lot rollup
Lot-level nearby compatibility is derived only from returned zones:
1. `ALLOWED` if any returned zone is ALLOWED.
2. Otherwise `UNKNOWN` if an UNKNOWN zone was explicitly returned.
3. A lot with no returned zones is omitted.
4. Nearby lot-level `NOT_ALLOWED` is never emitted because known prohibited zones are filtered first.

Thus ALLOWED + UNKNOWN => lot ALLOWED while the child UNKNOWN remains UNKNOWN.

## Common zone wire schema
Every selected-vehicle parking endpoint uses this base zone member. Specialized endpoints MAY add endpoint-specific fields, but MUST NOT omit these base fields.

```json
{
  "zone_id": 20,
  "name": "B2 大重機區",
  "space_type": "HEAVY_ONLY",
  "capacity": 20,
  "compatibility": {
    "status": "ALLOWED",
    "vehicle": "RED",
    "reason": "explicit_vehicle_permission",
    "confidence": 1.0,
    "provenance": {
      "source_id": 4,
      "source_type": "GOVERNMENT",
      "source_updated_at": "2026-10-02T01:50:00Z",
      "fetched_at": "2026-10-02T01:51:00Z",
      "verified_at": null
    }
  },
  "rate_summary": {
    "display_text": "20元/小時・最高100元/日",
    "comparison_eligible": true,
    "comparison_hourly_rate_twd": 20,
    "daily_max_twd": 100,
    "parse_status": "PARSED",
    "provenance": {
      "source_id": 7,
      "source_type": "GOVERNMENT",
      "source_updated_at": "2026-10-01T00:00:00Z",
      "fetched_at": "2026-10-02T01:00:00Z",
      "verified_at": null
    }
  },
  "availability": {
    "status": "AVAILABLE",
    "available": 8,
    "total": 20,
    "freshness": {"status": "FRESH"},
    "provenance": {
      "source_id": 9,
      "source_type": "OPERATOR",
      "source_updated_at": "2026-10-02T02:00:00Z",
      "fetched_at": "2026-10-02T02:01:00Z",
      "verified_at": null
    }
  }
}
```

Base fields:
- `zone_id`: required stable numeric identifier
- `name`: required but nullable display name
- `space_type`: required enum value
- `capacity`: required but nullable known physical capacity
- `compatibility`: required selected-vehicle compatibility object
- `rate_summary`: required field, nullable when no applicable trustworthy/normalized summary exists
- `availability`: required field, nullable when no current/last realtime observation exists

`rate_summary.provenance`, `availability.provenance`, and compatibility provenance are independent. Facts from another zone MUST NOT be substituted.

## Realtime model
Availability observation status and freshness are separate.

Availability status: `AVAILABLE`, `FULL`, `UNKNOWN`, `CLOSED`.
Freshness: `FRESH`, `STALE`, `UNKNOWN`.

`STALE` is not an availability status. Aging preserves the last observation status and changes only freshness.

Freshness rules:
- `FRESH` requires non-null `fetched_at` and compliance with source/server freshness policy
- `STALE` means an observation exists but is too old for current claims
- `UNKNOWN` includes missing/indeterminable fetch time

Realtime is zone-scoped whenever the source supports zone-level facts.

### Numeric integrity
A trustworthy numeric observation satisfies:
- `available` integer >= 0 when present
- `total` integer >= 0 when present
- if both are present, `available <= total`
- `AVAILABLE` => confirmed `available > 0`
- `FULL` => confirmed `available = 0`
- `CLOSED` => confirmed `available = 0`

Invalid upstream counts remain raw ingestion evidence, not trustworthy normalized claims.

## `available_only`
`available_only=true` qualifies a lot only when at least one otherwise-matching zone has:
- compatibility `ALLOWED`
- availability `AVAILABLE`
- confirmed integer `available > 0`
- freshness `FRESH`
- non-null realtime `fetched_at`

`total` may be null for this filter. If supplied, it MUST be a nonnegative integer and `available <= total`; otherwise the observation is invalid and does not qualify.

The following do not qualify: FULL/UNKNOWN/CLOSED, AVAILABLE with zero, invalid known total, stale/unknown/missing freshness, missing realtime/fetched_at, or nonmatching/non-ALLOWED zone.

## Lot-level availability summary
`availability_summary` is derived only from returned ALLOWED zones. It excludes car-only, light-motorcycle-only, NOT_ALLOWED, and compatibility-UNKNOWN counts.

A trustworthy COMPLETE contributor requires:
- realtime status AVAILABLE/FULL/CLOSED, never UNKNOWN
- confirmed integer `available >= 0`
- confirmed integer `total >= 0`
- `available <= total`
- status/count consistency above
- freshness FRESH
- non-null `fetched_at`

A row may preserve `total=null`, but cannot contribute to COMPLETE numeric coverage.

Define:
- `eligible_zone_count`: returned ALLOWED zones
- `fresh_realtime_zone_count`: eligible zones satisfying all COMPLETE contributor requirements

Coverage:
- `COMPLETE`: eligible > 0 and fresh count == eligible count
- `PARTIAL`: 0 < fresh count < eligible count
- `NONE`: fresh count == 0

For PARTIAL/NONE: summary `status=UNKNOWN`, `available=null`, `total=null`. Clients MUST NOT display a partial child sum as a lot total.

Only COMPLETE may expose numeric totals.

### COMPLETE status
1. `AVAILABLE` if summed available > 0.
2. Else `CLOSED` if every contributor is CLOSED.
3. Else `FULL` if summed available = 0, at least one contributor is FULL, and all others are FULL/CLOSED.
4. Else `UNKNOWN` defensively.

### COMPLETE freshness/provenance
For COMPLETE coverage:
- freshness = FRESH
- `oldest_source_updated_at` = minimum only if every contributor supplies it; else null
- `oldest_fetched_at` = minimum contributor fetched_at
- `contributing_sources` contains deduplicated provenance covering every contributor and is never empty

## Rate filtering
`price_max` is absent from v1 because it is ambiguous across rate models.

`hourly_rate_max_twd` compares only a confirmed deterministic hourly-equivalent value for the selected vehicle/zone at the pinned evaluation_at.

Eligible examples: FREE => 0 TWD/hour; simple confirmed time-unit rate exactly normalizable to one hour.

Ineligible without a future explicit normalization rule: progressive, per-entry, daily/monthly/custom, conflicting schedules, partially parsed/raw-only.

A zone qualifies only when `comparison_eligible=true` and comparison value <= threshold. Never guess.

`daily_max_required=true` requires a confirmed daily cap for at least one otherwise-matching ALLOWED zone at the same evaluation instant.

## Nearby processing
1. Resolve/pin evaluation_at and convert to Asia/Taipei for scheduled rules/rates.
2. Resolve effective rules using deterministic precedence.
3. Apply compatibility at zone level.
4. Apply space type, realtime/operating, rate, and request filters.
5. Retain matching zones per lot; never substitute nonmatching-zone facts.
6. Derive lot-level compatibility.
7. Rank confirmed ALLOWED lots using returned ALLOWED-zone facts only. UNKNOWN-zone price/availability/confidence MUST NOT change an ALLOWED lot score.
8. If requested, put unknown-only lots in a separate unverified group after confirmed lots.
9. Apply keyset pagination using the exact versioned ranking tuple.

## Ranking: `sort_version=1`
Compatibility/legal applicability is a hard filter and never a score component.

All component values are integers in `0..10000`. All division uses integer **round-half-up** for nonnegative values: `round_half_up(n/d) = floor((n + d/2) / d)`. Clamp results to `0..10000`.

The response `distance_m` and scoring distance use the same integer meter value: `distance_m = round_half_up(ST_Distance(...))`.

### Distance component (35%)
Use the effective query radius in meters (`radius_m`, default 1500):

```text
distance_component_bp =
  round_half_up(10000 * max(radius_m - distance_m, 0) / radius_m)
```

A lot at the query point scores 10000; a lot at the radius boundary scores 0.

### Availability component (25%)
Use only the lot `availability_summary` derived from returned ALLOWED zones:

```text
if coverage != COMPLETE: 0
else if total <= 0:       0
else: round_half_up(10000 * available / total)
```

PARTIAL/NONE/unknown coverage gets no availability boost.

### Price component (20%)
Use only a selected-vehicle ALLOWED-zone `rate_summary` with `comparison_eligible=true` at evaluation_at. For a lot, use the **lowest eligible** `comparison_hourly_rate_twd` among returned ALLOWED zones.

`sort_version=1` price bands:
- exactly 0 TWD/hour => 10000
- >0 and <=20 => 8000
- >20 and <=30 => 6000
- >30 and <=50 => 4000
- >50 => 2000
- no eligible comparison value => 0

Unknown, raw-only, partially parsed, per-entry, or otherwise ineligible rates get no price boost.

### Confidence component (15%)
Compatibility confidence is normalized to `0.0..1.0`. For a confirmed lot, use the **maximum non-null compatibility confidence among returned ALLOWED zones**:

```text
confidence_component_bp = round_half_up(clamp(confidence, 0, 1) * 10000)
```

If all are null, score 0. Confidence never overrides compatibility legality.

### Entrance-quality component (5%)
- 10000: at least one entrance has coordinates and `heavy_motorcycle_access=ALLOWED`
- 5000: no confirmed ALLOWED entrance exists, but at least one coordinate-bearing entrance is `UNKNOWN`
- 0: otherwise, including only NOT_ALLOWED entrances or no usable entrance coordinate

### Final score and ordering
For nonnegative integer components:

```text
weighted_sum =
  distance_component_bp * 35 +
  availability_component_bp * 25 +
  price_component_bp * 20 +
  confidence_component_bp * 15 +
  entrance_component_bp * 5

ranking_score_bp = floor((weighted_sum + 50) / 100)
```

Confirmed ALLOWED sort tuple:
1. `ranking_group = 0`
2. `ranking_score_bp DESC`
3. `distance_m ASC`
4. `parking_id ASC`

Unknown-only sort tuple:
1. `ranking_group = 1`
2. `distance_m ASC`
3. `parking_id ASC`

Unknown-only results receive no confirmed price/availability/confidence boosts.

Changing any component formula, band, rounding rule, weight, or key order requires a new `sort_version`.

## Nearby response

```json
{
  "evaluation_at": "2026-10-02T09:30:00Z",
  "items": [
    {
      "id": 12345,
      "name": "XX地下停車場",
      "distance_m": 420,
      "location": {"lat": 25.0331, "lng": 121.5628},
      "compatibility": {"status": "ALLOWED", "vehicle": "RED"},
      "zones": [
        {
          "zone_id": 20,
          "name": "B2 大重機區",
          "space_type": "HEAVY_ONLY",
          "capacity": 20,
          "compatibility": {"status": "ALLOWED", "vehicle": "RED"},
          "rate_summary": null,
          "availability": null
        }
      ],
      "availability_summary": null
    }
  ],
  "page": {"next_cursor": "opaque-token", "has_more": true}
}
```

## Pagination
Nearby uses opaque keyset pagination.

- clients MUST NOT parse/modify cursors
- cursor is bound to location, radius, vehicle, filters, evaluation_at, and sort_version
- first page without at pins request-received time
- continuation reuses cursor-pinned evaluation_at
- changed query or conflicting at + old cursor => cursor error
- confirmed cursor keys: `(ranking_group, ranking_score_bp, distance_m, parking_id)`
- unknown-only cursor keys: `(ranking_group, distance_m, parking_id)`
- server encoding may contain a superset plus query fingerprint/version metadata

Final page has `next_cursor=null`, `has_more=false`.

Stable cursor errors: `INVALID_CURSOR`, `CURSOR_QUERY_MISMATCH`, `CURSOR_VERSION_UNSUPPORTED`. Cursor errors MUST NOT silently restart page one.

## Parking detail response
`GET /parking/{id}?vehicle=RED&at=2026-10-02T09:30:00Z`

```json
{
  "id": 12345,
  "vehicle": "RED",
  "evaluation_at": "2026-10-02T09:30:00Z",
  "name": "XX地下停車場",
  "location": {"lat": 25.0331, "lng": 121.5628},
  "zones": [
    {
      "zone_id": 20,
      "name": "B2 大重機區",
      "space_type": "HEAVY_ONLY",
      "capacity": 20,
      "compatibility": {"status": "ALLOWED", "vehicle": "RED"},
      "rate_summary": null,
      "availability": null
    }
  ],
  "entrances": [
    {
      "id": 301,
      "name": "忠孝東路入口",
      "location": {"lat": 25.0333, "lng": 121.5625},
      "entrance_type": "VEHICLE",
      "heavy_motorcycle_access": "ALLOWED",
      "notes": null,
      "provenance": {
        "source_id": 11,
        "source_type": "GOVERNMENT",
        "source_updated_at": "2026-10-01T00:00:00Z",
        "fetched_at": "2026-10-02T01:00:00Z",
        "verified_at": null
      }
    }
  ]
}
```

Entrance access is ALLOWED / NOT_ALLOWED / UNKNOWN. Prefer ALLOWED entrance; UNKNOWN may be shown as unverified; NOT_ALLOWED is never selected; lot center is explicit fallback only when no usable confirmed entrance coordinate exists.

## Rates response
`GET /parking/{id}/rates?vehicle=RED&at=...`

The response uses the common zone base and adds `rates` as an endpoint-specific extension.

```json
{
  "parking_id": 12345,
  "vehicle": "RED",
  "evaluation_at": "2026-10-02T09:30:00Z",
  "zones": [
    {
      "zone_id": 20,
      "name": "B2 大重機區",
      "space_type": "HEAVY_ONLY",
      "capacity": 20,
      "compatibility": {"status": "ALLOWED", "vehicle": "RED"},
      "rate_summary": {
        "display_text": "20元/小時・最高100元/日",
        "comparison_eligible": true,
        "comparison_hourly_rate_twd": 20,
        "daily_max_twd": 100,
        "parse_status": "PARSED",
        "provenance": {
          "source_id": 7,
          "source_type": "GOVERNMENT",
          "source_updated_at": "2026-10-01T00:00:00Z",
          "fetched_at": "2026-10-02T01:00:00Z",
          "verified_at": null
        }
      },
      "availability": null,
      "rates": [
        {
          "rate_id": 901,
          "rate_type": "HOURLY",
          "currency": "TWD",
          "base_amount": 20,
          "unit_minutes": 60,
          "free_minutes": 0,
          "daily_max_twd": 100,
          "description": "20元/小時，最高100元/日",
          "parse_status": "PARSED",
          "provenance": {
            "source_id": 7,
            "source_type": "GOVERNMENT",
            "source_updated_at": "2026-10-01T00:00:00Z",
            "fetched_at": "2026-10-02T01:00:00Z",
            "verified_at": null
          }
        }
      ]
    }
  ]
}
```

Rates MUST NOT be attached across zones or hide selected-vehicle compatibility context.

## Realtime response
`GET /parking/{id}/realtime?vehicle=RED&at=...`

The response uses the same common zone base. `evaluation_at` controls attached compatibility; realtime freshness remains current.

```json
{
  "parking_id": 12345,
  "vehicle": "RED",
  "evaluation_at": "2026-10-02T09:30:00Z",
  "zones": [
    {
      "zone_id": 20,
      "name": "B2 大重機區",
      "space_type": "HEAVY_ONLY",
      "capacity": 20,
      "compatibility": {"status": "ALLOWED", "vehicle": "RED"},
      "rate_summary": null,
      "availability": {
        "status": "AVAILABLE",
        "available": 8,
        "total": 20,
        "freshness": {"status": "FRESH"},
        "provenance": {
          "source_id": 9,
          "source_type": "OPERATOR",
          "source_updated_at": "2026-10-02T02:00:00Z",
          "fetched_at": "2026-10-02T02:01:00Z",
          "verified_at": null
        }
      }
    }
  ],
  "availability_summary": null
}
```

A FRESH realtime example/fact MUST include non-null `fetched_at` provenance.

## Destination search

Google Places is a geocoding source only. These public endpoints never return or
influence parking compatibility, rates, availability or ranking; clients pass the
resolved coordinates to `GET /parking/nearby`.

`GET /places/autocomplete?input=<1..100 chars>&session_token=<UUID>[&lat=&lng=]`

```json
{
  "suggestions": [
    {"place_id": "ChIJH56c2rarQjQRphD9gvC8BhI", "primary_text": "台北101", "secondary_text": "台灣台北市信義區信義路五段7號"}
  ],
  "attribution": "GOOGLE"
}
```

`GET /places/{place_id}?session_token=<UUID>`

```json
{
  "place_id": "ChIJH56c2rarQjQRphD9gvC8BhI",
  "name": "台北101",
  "address": "110台灣台北市信義區信義路五段7號",
  "location": {"lat": 25.0339639, "lng": 121.5644722},
  "attribution": "GOOGLE"
}
```

- `lat`/`lng` are an optional location bias and must be supplied together.
- Clients reuse one `session_token` for all autocomplete requests and the final
  details request of a selection, then start a new token.
- Errors: `422 VALIDATION_ERROR`, `404 PLACE_NOT_FOUND`, `429 RATE_LIMITED`,
  `502 PLACES_UPSTREAM_ERROR`, `503 PLACES_UNAVAILABLE` (no server key).

See [destination search](destination-search.md).

## Error envelope
Use one JSON error shape with stable machine-readable code, human-readable message, and optional details/field errors.

```json
{
  "error": {
    "code": "INVALID_CURSOR",
    "message": "The pagination cursor is invalid."
  }
}
```

Stable auth codes: `UNAUTHENTICATED` => 401; `FORBIDDEN` => 403.

## OpenAPI
FastAPI-generated OpenAPI is required and must match tests. Protected operations declare the bearer scheme. Breaking v1 contract changes require `/api/v2` rather than silent mutation.

## M3 implementation notes

See [the implementation contract and verification evidence](parking-api-implementation.md)
for the 5000m radius validation limit, signing-key configuration, conservative
rate normalization and component evidence projection. Compatibility adds
`rule_evidence` to preserve all winning rules; the singular `provenance` is
nullable when no single winner exists. Rates add `supporting_sources` and
`applicability=MATCH|UNKNOWN` without omitting any common zone base member.
