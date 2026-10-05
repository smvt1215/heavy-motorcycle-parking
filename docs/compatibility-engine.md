# M2 parking compatibility engine

`ParkingCompatibilityService.evaluate(parking, zone, vehicle, timestamp)` is a
pure domain operation. It does not query the database, fetch government data,
read authentication state or the wall clock, or depend on HTTP/mobile code.
Repositories supply preloaded `ParkingFacts`, `ZoneFacts` and `RuleFact` objects
from `app.domain.parking`; source policy must supply rule kind and authority.
The selected vehicle and an offset-aware evaluation timestamp are required.
The service validates that the zone belongs to the requested lot.

## Resolution

1. Ignore rules for another lot/zone, inactive rules, and rules outside the
   absolute effective window `[effective_from, effective_to)`.
2. Evaluate each remaining schedule in Asia/Taipei. Known nonmatches are ignored.
   Unresolved schedules remain uncertain candidates.
3. Select the highest tier: zone-specific over lot-wide, then EXCEPTION over
   BASELINE, then highest configured integer `authority_priority`.
4. Evaluate every fact tied in that tier. All TRUE means ALLOWED; all FALSE means
   NOT_ALLOWED. Any TRUE/FALSE conflict, any NULL, or any unresolved schedule
   means UNKNOWN. Never fall back from an uncertain/conflicting winning tier.
5. Only when there are no candidates, apply the documented space-type defaults.

| Space type | Vehicles allowed by classification alone |
| --- | --- |
| HEAVY_ONLY | YELLOW, RED |
| MOTO_SHARED | GREEN, WHITE, YELLOW, RED |
| CAR_SHARED | YELLOW, RED, CAR |
| LIGHT_MOTO_ONLY | GREEN, WHITE |

Explicit tri-state permissions override classification; an explicit NULL does
not use the classification default. Database ID, source timestamp, input order
and confidence never resolve legality. Rule IDs must be unique in a supplied
fact snapshot; conflicting permissions with distinct rule IDs are retained.

## Schedule JSONB contract

Rules may use the following normalized schedule object. Constraints intersect
(AND); absence of a constraint means unrestricted on that dimension. `NULL`
schedule and `{}` mean no schedule restriction. Unknown fields or malformed
values produce UNKNOWN applicability, without silently discarding the rule.

| Key | Meaning |
| --- | --- |
| timezone | Optional; only `Asia/Taipei` is supported, also the default. |
| day_type | `ALL` (default), `WEEKDAY`, `WEEKEND`, `HOLIDAY`, or `SPECIAL`. |
| weekdays | Optional array of integers: Monday=0 through Sunday=6. Empty array matches no days. |
| dates | Optional array of `YYYY-MM-DD` dates. Required for SPECIAL; empty array matches no days. |
| start_time / end_time | Both supplied or both absent. `HH:MM` or `HH:MM:SS`, without an offset; half-open `[start,end)`. |

WEEKDAY means Monday–Friday and WEEKEND means Saturday–Sunday; these do not infer
official public holidays or substitute working days. HOLIDAY requires an
injected, preloaded `HolidayCalendar` with explicit coverage for the local date.
`CalendarSnapshot` accepts known true/false dates; an absent date or absent
calendar means UNKNOWN. M2 does not bundle or download an official calendar.

A start time later than the end time defines an overnight window. The portion
after midnight uses the **previous day's** weekdays, dates and holiday flag.
For example, Friday `22:00`–`06:00` includes Saturday `01:00`, with Friday as
the schedule date. Use `00:00` for an overnight end at midnight; equal endpoints
and `24:00` are invalid. Omit both endpoints for an unrestricted day.

```json
{
  "timezone": "Asia/Taipei",
  "weekdays": [4],
  "start_time": "22:00",
  "end_time": "06:00"
}
```

Invalid normalized permission values, naive effective/evaluation timestamps,
unsupported space/vehicle types, invalid authority metadata and nonfinite or
out-of-range confidence raise `ValueError`. Lot, zone, rule and source IDs must
be actual integers; strings and booleans are rejected before scope matching.
Upstream invalid payloads remain
raw ingestion evidence; they must not be coerced into legality facts.

## Result and provenance

`CompatibilityResult` exposes explicit `status`, lot/zone identity, `vehicle`,
UTC `evaluation_at`, `space_type`, `reason`, all winning `provenance`, `rule_ids`
and nullable `confidence`. `to_dict()` supplies JSON-safe diagnostics with UTC
RFC3339 strings and explicit nulls. This internal result is projected into the
documented common zone wire schema by M3; it is not a new HTTP endpoint/schema.

Reasons are `space_type_default`, `explicit_vehicle_permission`,
`conflicting_permissions`, `unknown_permission`, and `schedule_unknown`.
If both known permission conflict and NULL exist, the conflict reason wins.
Schedule uncertainty is reported before permission resolution.

All winning rule sources are retained, including multiple contradictory records
from the same source. IDs sort paired evidence for stable serialization only.
For confirmed results, confidence is the minimum winning confidence if every
winner supplies one; otherwise it is NULL. UNKNOWN always has NULL confidence.
Classification defaults have empty source evidence and NULL confidence, so no
unrelated lot/rate/realtime/entrance provenance is presented as a rule source.

## Nearby policy

`is_nearby_eligible` and `filter_nearby_zones` include ALLOWED, exclude NOT_ALLOWED,
and include UNKNOWN only with the explicit boolean `include_unknown=True`.
UNKNOWN remains UNKNOWN. Normal YELLOW/RED nearby search excludes LIGHT_MOTO_ONLY
even if inconsistent source permissions would override that classification;
no prohibited-location browsing control is introduced.

`rollup_nearby_compatibility` consumes only returned zones for one lot, vehicle
and evaluation instant. Any ALLOWED yields lot ALLOWED; otherwise any returned
UNKNOWN yields lot UNKNOWN; an empty set yields None (omit the lot). Passing
NOT_ALLOWED, heavy-vehicle LIGHT_MOTO_ONLY or mixed contexts is rejected. The lot result is a projection,
never an independent compatibility source fact.

## Verification and next milestone

Unit tests cover all 4×5 classifications, all selected permissions, precedence,
contradictory/NULL ties, recency/confidence/order independence, schedules,
missing holiday coverage, evidence serialization and nearby rollup. Live
PostgreSQL fixtures verify that persisted conflicting/null facts remain UNKNOWN
after conversion into domain inputs. A separate-process import test verifies
that the service does not load ORM or HTTP dependencies.

M3 repositories must fetch complete relevant rule evidence and map it into
these facts before evaluating compatibility at one pinned instant. Rate
normalization/evaluation, realtime freshness/aggregation, nearby scoring and
pagination remain their separately documented responsibilities.
