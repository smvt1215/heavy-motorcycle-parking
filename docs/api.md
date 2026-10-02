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
`lat`, `lng`, `radius`, `vehicle`, `space_type`, `available_only`, `price_max`, `daily_max_required`, `limit`, `cursor`.
Supported MVP radii: 500m / 1km / 3km / 5km.

## Nearby semantics
1. Determine effective vehicle compatibility.
2. Exclude NOT_ALLOWED; UNKNOWN behavior must be explicit in response/filter policy.
3. Apply operating/realtime/filter conditions.
4. Rank applicable results.

Response summary includes id, name, location, distance, space types, yellow/red compatibility, availability with freshness, rate summary, provenance, update time.

## Error format
Use one consistent JSON error envelope with stable machine-readable code, human-readable message, and optional details/field errors.

## OpenAPI
FastAPI-generated OpenAPI is required and must remain consistent with tests. Breaking changes require `/api/v2` rather than silently changing v1 contracts.
