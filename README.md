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
4. Run database migrations:
   ```bash
   alembic upgrade head
   ```
5. Start the development server:
   ```bash
   uvicorn app.main:app --reload
   ```

### Mobile

1. Navigate to the mobile directory:
   ```bash
   cd mobile
   ```
2. Fetch dependencies:
   ```bash
   flutter pub get
   ```
3. Run the app:
   ```bash
   flutter run
   ```

## Running Tests

- **Backend**: `cd backend && pytest`
- **Mobile**: `cd mobile && flutter test`

## Project Structure

- `backend/`: FastAPI application
- `mobile/`: Flutter mobile application
- `database/`: Database initialization scripts and migrations
- `infra/`: Infrastructure configuration and deployment scripts (placeholder)
- `workers/`: Data pipeline workers (placeholder)
- `docs/`: Architecture Decision Records (ADRs) and documentation
- `scripts/`: Utility scripts

## Documentation

See the [docs/](docs/) directory for detailed architecture decisions.

## License

All rights reserved.