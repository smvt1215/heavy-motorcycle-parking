# ADR 002: Tech Stack

## Context
Need backend API, mobile app, spatial database.

## Decision
FastAPI + Flutter + PostgreSQL/PostGIS + Redis.

## Rationale
FastAPI for async Python + auto-OpenAPI; Flutter for cross-platform with strong map support; PostGIS for spatial queries; Redis for caching realtime data.

## Consequences
Team needs Python + Dart skills.
