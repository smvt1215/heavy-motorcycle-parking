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

## Backend architecture
Router -> Service/Domain -> Repository -> DB. Routers must not contain business rules or raw SQL.

## GIS
Store geospatial points as `GEOGRAPHY(POINT,4326)`. Nearby search uses `ST_DWithin` and GiST indexes. Never fetch all rows and calculate distance in Python.

## Caching
Redis only for nearby/detail/realtime cache and rate limiting initially. TTL should reflect source freshness, normally 30–120 seconds for realtime-derived responses.

## Privacy/security
Request location only while in use. No background GPS trajectory in MVP. HTTPS, validation, rate limits, RBAC for admin workflows, secrets outside repo, restricted Google API keys.
