# 重機停車通 (Heavy Motorcycle Parking)

A comprehensive parking application designed specifically for heavy motorcycles.

## Architecture Overview

- **Backend**: FastAPI (Python 3.12+), Pydantic v2, SQLAlchemy 2 (async)
- **Database**: PostgreSQL 18 with PostGIS 3.6+
- **Cache**: Redis 7
- **Mobile App**: Flutter stable, Riverpod, go_router, Dio, Freezed

## Prerequisites

- Docker and Docker Compose
- Python 3.12+
- Flutter stable

## Quick Start

1. Set up the environment variables:
   ```bash
   cp .env.example .env
   ```
2. Start the infrastructure:
   ```bash
   docker compose up -d
   ```

## Development Setup

### Backend

1. Navigate to the backend directory:
   ```bash
   cd backend
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows use `.venv\Scripts\activate`
   ```
3. Install dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
4. Run database migrations (uses `backend/alembic.ini`):
   ```bash
   alembic upgrade head
   ```
   The backend reads the repository-root `.env` regardless of the working directory;
   real environment variables take precedence over it.
5. Start the development server:
   ```bash
   uvicorn app.main:app --reload
   ```

Parking discovery is available at `/api/v1/parking/nearby?lat=25.03&lng=121.56&vehicle=LARGE_HEAVY`.
Detail, rates and realtime requests also require `vehicle`. See
[M3 API implementation](docs/parking-api-implementation.md) for shared zone
schemas, time-pinned queries, rate evaluation, cursor behavior and EXPLAIN evidence.
Production API workers require the same `CURSOR_SIGNING_KEY` secret of at least
32 bytes; development without one uses a process key that changes on restart.

### Mobile

The M5 map home screen provides clustered markers, NORMAL_HEAVY/LARGE_HEAVY vehicle selection,
backend filters and a zone-scoped parking panel. Set restricted native Maps SDK
keys and `API_BASE_URL` before device testing; see [Flutter map setup](docs/flutter-map.md).
M8 adds favorites, community reports and sign-in state; see
[user features](docs/user-features.md) for tokens and S3 photo storage.
M6 destination search uses a server-side `GOOGLE_PLACES_API_KEY`; see
[destination search](docs/destination-search.md).

1. Navigate to the mobile directory:
   ```bash
   cd mobile
   ```
2. Fetch dependencies:
   ```bash
   flutter pub get
   ```
3. Run the app on an Android emulator/device or iOS simulator/device
   (`android/` and `ios/` platform projects are included; iOS builds require macOS + Xcode):
   ```bash
   flutter run
   ```

## Running Tests

- **Backend**: `cd backend && pytest`
- **Mobile**: `cd mobile && flutter test`

Backend migration/schema tests upgrade and downgrade the configured database.
Use a disposable PostgreSQL/PostGIS test database, supplied through `DATABASE_URL`;
do not point these tests at a database containing data you need to keep.
Set `REDIS_URL` to a test Redis instance and `HEALTH_REQUIRE_SERVICES=1` to also
verify live service health, as CI does. The schema tests include the M1 migration,
foreign keys, tri-state permissions, realtime integrity, and ORM/schema comparison.

## Project Structure

- `backend/`: FastAPI application
- `mobile/`: Flutter mobile application
- `database/`: Database initialization scripts and migrations
- `infra/`: Infrastructure configuration and deployment scripts (placeholder)
- `workers/`: Data pipeline worker operations; M4 implementation in `backend/app/ingestion`
- `docs/`: Architecture Decision Records (ADRs) and documentation
- `scripts/`: Utility scripts

## Documentation

See the [docs/](docs/) directory for detailed architecture decisions.

To import Taipei parking data after backend migrations, run
`python -m app.ingestion.cli --feed all` from `backend/`.
See [New Taipei ingestion](docs/new-taipei-ingestion.md) for `--city new_taipei`.
See [Taipei ingestion](docs/taipei-ingestion.md) for source mappings, strict
unknown-data handling, file replay and observable record failures.

## License

All rights reserved.

雙北政策來源、核對範圍與路邊 worker：[雙北政策與資料](docs/twin-city-policy.md)。

整合結果、實機與金鑰待驗項目：[整合驗收紀錄](docs/integration-acceptance.md)。

下一輪資料補齊、社群核實與 Flutter Web 初複審工作台：[實作計畫](docs/community-verification-plan.md)。
