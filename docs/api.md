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

## Nearby parameters
Required: `lat`, `lng`, `vehicle`.

Optional: `radius`, `space_type`, `available_only`, `price_max`, `daily_max_required`, `include_unknown`, `limit`, `cursor`.

Defaults and limits:
- `radius`: default 1500m. Supported MVP presets: 500m / 1km / 3km / 5km.
- `include_unknown`: default `false`.
- `limit`: default 20, maximum 100.
- `cursor`: opaque continuation token returned by the previous page. Clients MUST NOT parse or modify it.

## Vehicle compatibility semantics
Compatibility is a first-class three-state result:
- `ALLOWED`: the selected vehicle is confirmed allowed by the effective rule.
- `NOT_ALLOWED`: the selected vehicle is confirmed not allowed by the effective rule.
- `UNKNOWN`: the selected vehicle permission is not verified or cannot be determined from authoritative data.

`UNKNOWN` MUST NOT be coerced to `NOT_ALLOWED` or `ALLOWED`.

For `GET /parking/nearby` in v1:
1. `ALLOWED` results are eligible for the normal result set and ranking.
2. `NOT_ALLOWED` results are excluded from normal results.
3. `UNKNOWN` results are excluded by default when `include_unknown=false`.
4. When `include_unknown=true`, `UNKNOWN` results may be returned but MUST retain `compatibility.status = "UNKNOWN"`; clients must present them as unverified, never as legal parking.
5. Legality/compatibility filtering happens before recommendation ranking.

Example compatibility payload:

```json
{
  "compatibility": {
    "status": "UNKNOWN",
    "vehicle": "RED",
    "reason": "vehicle_permission_not_verified",
    "source": null,
    "confidence": null
  }
}
```

## Nearby processing order
1. Resolve effective parking rules for the selected vehicle and request time.
2. Apply compatibility policy above.
3. Apply operating status, realtime, space-type, price and other request filters.
4. Rank remaining applicable results.
5. Apply keyset pagination.

## Nearby pagination
Nearby results use opaque keyset pagination, not offset pagination.

The server owns the cursor format. A cursor may internally encode versioned sort keys and a query fingerprint, but that representation is an implementation detail and MUST NOT be exposed as a client contract.

A cursor is valid only for the same effective query (including location, radius, vehicle and filters) that produced it. Reusing a cursor with materially different query parameters MUST return a cursor error rather than silently restarting pagination.

The sort order used to produce the cursor must be deterministic and include a stable tie-breaker such as parking ID.

Example response:

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
        "vehicle": "RED",
        "space_types": ["HEAVY_ONLY"],
        "reason": "explicit_vehicle_permission"
      },
      "availability": {
        "status": "AVAILABLE",
        "available": 8,
        "total": 20,
        "source_updated_at": "2026-10-02T02:00:00Z"
      },
      "rate_summary": {
        "display_text": "20元/小時・最高100元/日"
      },
      "data_status": {
        "source_type": "GOVERNMENT",
        "updated_at": "2026-10-02T02:00:00Z"
      }
    }
  ],
  "page": {
    "next_cursor": "opaque-token",
    "has_more": true
  }
}
```

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

Response summaries must retain explicit compatibility state, location/distance, parking space types, availability with freshness, rate summary, provenance and update time.

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
