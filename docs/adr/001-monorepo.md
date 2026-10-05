# ADR 001: Monorepo Architecture

## Context
Need to coordinate backend, mobile, workers, infra, docs.

## Decision
Single monorepo with top-level directories.

## Rationale
Easier cross-cutting changes, shared CI, atomic PRs.

## Consequences
Need clear directory boundaries, larger clone.
