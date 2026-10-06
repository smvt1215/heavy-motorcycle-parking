# Architecture

## Stack
- Mobile: Flutter stable, Dart, Riverpod, go_router, Dio, Freezed/json_serializable
- Map: Google Maps SDK
- Search: Google Places API (New)
- Navigation MVP: Apple Maps / Google Maps deep link
- Backend: Python, FastAPI, Pydantic, SQLAlchemy 2, Alembic
- DB: PostgreSQL 18 + PostGIS 3.6+
- Cache: Redis
- Object storage: S3-compatible
- CI/CD: GitHub Actions
- Local infra: Docker Compose

## System boundaries
Google Maps/Places answers where a place is. Government/operator/community sources describe parking data. Our Rule Engine answers whether the selected vehicle may park there.

```text
Flutter
  -> HTTPS REST /api/v1
FastAPI
  -> domain services / repositories
PostgreSQL + PostGIS
Redis
Workers -> raw source -> adapter -> normalizer -> validator -> rule mapping -> DB
```

## Mobile architecture
`app/`, `core/`, `design_system/`, `features/`, `domain/`, `data/`.
Shared business logic; platform-adaptive iOS/Android presentation.

M5 [Flutter map experience](flutter-map.md) connects typed parking DTOs and a Dio
repository to a persistent Riverpod map controller. Camera updates are committed
only by explicit area search; pinned queries and request generations preserve
selected-vehicle and pagination context. The map and zone panel render separate
rule/rate/realtime/entrance facts and use foreground-only location plus external
navigation.

## Backend architecture
Router -> Service/Domain -> Repository -> DB. Routers must not contain business rules or raw SQL.

The M2 [compatibility engine](compatibility-engine.md) consumes preloaded domain
facts through `ParkingCompatibilityService.evaluate`. It has no ORM/HTTP/UI
dependency; repositories map normalized database evidence to explicit rule
inputs, and holiday coverage is injected as a local calendar snapshot.

## GIS
Store geospatial points as `GEOGRAPHY(POINT,4326)`. Nearby search uses `ST_DWithin` and GiST indexes. Never fetch all rows and calculate distance in Python.

M3 [parking API implementation](parking-api-implementation.md) batches indexed
spatial candidates into domain snapshots, resolves selected-vehicle zone facts,
and applies versioned ranking/keyset cursors. Request time pins compatibility
and schedules; realtime freshness remains current. Common zone response models
preserve independent compatibility, rate, realtime and entrance evidence.

## Caching

M4's [Taipei ingestion worker](taipei-ingestion.md) lives in `app/ingestion`:
HTTPX downloader -> independently committed raw evidence -> pure city adapter
-> source-owned PostgreSQL writer with per-record SAVEPOINTs. A shared transaction
advisory lock coordinates both Taipei feeds; Redis holds import-status metadata.
This worker runs outside API routers and does not change domain precedence.
Redis only for nearby/detail/realtime cache and rate limiting initially. TTL should reflect source freshness, normally 30–120 seconds for realtime-derived responses.

## Privacy/security
Request location only while in use. No background GPS trajectory in MVP. HTTPS, validation, rate limits, RBAC for admin workflows, secrets outside repo, restricted Google API keys.
