# REST API v1

Base path: `/api/v1`.

## Endpoints
- `GET /health`
- `GET /version`
- `GET /parking/nearby`
- `GET /parking/{id}`
- `GET /parking/{id}/rates`
- `GET /parking/{id}/realtime`
- `GET /parking/{id}/reports`
- `POST /reports`
- `GET /favorites`
- `POST /favorites`
- `DELETE /favorites/{parking_id}`
- `GET /me`

## Vehicle context
Selected-vehicle endpoints require an explicit `vehicle` query parameter.

For v1 public/mobile parking flows, supported values are `YELLOW` and `RED`.

Required on:
- `GET /parking/nearby`
- `GET /parking/{id}`
- `GET /parking/{id}/rates`
- `GET /parking/{id}/realtime`

The server MUST NOT infer a selected vehicle from login/profile state. Missing vehicle context is a validation error so guest, authenticated, and deep-link flows remain deterministic.

## Provenance
Facts that can come from different upstream datasets retain their own provenance. Rule/compatibility, rate, realtime, and entrance provenance MUST NOT be collapsed into one lot-level source.

```json
{
  "provenance": {
    "source_id": 12,
    "source_type": "GOVERNMENT",
    "source_updated_at": "2026-10-02T02:00:00Z",
    "fetched_at": "2026-10-02T02:01:00Z",
    "verified_at": null
  }
}
```

A timestamp may be null when the source does not provide it. The API MUST NOT substitute an unrelated component's timestamp.

For realtime facts, non-null `fetched_at` is required before freshness may be `FRESH`.

## Nearby request
`GET /parking/nearby`

Required:
- `lat`
- `lng`
- `vehicle`

Optional:
- `radius`
- `space_type`
- `available_only`
- `hourly_rate_max_twd`
- `daily_max_required`
- `include_unknown`
- `limit`
- `cursor`

Defaults:
- `radius=1500`
- `available_only=false`
- `include_unknown=false`
- `limit=20`
- maximum `limit=100`

Supported MVP radius presets: 500m / 1km / 3km / 5km.

### Space-type filtering
For YELLOW/RED parking search, `space_type` may narrow otherwise-compatible results to:
- `HEAVY_ONLY`
- `MOTO_SHARED`
- `CAR_SHARED`

`LIGHT_MOTO_ONLY` is known `NOT_ALLOWED` for YELLOW/RED and is not a normal v1 parking-search filter. The API has no generic override to include confirmed prohibited locations.

`include_unknown=true` is only for compatibility `UNKNOWN`; it does not include `NOT_ALLOWED` zones.

If a future product intentionally browses prohibited/non-parking locations, it must use a separately specified discovery mode rather than changing legal parking-search semantics.

## Compatibility
Compatibility is three-state:
- `ALLOWED`: selected vehicle is confirmed allowed.
- `NOT_ALLOWED`: selected vehicle is confirmed prohibited.
- `UNKNOWN`: permission cannot currently be verified.

`UNKNOWN` MUST NOT be coerced to true/false.

Nearby policy:
1. `ALLOWED` zones are eligible for normal results and ranking.
2. `NOT_ALLOWED` zones are excluded.
3. `UNKNOWN` zones are excluded by default.
4. With `include_unknown=true`, `UNKNOWN` zones may be returned but remain explicitly `UNKNOWN` and must be presented as unverified.
5. Compatibility filtering occurs before recommendation ranking.

```json
{
  "compatibility": {
    "status": "UNKNOWN",
    "vehicle": "RED",
    "reason": "vehicle_permission_not_verified",
    "confidence": null,
    "provenance": null
  }
}
```

## Realtime availability model
Availability observation status and freshness are separate dimensions.

### Availability status
Allowed values:
- `AVAILABLE`
- `FULL`
- `UNKNOWN`
- `CLOSED`

`STALE` is **not** an availability status.

When an observation ages, retain its last observed status and change only freshness. Example: an old observation can be `status=AVAILABLE` with `freshness.status=STALE`.

### Freshness status
Allowed values:
- `FRESH`
- `STALE`
- `UNKNOWN`

Rules:
- `FRESH` requires non-null `fetched_at` and must satisfy the server/source freshness policy.
- `STALE` means the observation exists but is too old for current-availability claims.
- `UNKNOWN` means freshness cannot be established, including missing `fetched_at`.

Freshness thresholds are server/source configuration, not a client contract.

### Zone-scoped realtime
Realtime belongs to a parking zone whenever the source exposes zone-level data.

```json
{
  "availability": {
    "status": "AVAILABLE",
    "available": 8,
    "total": 20,
    "freshness": {
      "status": "FRESH"
    },
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

Trustworthy current numeric invariants:
- `AVAILABLE` => confirmed `available > 0`.
- `FULL` => confirmed `available = 0`.
- `CLOSED` => confirmed `available = 0`; known physical `total` may remain present.

A record violating these invariants cannot contribute to a COMPLETE lot-level aggregate.

## `available_only`
`available_only=true` is conservative.

A lot qualifies only when at least one otherwise-matching zone has all of:
- compatibility `ALLOWED`
- availability status `AVAILABLE`
- confirmed integer `available > 0`
- freshness `FRESH`
- non-null realtime `fetched_at`

The following do not qualify:
- `FULL`, `UNKNOWN`, or `CLOSED` availability
- status `AVAILABLE` with `available=0`
- freshness `STALE` or `UNKNOWN`
- missing freshness/realtime/fetched_at
- availability from a nonmatching or non-ALLOWED zone

`available_only=false` does not assert current availability; it simply does not filter by realtime.

## Lot-level availability summary
`availability_summary` is a derived API projection over returned `ALLOWED` zones. It MUST NOT include car-only, light-motorcycle-only, `NOT_ALLOWED`, or compatibility-`UNKNOWN` zone counts.

Define:
- `eligible_zone_count`: returned `ALLOWED` zones in the selected-vehicle result.
- `fresh_realtime_zone_count`: those zones with trustworthy numeric realtime, freshness `FRESH`, and non-null `fetched_at`.

Coverage:
- `COMPLETE`: eligible > 0 and fresh count == eligible count.
- `PARTIAL`: 0 < fresh count < eligible count.
- `NONE`: fresh count == 0.

For `PARTIAL` or `NONE`:
- summary status = `UNKNOWN`
- `available=null`
- `total=null`
- client MUST NOT present a partial child-zone sum as a complete lot total

Only `COMPLETE` may expose numeric lot totals.

### COMPLETE aggregate status
After validating all contributing zone observations:
1. `AVAILABLE` if summed `available > 0`.
2. Else `CLOSED` if every contributor is `CLOSED`.
3. Else `FULL` if available sum is 0, at least one contributor is `FULL`, and all others are `FULL` or `CLOSED`.
4. Else `UNKNOWN` defensively.

Examples:
- AVAILABLE + FULL => AVAILABLE
- AVAILABLE + CLOSED => AVAILABLE
- FULL + FULL => FULL
- FULL + CLOSED => FULL
- CLOSED + CLOSED => CLOSED

Clients MUST consume the server-derived aggregate status rather than re-derive it.

### COMPLETE aggregate freshness/provenance
For COMPLETE coverage:
- aggregate freshness = `FRESH`
- `oldest_source_updated_at` = minimum contributor value only if every contributor supplies it; otherwise null
- `oldest_fetched_at` = minimum contributor `fetched_at`; it is non-null because non-null fetch time is required for COMPLETE coverage
- `contributing_sources` contains deduplicated provenance for every contributor

Do not select one child source/timestamp and represent it as the whole aggregate.

Example:

```json
{
  "availability_summary": {
    "status": "AVAILABLE",
    "available": 8,
    "total": 20,
    "scope": "ALLOWED_RETURNED_ZONES",
    "coverage": {
      "status": "COMPLETE",
      "eligible_zone_count": 1,
      "fresh_realtime_zone_count": 1
    },
    "freshness": {
      "status": "FRESH",
      "oldest_source_updated_at": "2026-10-02T02:00:00Z",
      "oldest_fetched_at": "2026-10-02T02:01:00Z"
    },
    "contributing_sources": []
  }
}
```

## Rate filtering
`price_max` is intentionally not part of v1 because a generic scalar is ambiguous across progressive, per-entry, daily, monthly, custom, or partially parsed rates.

`hourly_rate_max_twd` compares only a confirmed deterministic hourly-equivalent value for the selected vehicle and zone.

A rate summary may expose:

```json
{
  "rate_summary": {
    "display_text": "20元/小時・最高100元/日",
    "comparison_eligible": true,
    "comparison_hourly_rate_twd": 20,
    "daily_max_twd": 100,
    "parse_status": "PARSED",
    "provenance": {}
  }
}
```

Eligible examples:
- `FREE` => 0 TWD/hour
- simple confirmed time-unit rate that converts exactly to one hour

Not eligible without a future explicit normalization rule:
- progressive
- per-entry
- daily/monthly/custom
- conflicting time schedules
- partially parsed / raw-only

When `hourly_rate_max_twd` is supplied, a zone qualifies only when `comparison_eligible=true` and its comparison value is <= threshold. Do not guess.

`daily_max_required=true` requires a confirmed daily cap for at least one otherwise-matching `ALLOWED` zone.

## Nearby processing order
1. Resolve effective rules for selected vehicle/time.
2. Apply compatibility at zone level.
3. Apply space-type, operating/realtime, price, and other request filters to zones.
4. Retain matching zones per lot; never substitute facts from nonmatching zones.
5. Rank remaining applicable lots using selected-vehicle/matching-zone facts only.
6. Apply keyset pagination.

## Nearby response
Nearby is lot-oriented for map/list efficiency but preserves the zone boundary.

```json
{
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
          "compatibility": {},
          "availability": {},
          "rate_summary": {}
        }
      ],
      "availability_summary": {}
    }
  ],
  "page": {
    "next_cursor": "opaque-token",
    "has_more": true
  }
}
```

## Pagination
Nearby uses opaque keyset pagination, not offset pagination.

- clients MUST NOT parse or modify cursors
- cursors are bound to the effective query, including location, radius, vehicle, and filters
- changed effective query + old cursor => cursor error
- deterministic sort requires a stable tie-breaker such as parking ID

Final page:

```json
{
  "page": {
    "next_cursor": null,
    "has_more": false
  }
}
```

Stable cursor errors:
- `INVALID_CURSOR`
- `CURSOR_QUERY_MISMATCH`
- `CURSOR_VERSION_UNSUPPORTED`

Cursor errors MUST NOT silently restart on page one.

## Parking detail
`GET /parking/{id}?vehicle=RED`

The response exposes lot coordinates separately from entrance coordinates and keeps selected-vehicle facts at zone level.

```json
{
  "id": 12345,
  "vehicle": "RED",
  "name": "XX地下停車場",
  "location": {"lat": 25.0331, "lng": 121.5628},
  "zones": [],
  "entrances": [
    {
      "id": 301,
      "name": "忠孝東路入口",
      "location": {"lat": 25.0333, "lng": 121.5625},
      "entrance_type": "VEHICLE",
      "heavy_motorcycle_access": "ALLOWED",
      "notes": null,
      "provenance": {}
    }
  ]
}
```

Entrance heavy-motorcycle accessibility is tri-state:
- `ALLOWED`
- `NOT_ALLOWED`
- `UNKNOWN`

Navigation behavior:
1. Prefer an entrance with access `ALLOWED`.
2. `UNKNOWN` may be shown as unverified but never as confirmed accessible.
3. `NOT_ALLOWED` must never be selected as the heavy-motorcycle navigation target.
4. Lot-center navigation is an explicit fallback only when no usable confirmed entrance coordinate exists.

## Rates and realtime detail
`GET /parking/{id}/rates?vehicle=RED` and `GET /parking/{id}/realtime?vehicle=RED` retain zone IDs, selected-vehicle scope, and component provenance.

The realtime endpoint uses the same separation of availability status (`AVAILABLE/FULL/UNKNOWN/CLOSED`) and freshness (`FRESH/STALE/UNKNOWN`) defined above.

## Error envelope
Use one consistent JSON error shape with stable machine-readable code, human-readable message, and optional details/field errors.

```json
{
  "error": {
    "code": "INVALID_CURSOR",
    "message": "The pagination cursor is invalid."
  }
}
```

## OpenAPI
FastAPI-generated OpenAPI is required and must remain consistent with tests. Breaking changes require `/api/v2` rather than silently changing v1 contracts.
