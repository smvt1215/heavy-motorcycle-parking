# ADR 003: Configuration Management

## Context
Need environment-specific configuration without leaking secrets.

## Decision
pydantic-settings with .env files, .env.example committed, .env gitignored.

## Rationale
Type-safe config, env var override, no secrets in repo.

## Consequences
Developers must copy .env.example to .env.
