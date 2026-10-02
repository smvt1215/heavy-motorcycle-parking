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

## Common provenance object
Facts that can come from different upstream datasets MUST retain their own provenance. Do not collapse rule, rate, realtime, or entrance provenance into one lot-level source field.

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

A component may have `null` timestamps when its source does not provide them, but the API MUST NOT substitute an unrelated component's timestamp.

## Nearby parameters
Required: `lat`, `lng`, `vehicle`.

Optional: `radius`, `space_type`, `available_only`, `hourly_rate_max_twd`, `daily_max_required`, `include_unknown`, `limit`, `cursor`.

Defaults and limits:
- `radius`: default 1500m. Supported MVP presets: 500m / 1km / 3km / 5km.
- `include_unknown`: default `false`.
- `limit`: default 20, maximum 100.
- `cursor`: opaque continuation token returned by the previous page. Clients MUST NOT parse or modify it.

`price_max` is intentionally not part of the v1 contract because a generic scalar is ambiguous for progressive, per-entry, daily, monthly, custom, or partially parsed rates.

## Rate-filter semantics
`hourly_rate_max_twd` compares only a confirmed, deterministic hourly-equivalent value for the selected vehicle and zone.

The server may set `comparison_hourly_rate_twd` only when normalization requires no assumptions. Examples:
- `FREE`: eligible, comparison value `0`.
- A simple confirmed hourly/unit-time rate that can be exactly normalized to one hour: eligible.
- Progressive, per-entry, daily, monthly, custom, conflicting time-schedule, or partially parsed/raw-only rates: not eligible unless a future version defines an unambiguous normalization rule.

When `hourly_rate_max_twd` is supplied:
1. A parking zone qualifies only if `rate_summary.comparison_eligible=true`.
2. `comparison_hourly_rate_twd <= hourly_rate_max_twd` must hold.
3. A lot qualifies if at least one otherwise-matching `ALLOWED` zone qualifies.
4. Unknown/ineligible rates MUST NOT be guessed or coerced to pass the filter.

`daily_max_required=true` means at least one otherwise-matching `ALLOWED` zone has a confirmed `daily_max_twd` for the selected vehicle. It does not infer a daily cap from raw rate text.

## Vehicle compatibility semantics
Compatibility is a first-class three-state result:
- `ALLOWED`: the selected vehicle is confirmed allowed by the effective rule.
- `NOT_ALLOWED`: the selected vehicle is confirmed not allowed by the effective rule.
- `UNKNOWN`: the selected vehicle permission is not verified or cannot be determined from authoritative data.

`UNKNOWN` MUST NOT be coerced to `NOT_ALLOWED` or `ALLOWED`.

For `GET /parking/nearby` in v1:
1. `ALLOWED` zones are eligible for the normal result set and ranking.
2. `NOT_ALLOWED` zones are excluded from normal results.
3. `UNKNOWN` zones are excluded by default when `include_unknown=false`.
4. When `include_unknown=true`, `UNKNOWN` zones may be returned but MUST retain `compatibility.status = "UNKNOWN"`; clients must present them as unverified, never as legal parking.
5. Lot-level compatibility is `ALLOWED` if at least one returned zone is `ALLOWED`; otherwise it may be `UNKNOWN` only when unknown results were explicitly requested.
6. Legality/compatibility filtering happens before recommendation ranking.

Example compatibility payload:

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

## Zone-scoped availability
Availability is a property of a parking zone, not implicitly of the whole lot.

Each returned zone may include:

```json
{
  "zone_id": 20,
  "name": "B2 大重機區",
  "space_type": "HEAVY_ONLY",
  "compatibility": {
    "status": "ALLOWED",
    "vehicle": "RED",
    "reason": "explicit_vehicle_permission",
    "provenance": {
      "source_id": 4,
      "source_type": "GOVERNMENT",
      "source_updated_at": "2026-10-02T01:50:00Z",
      "fetched_at": "2026-10-02T01:51:00Z",
      "verified_at": null
    }
  },
  "availability": {
    "status": "AVAILABLE",
    "available": 8,
    "total": 20,
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

A lot-level `availability_summary`, when present, MUST aggregate only returned zones with `compatibility.status = ALLOWED` and zone-scoped realtime data. It MUST NOT include car-only, light-motorcycle-only, `NOT_ALLOWED`, or merely `UNKNOWN` zone counts.

If no `ALLOWED` returned zone has trustworthy zone-scoped realtime data, the lot-level availability summary MUST be `UNKNOWN` rather than borrowing counts from another zone.

## Rate summary and provenance
Rate summaries are zone- and vehicle-scoped. A returned zone may contain:

```json
{
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
  }
}
```

Compatibility, rate, availability, and entrance components may legitimately reference different sources and timestamps.

## Nearby processing order
1. Resolve effective parking rules for the selected vehicle and request time.
2. Apply compatibility policy above at zone level.
3. Apply operating status, realtime, space-type, rate and other request filters to candidate zones.
4. Retain the matching zones for each lot; do not substitute data from non-matching zones.
5. Rank remaining applicable lots using only facts valid for the selected vehicle/matching zones.
6. Apply keyset pagination.

## Nearby response
Nearby is lot-oriented for map/list efficiency, but it retains the zone boundary through `zones`.

```json
{
  "items": [
    {
      "id": 12345,
      "name": "XX地下停車場",
      "distance_m": 420,
      "location": {
        "lat": 25.0331,
        "lng": 121.5628
      },
      "compatibility": {
        "status": "ALLOWED",
        "vehicle": "RED"
      },
      "zones": [
        {
          "zone_id": 20,
          "name": "B2 大重機區",
          "space_type": "HEAVY_ONLY",
          "compatibility": {
            "status": "ALLOWED",
            "vehicle": "RED",
            "reason": "explicit_vehicle_permission",
            "provenance": {
              "source_id": 4,
              "source_type": "GOVERNMENT",
              "source_updated_at": "2026-10-02T01:50:00Z",
              "fetched_at": "2026-10-02T01:51:00Z",
              "verified_at": null
            }
          },
          "availability": {
            "status": "AVAILABLE",
            "available": 8,
            "total": 20,
            "provenance": {
              "source_id": 9,
              "source_type": "OPERATOR",
              "source_updated_at": "2026-10-02T02:00:00Z",
              "fetched_at": "2026-10-02T02:01:00Z",
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
          }
        }
      ],
      "availability_summary": {
        "status": "AVAILABLE",
        "available": 8,
        "total": 20,
        "scope": "ALLOWED_RETURNED_ZONES"
      }
    }
  ],
  "page": {
    "next_cursor": "opaque-token",
    "has_more": true
  }
}
```

## Nearby pagination
Nearby results use opaque keyset pagination, not offset pagination.

The server owns the cursor format. A cursor may internally encode versioned sort keys and a query fingerprint, but that representation is an implementation detail and MUST NOT be exposed as a client contract.

A cursor is valid only for the same effective query (including location, radius, vehicle and filters) that produced it. Reusing a cursor with materially different query parameters MUST return a cursor error rather than silently restarting pagination.

The sort order used to produce the cursor must be deterministic and include a stable tie-breaker such as parking ID.

On the final page:

```json
{
  "items": [],
  "page": {
    "next_cursor": null,
    "has_more": false
  }
}
```

## Parking detail response
`GET /parking/{id}` MUST expose lot coordinates separately from entrance coordinates and retain zone-level facts.

At minimum:

```json
{
  "id": 12345,
  "name": "XX地下停車場",
  "location": {
    "lat": 25.0331,
    "lng": 121.5628
  },
  "zones": [],
  "entrances": [
    {
      "id": 301,
      "name": "忠孝東路入口",
      "location": {
        "lat": 25.0333,
        "lng": 121.5625
      },
      "entrance_type": "VEHICLE",
      "heavy_motorcycle_access": true,
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

Navigation clients MUST prefer an entrance with `heavy_motorcycle_access=true`. If no confirmed heavy-motorcycle-accessible entrance exists, the UI must not imply that an unverified entrance is confirmed accessible. Falling back to the lot center is permitted only when no usable entrance coordinate exists, and should be identifiable as a fallback in client logic.

## Cursor errors
At minimum, v1 defines these stable error codes:
- `INVALID_CURSOR`: malformed, invalid, tampered or otherwise unusable cursor.
- `CURSOR_QUERY_MISMATCH`: cursor does not belong to the current effective query.
- `CURSOR_VERSION_UNSUPPORTED`: cursor version is no longer supported.

Cursor errors MUST NOT silently fall back to the first page, because doing so can create duplicate or misleading results.

## Error format
Use one consistent JSON error envelope with stable machine-readable code, human-readable message, and optional details/field errors.

Example:

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
