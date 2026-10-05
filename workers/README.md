# Workers

Data pipeline workers for fetching, normalizing, and validating parking data from government and operator sources.

M4's Taipei worker shares backend dependencies and models in `backend/app/ingestion`.
Run `python -m app.ingestion.cli --feed all` from `backend/`, or
`docker compose run --rm api python -m app.ingestion.cli --feed all` after migrations.
See [Taipei ingestion](../docs/taipei-ingestion.md) for scheduling, source policy,
file replay, errors and attribution. This directory documents worker operations;
city parsing and persistence stay in the tested Python package.
