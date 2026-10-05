# Taipei V2 ingestion (M4)

The one-shot worker runs separately from FastAPI, shares its PostgreSQL models,
and never makes the mobile app contact government services. A scheduler can run
the realtime feed every minute and static feed daily; the worker itself does not
run a scheduler or expose an administrative HTTP endpoint.

## Official source and attribution

The [Taipei dataset](https://data.taipei/dataset/detail?id=d5c0656b-5250-4179-a491-c94daa56ef2c)
publishes these V2 feeds, verified on 2026-10-06:

| Feed | Endpoint | Source code | Freshness policy |
| --- | --- | --- | --- |
| Static | `https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.json` | `TAIPEI_STATIC_V2` | 86400 seconds |
| Realtime | `https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.json` | `TAIPEI_REALTIME_V2` | 120 seconds |

The previous `blobfs` path is no longer the dataset's published endpoint.
Both feeds contain `data.park` records and `data.UPDATETIME`. The English
timestamp's literal `CST` means **Asia/Taipei**. Invalid/missing source timestamps
remain NULL and their original strings remain in the batch envelope.
`fetched_at` is the actual successful HTTP receipt instant in UTC.

The worker persists separate GOVERNMENT source records and independent component
provenance. Attribution: **臺北市政府交通局停車管理工程處 2026 臺北市停車場資訊 V2**.
The [Government Open Data License v1](https://data.taipei/rule) applies to these
fixtures and external data; the repository's source-code license does not replace it.

## Running the worker

From `backend/`, after installing its dependencies and running `alembic upgrade head`:

```bash
python -m app.ingestion.cli --feed all
python -m app.ingestion.cli --feed static
python -m app.ingestion.cli --feed realtime
```

`all` imports static before realtime. Configure `DATABASE_URL` and `REDIS_URL`
as for the backend. Docker usage with the API image and Compose dependencies:

```bash
docker compose run --rm api python -m app.ingestion.cli --feed all
```

Every feed prints a JSON result with `batch_id`, `status`, total, normalized and
failed counts. Exit 0 means all requested batches succeeded; exit 1 means a
partial/failed import; invalid CLI arguments or unreadable input exit 2.
Records are still committed for a partial batch. `--no-cache` disables the Redis
status cache; a Redis outage is recorded without undoing committed PostgreSQL data.

File replay requires the original absolute receipt instant, so archived data
cannot accidentally become freshly fetched realtime:

Static input is a complete source snapshot. Replaying a sample fixture should
use a disposable development database; lots absent from it become UNKNOWN.
An empty valid snapshot also makes existing source-owned lots UNKNOWN until a
later successful snapshot restores explicit evidence.

```bash
python -m app.ingestion.cli --feed all \
  --static-file tests/fixtures/taipei/alldesc_sample.json \
  --realtime-file tests/fixtures/taipei/allavailable_sample.json \
  --fetched-at 2026-10-05T16:02:01Z --no-cache
```

## Mapping policy

| Source evidence | Normalized fact | Heavy vehicle policy |
| --- | --- | --- |
| Positive `totalcar` | `car` / CAR_SHARED | CAR true; YELLOW/RED NULL |
| Positive `totalmotor` | `motor` / MOTO_SHARED | GREEN/WHITE true; YELLOW/RED NULL |
| Positive `totallargemotor` | `heavy` / HEAVY_ONLY | Explicit dedicated heavy category confirms YELLOW/RED true |
| Missing category count with matching fare channel | Capacity-NULL zone | All permissions NULL |
| Zero category count | No new zone | Never converted into false permission |
| `tw97x`, `tw97y` | Lot center: EPSG:3826 -> EPSG:4326 via pyproj | No entrance-coordinate fallback |
| `EntrancecoordInfo.Xcod`, `Ycod` | Separate WGS84 entrance latitude, longitude | Heavy entrance access NULL |
| `availablecar`, `availablemotor`, `availableheavymotor` | Corresponding category observations | Never reuse car/motor numbers for heavy |
| Missing or `-9` availability | UNKNOWN, null counts | Supersedes a prior positive observation |

Counts accept nonnegative integers or ASCII integer strings, bounded by PostgreSQL
INTEGER. Booleans, fractions and other negatives are rejected without destroying
the raw evidence. Invalid lot coordinates reject that record; invalid entrance
coordinates retain the entrance with null coordinates. Lots with no supported
category are retained with zero zones; no shared zone is invented.

The adapter assigns zone-specific `BASELINE`, `authority_priority=100` from
this explicit policy. Confidence remains NULL. It always writes a permission
rule, including NULL permissions, so M2 space-type defaults cannot convert a
generic category into confirmed heavy permission. An existing source-owned zone
that disappears from a later lot record keeps its stable ID and an active NULL
rule; its source-owned rates expire. Other providers' rules, entrances and rates
remain independently attributable and untouched.

Realtime total capacity is joined only when both feeds have the **same non-null
upstream update instant**. Otherwise total remains NULL, even when a static
capacity exists. UNKNOWN observations never carry trustworthy counts. A count
above a confirmed corresponding total fails that whole lot record, including its
other categories, to keep the normalized snapshot internally consistent.
The API derives current freshness from source policy; the worker does not persist
freshness or lot aggregates. Taipei realtime opts into
`freshness_uses_source_timestamp=true`: age is measured from the earlier of
fetched_at and source_updated_at. Both timestamps must exist and must not be in
the future. Repeatedly fetching a frozen source cannot turn old observations
FRESH. Other sources retain their existing fetch-based policy unless explicitly
configured otherwise; ranking formulas and `sort_version=1` remain unchanged.

## Fees

`parse_rate_text` fully parses only complete, simple phrases: hourly, time block,
per entry, free, daily, monthly and an explicit daily cap. Values use finite
Decimal amounts within NUMERIC(10,2). Rounding, day/event discounts, progressive
or multi-vehicle prose remains PARTIALLY_PARSED/RAW_ONLY; malformed numeric terms
are INVALID. The original text is always retained. An unlabelled `payex` remains
`vehicle_type=NULL` even when fully parsed, so it cannot confirm a selected price.

Structured `FareInfo.FareRule` is **at most PARTIALLY_PARSED**: real `payex`
contains day/event conditions not represented in this list. `ParkingRates` and
local `ChargeableSTime`/`ChargeableETime` are retained, but no hourly unit is
guessed. `00`–`24` means unrestricted; a terminal `24` becomes midnight; overnight
windows remain separate rules. Unknown types remain raw and malformed amounts or
windows become INVALID.

`C` fees apply only to CAR, `M` only GREEN/WHITE, `HM` only YELLOW/RED on a heavy
zone, and `CM` to CAR plus YELLOW/RED on an existing heavy zone. Fare fields never
grant legality. Bus `T` and charging `RateType=9` are not parking prices. Exact
duplicate fare entries collapse; distinct conflicting entries remain independent.

## Transactions, retries and observability

1. Download with a 30-second HTTPX timeout, a 20 MB decompressed payload limit,
   and at most four attempts. Transport failures, 429 and 5xx retry after 1, 2,
   and 4 seconds; other HTTP failures do not retry. Redirects are not followed.
2. Commit the complete envelope and each raw record before normalized writes.
   Invalid JSON response bytes are retained as base64 in a failed import batch.
   NUL/lone-surrogate payloads are retained reversibly as
   `{"encoding":"base64-json-ascii","data":"..."}`; offending records fail
   while other valid records remain importable. File replay uses the same strict
   JSON decoder as downloads and cannot mix live and archived feeds.
3. Serialize both Taipei feeds with PostgreSQL transaction advisory lock
   `7400401`. Normalize records inside SAVEPOINTs. Schema/validation/integrity
   failures mark individual records INVALID while valid records proceed.
4. Reject conflicting rows sharing an upstream ID; never choose the last row.
   Exact raw duplicates remain evidence but normalize once. Reject older source
   or fetch instants rather than overwrite newer facts.
5. Commit normalized data and raw statuses atomically. Unexpected fatal failures
   roll back the normalized batch, retain its separately committed raw evidence,
   and mark its rows `BATCH_ABORTED`.
6. Persist batch status/counters/errors. Redis caches only import-status metadata
   at `ingestion:taipei:<feed>:latest`, with the feed's freshness TTL.

Repeated imports append audit batches/raw evidence but retain normalized lot,
zone, rule, rate and entrance identities. Realtime retains observation history;
an identical zone/source/fetch/update instant is deduplicated, and a contradictory
observation at that same instant fails rather than arbitrarily replace it.

Inspect partial failures without reading operational secrets:

```sql
SELECT id, feed_kind, status, total_records, normalized_records, failed_records,
       fetched_at, source_updated_at, error_summary
FROM raw_import_batches ORDER BY id DESC LIMIT 20;

SELECT external_id, error_code, error_message, payload
FROM raw_parking_records WHERE batch_id = :batch_id AND status = 'INVALID';
```

## Validation and limits

Fixtures contain a reduced official TPE0003 record and clearly synthetic edge
cases. Tests cover locale-independent timestamps, projection, missing permissions,
fee ambiguity, sentinel availability, retries, replay, partial failure, SAVEPOINTs,
fatal rollback, source isolation, overlapping workers and migration round trips.
Live-source manual verification uses a disposable database and never rewrites
the user's running API/database.

This milestone does not add a production scheduler, distributed task queue,
retention jobs, official holiday calendar, source-specific cache invalidation,
or New Taipei support. A forced process termination can leave a durable RUNNING
batch requiring operator inspection/replay. A failed static record retains its
previous source facts and older provenance. Lots absent from a newer complete feed
receive NULL permission rules, inactive source zones, retired rates and null
entrance coordinates; IDs and raw history remain. Inactive zones receive UNKNOWN
realtime even if a later feed still supplies positive counts. API response caching remains
outside this milestone. Different static/realtime timestamps will often leave
numeric lot coverage incomplete, deliberately preserving UNKNOWN summaries.
