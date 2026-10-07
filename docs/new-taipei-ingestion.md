# New Taipei ingestion (M7)

Issue #8 adds New Taipei City parking data through the same worker, normalized
contracts, writer and API as [Taipei](taipei-ingestion.md). The mobile app still
never contacts government services.

## Official sources and attribution

Verified on 2026-10-06 from the [New Taipei open data platform](https://data.ntpc.gov.tw):

| Feed | Dataset | Endpoint | Source code | Freshness policy |
| --- | --- | --- | --- | --- |
| Static | 新北市路外公共停車場資訊 | `https://data.ntpc.gov.tw/api/datasets/B1464EF0-9C7C-4A6F-ABF7-6BDF32847E68/json` | `NEW_TAIPEI_STATIC` | 86400 seconds |
| Realtime | 新北市公有路外停車場即時賸餘車位數 | `https://data.ntpc.gov.tw/api/datasets/E09B35A5-A738-48CC-B0F5-570B67AD9C78/json` | `NEW_TAIPEI_REALTIME` | 180 seconds, fetch-based |

Both are paged JSON arrays (`?page=N&size=1000`; larger sizes are capped at 1000).
On 2026-10-06 the static feed had 1,384 rows (1,374 unique IDs) and the realtime feed
had 1,384 rows. Attribution: **新北市政府 新北市路外公共停車場資訊、新北市公有路外停車場即時賸餘車位數**,
[政府資料開放授權條款第1版](https://data.gov.tw/license).

## Running the worker

```bash
python -m app.ingestion.cli --city new_taipei --feed all
python -m app.ingestion.cli --city new_taipei --feed static \
  --static-file tests/fixtures/new_taipei/static_sample.json \
  --fetched-at 2026-10-06T17:30:00Z --no-cache
```

`--city` defaults to `taipei`, so existing Taipei commands are unchanged. Exit codes,
JSON results, replay rules and the Redis status cache key
(`ingestion:new_taipei:<feed>:latest`) follow the Taipei worker.

## Paging and snapshot completeness

The downloader requests pages until one has fewer than 1,000 rows (at most 20 pages).
Every page uses the same timeout, retry and size limits as Taipei. **Any failed,
non-array or runaway page fails the whole download**, which is recorded as a FAILED
batch and leaves normalized data untouched. The stored envelope is
`{"page_size": 1000, "pages": [[...], ...]}`, so raw evidence keeps page boundaries.
The snapshot `fetched_at` is the *first* page receipt, so freshness never looks newer
than the oldest page.

Paging is not atomic upstream. A static snapshot whose unique lot IDs are fewer than
80% of lots that currently have an active zone imports its records but **skips
retiring absent lots**, with warning `RECONCILE_SKIPPED_INCOMPLETE_SNAPSHOT`. Taipei
keeps its complete-snapshot semantics.

## Mapping policy

| Source evidence | Normalized fact | Heavy vehicle policy |
| --- | --- | --- |
| Positive `TOTALCAR` | `car` / CAR_SHARED | CAR true; YELLOW/RED NULL |
| Positive `TOTALMOTOR` | `motor` / MOTO_SHARED | GREEN/WHITE true; YELLOW/RED NULL |
| `重型機車` fee segment | Capacity-NULL `heavy` / HEAVY_ONLY | All permissions NULL: a fee never grants legality |
| Missing (`""`) count with matching fee segment | Capacity-NULL zone | All permissions NULL |
| Zero count | No zone | Never NOT_ALLOWED |
| `TW97X`, `TW97Y` | Lot center via EPSG:3826 -> 4326 | No entrances (none published) |
| `AVAILABLECAR` | Car observation | `-9`/missing => UNKNOWN; heavy counts never derived |

There is no heavy-motorcycle count field, so every New Taipei lot is UNKNOWN for
YELLOW/RED. It appears in nearby results only with `include_unknown=true`, labelled
unverified and ranked after confirmed results. This is deliberate (AGENTS rules 7–9).

`PAYEX` is split on `;`. `小型車` -> CAR on car, `機車` -> GREEN/WHITE on motor,
`重型機車` -> YELLOW/RED on heavy. `身障車`, `身障機車` (permit holders), `大型車`
(bus/truck) and unlabelled segments are not selected-vehicle prices. Segment terms
use the shared conservative text parser. `計時N元` states no time unit, so it stays
PARTIALLY_PARSED with no comparison value; `月租`, `計次` and `免費` parse fully.
Conflicting segments for one vehicle remain separate rates, and the rate resolver
then refuses a confirmed price. The full `PAYEX` is also attached to each zone as a
vehicle-NULL rate. `TYPE`, `SUMMARY`, `TEL`, `SERVICETIME` and `TOTALBIKE` stay raw
evidence only.

Realtime total is never joined: neither feed has an upstream update instant, so lot
numeric totals stay UNKNOWN (same rule as Taipei). Without a source timestamp,
freshness is fetch-based: re-fetching a frozen upstream feed can still look FRESH.
An UNKNOWN-only realtime record for a lot without that category is skipped; a known
count without a matching zone is still a `ZONE_NOT_IMPORTED` record failure.

## Shared infrastructure

M7 generalizes the M4 worker without changing Taipei behavior:

- `sources.CitySource` holds each city's feeds, lock key, attribution, rule note and
  identity prefix. Taipei keeps lock `7400401` and `taipei:` identities; New Taipei
  uses lock `7400402` and `new_taipei:` identities.
- `ingestion/common.py` holds city-independent validators (counts, TWD97 projection,
  Taiwan bounds, digests).
- Adapters supply `record_key` and `raw_rate_text`; the pipeline no longer assumes
  Taipei field names.
- The normalized transaction flushes and clears ORM state after each record, so a
  large feed does not slow down as the session grows.

## Live verification (2026-10-07, disposable PostGIS)

| Run | Static | Realtime |
| --- | --- | --- |
| Rows / normalized / failed | 1,384 / 1,361 / 23 | 1,384 / 1,339 / 45 |
| Duration (re-import) | 133 s | 35 s |

Static failures: 14 `DUPLICATE_ID_CONFLICT` (upstream rows sharing an ID with
different content, e.g. `170120`), 9 `INVALID_COORDINATES`. Realtime failures:
23 `LOT_NOT_IMPORTED` (their static rows failed) and 22 `ZONE_NOT_IMPORTED` (car
availability for lots whose `TOTALCAR` is 0). Normalized lot IDs stayed stable
across re-imports (1,359 lots; 1,294 car, 381 motor, 85 heavy zones).
