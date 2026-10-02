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

## Authentication and authorization
Public parking discovery endpoints are usable without authentication unless an endpoint explicitly states otherwise.

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

The backend derives identity only from the validated token. Clients MUST NOT choose `/me` or `/favorites` ownership by supplying an arbitrary user ID.

- missing/malformed/expired/revoked/invalid credentials => HTTP 401, code `UNAUTHENTICATED`
- valid identity without required permission => HTTP 403, code `FORBIDDEN`
- favorites are always scoped to the authenticated user
- guest mode may use public parking/search/detail APIs but not protected user endpoints

Protected operations MUST declare the bearer security scheme in OpenAPI.

## Vehicle and rule-evaluation context
Selected-vehicle endpoints require explicit `vehicle`; v1 mobile/public parking flows support `YELLOW` and `RED`.

Required on:
- `GET /parking/nearby`
- `GET /parking/{id}`
- `GET /parking/{id}/rates`
- `GET /parking/{id}/realtime`

The server MUST NOT infer the selected vehicle from login/profile state.

Scheduled parking rules/rates are evaluated at one explicit absolute instant, `evaluation_at`.

- Clients may supply optional `at=<RFC3339 timestamp with offset>` on nearby/detail/rates requests.
- If `at` is omitted on the first nearby page, the server captures the request-received instant as `evaluation_at`.
- The nearby response exposes that resolved `evaluation_at`.
- Every continuation cursor is bound to the same `evaluation_at`; subsequent pages MUST NOT re-evaluate weekday/weekend/day/night rules using a newer clock time.
- Supplying an `at` that conflicts with a cursor's pinned instant is a cursor/query mismatch.
- Detail/rates responses likewise expose the resolved evaluation instant; when omitted they use that request's receive instant.
- MVP rule/rate local-time interpretation is `Asia/Taipei` because v1 geography is Taipei/New Taipei. Convert the absolute evaluation instant to `Asia/Taipei` before applying weekday/weekend/holiday/time-of-day rules.

Realtime freshness remains a current-observation concept and is evaluated by server/source freshness policy; `evaluation_at` does not make stale realtime historical data current.

## Provenance
Rule/compatibility, rate, realtime, and entrance facts may come from different datasets and MUST retain separate provenance.

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

Do not substitute an unrelated component's timestamp. For realtime, non-null `fetched_at` is required before freshness may be `FRESH`.

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
For YELLOW/RED search, `space_type` may narrow compatible results to:
- `HEAVY_ONLY`
- `MOTO_SHARED`
- `CAR_SHARED`

`LIGHT_MOTO_ONLY` is known `NOT_ALLOWED` for YELLOW/RED and is not a normal v1 parking-search filter. `include_unknown=true` includes compatibility `UNKNOWN`, never known `NOT_ALLOWED` locations. Any future prohibited-location browsing is a separate discovery mode.

## Compatibility
Zone compatibility is three-state:
- `ALLOWED`
- `NOT_ALLOWED`
- `UNKNOWN`

`UNKNOWN` MUST NOT be coerced to true/false.

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

Thus ALLOWED+UNKNOWN => lot ALLOWED while the child UNKNOWN remains UNKNOWN.

## Realtime model
Availability observation status and freshness are separate dimensions.

Availability status:
- `AVAILABLE`
- `FULL`
- `UNKNOWN`
- `CLOSED`

Freshness:
- `FRESH`
- `STALE`
- `UNKNOWN`

`STALE` is not an availability status. Aging preserves the last observation status and changes only freshness.

Freshness rules:
- `FRESH` requires non-null `fetched_at` and compliance with source/server freshness policy
- `STALE` means an observation exists but is too old for current-availability claims
- `UNKNOWN` includes missing/indeterminable fetch time

Realtime is zone-scoped whenever the source supports zone-level facts.

### Numeric integrity
Raw/source observations may be incomplete. A trustworthy numeric observation must satisfy:
- `available` is an integer >= 0 when present
- `total` is an integer >= 0 when present
- when both are present, `available <= total`
- `AVAILABLE` requires confirmed `available > 0`
- `FULL` requires confirmed `available = 0`
- `CLOSED` requires confirmed `available = 0`

Impossible/invalid counts remain raw ingestion evidence but are not trustworthy normalized claims.

## `available_only`
`available_only=true` qualifies a lot only when at least one otherwise-matching zone has:
- compatibility `ALLOWED`
- availability status `AVAILABLE`
- confirmed integer `available > 0`
- freshness `FRESH`
- non-null `fetched_at`

`total` may be null for this filter. However, **when `total` is supplied**, it MUST be a nonnegative integer and `available <= total`; otherwise the observation is internally invalid and does not qualify.

The following do not qualify:
- FULL / UNKNOWN / CLOSED status
- AVAILABLE with `available=0`
- invalid known total (`total<0`, non-integer, or `available>total`)
- stale/unknown/missing freshness
- missing realtime/fetched_at
- nonmatching or non-ALLOWED zone

`available_only=false` simply does not filter by current availability.

## Lot-level availability summary
`availability_summary` is derived only from returned ALLOWED zones. It MUST NOT include car-only, light-motorcycle-only, NOT_ALLOWED, or compatibility-UNKNOWN counts.

A zone is a trustworthy COMPLETE-coverage contributor only when:
- realtime status is AVAILABLE / FULL / CLOSED (never UNKNOWN)
- `available` and `total` are confirmed nonnegative integers
- `available <= total`
- status/count requirements above hold
- freshness is FRESH
- `fetched_at` is non-null

A row may preserve `total=null` from upstream, but it cannot participate in COMPLETE numeric lot coverage.

Define:
- `eligible_zone_count`: returned ALLOWED zones
- `fresh_realtime_zone_count`: eligible zones satisfying all COMPLETE contributor requirements

Coverage:
- `COMPLETE`: eligible > 0 and fresh count == eligible count
- `PARTIAL`: 0 < fresh count < eligible count
- `NONE`: fresh count == 0

PARTIAL/NONE:
- summary `status=UNKNOWN`
- `available=null`
- `total=null`
- client MUST NOT display a partial child-zone sum as a lot total

Only COMPLETE may expose numeric lot totals.

### COMPLETE status
After validating all contributors:
1. `AVAILABLE` if summed available > 0.
2. Else `CLOSED` if every contributor is CLOSED.
3. Else `FULL` if summed available = 0, at least one contributor is FULL, and all others are FULL/CLOSED.
4. Else `UNKNOWN` defensively.

Examples:
- AVAILABLE + FULL => AVAILABLE
- AVAILABLE + CLOSED => AVAILABLE
- FULL + FULL => FULL
- FULL + CLOSED => FULL
- CLOSED + CLOSED => CLOSED

Clients consume this server-derived status rather than re-derive it.

### COMPLETE freshness/provenance
For COMPLETE coverage:
- aggregate freshness = FRESH
- `oldest_source_updated_at` = minimum contributor value only if every contributor supplies it; otherwise null
- `oldest_fetched_at` = minimum contributor fetched_at
- `contributing_sources` contains deduplicated provenance covering every contributor and is never empty when contributors exist

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
    "contributing_sources": [
      {
        "source_id": 9,
        "source_type": "OPERATOR",
        "source_updated_at": "2026-10-02T02:00:00Z",
        "fetched_at": "2026-10-02T02:01:00Z",
        "verified_at": null
      }
    ]
  }
}
```

## Rate filtering
`price_max` is intentionally absent from v1 because it is ambiguous across rate models.

`hourly_rate_max_twd` compares only a confirmed deterministic hourly-equivalent value for the selected vehicle/zone **at the pinned evaluation_at**.

Eligible examples:
- FREE => 0 TWD/hour
- simple confirmed time-unit rate exactly normalizable to one hour

Ineligible without a future explicit normalization rule:
- progressive
- per-entry
- daily/monthly/custom
- conflicting time schedules
- partially parsed/raw-only

A zone qualifies only when `comparison_eligible=true` and its comparison value <= threshold. Never guess.

`daily_max_required=true` requires a confirmed daily cap for at least one otherwise-matching ALLOWED zone at the same evaluation instant.

## Nearby processing and ranking
1. Resolve/pin `evaluation_at`; convert it to Asia/Taipei for scheduled rules/rates.
2. Resolve effective rules for selected vehicle at that instant.
3. Apply compatibility at zone level.
4. Apply space type, realtime/operating, rate, and request filters.
5. Retain matching zones per lot; never substitute nonmatching-zone facts.
6. Derive lot-level compatibility from retained zones.
7. Rank confirmed ALLOWED lots using returned ALLOWED-zone facts only. UNKNOWN-zone price/availability/confidence MUST NOT improve or worsen an ALLOWED lot.
8. With `include_unknown=true`, unknown-only lots form a separate unverified group after confirmed lots; v1 orders them by distance then stable parking ID and gives no confirmed price/availability boosts.
9. Apply keyset pagination using the pinned evaluation instant and deterministic order.

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
      "zones": [],
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
Nearby uses opaque keyset pagination.

- client MUST NOT parse/modify cursors
- cursor is bound to location, radius, vehicle, filters, **evaluation_at**, and sort version/keys
- first page without `at` pins server request-received time
- continuation uses cursor-pinned evaluation_at rather than a new wall-clock time
- changed query or conflicting `at` + old cursor => cursor error
- deterministic sort includes stable parking ID tie-breaker

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

Cursor errors MUST NOT silently restart page one.

## Parking detail
`GET /parking/{id}?vehicle=RED&at=2026-10-02T09:30:00Z`

`at` is optional; response exposes resolved `evaluation_at`. Lot coordinates are separate from entrance coordinates and selected-vehicle facts stay zone-scoped.

```json
{
  "id": 12345,
  "vehicle": "RED",
  "evaluation_at": "2026-10-02T09:30:00Z",
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

Entrance heavy-motorcycle accessibility is ALLOWED / NOT_ALLOWED / UNKNOWN.

Navigation:
1. Prefer ALLOWED entrance.
2. UNKNOWN may be shown as unverified, never confirmed accessible.
3. NOT_ALLOWED is never selected for heavy-motorcycle navigation.
4. Lot center is an explicit fallback only when no usable confirmed entrance coordinate exists.

## Rates and realtime detail
`GET /parking/{id}/rates?vehicle=RED&at=...` uses the same evaluation_at/timezone semantics for scheduled rates.

`GET /parking/{id}/realtime?vehicle=RED` retains zone IDs, selected-vehicle scope, component provenance, and the same availability/freshness model. Realtime freshness is evaluated as current data, not rewound by `at`.

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

Stable auth codes:
- `UNAUTHENTICATED` => HTTP 401
- `FORBIDDEN` => HTTP 403

## OpenAPI
FastAPI-generated OpenAPI is required and must match tests. Protected operations declare the bearer scheme. Breaking v1 contract changes require `/api/v2` rather than silent mutation.
