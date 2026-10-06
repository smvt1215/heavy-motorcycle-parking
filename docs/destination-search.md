# M6 Destination search

Issue #7 adds destination search. Google Places API (New) is used only to turn an
address or POI into coordinates. Parking legality, space types, rates and
availability always come from our own `/api/v1/parking/*` data.

## Flow

1. The rider taps `搜尋目的地` on the map and types, e.g. `台北101`.
2. After a 350 ms quiet period (inputs shorter than 2 characters show recent
   searches instead) the app calls `GET /api/v1/places/autocomplete`.
3. Selecting a suggestion calls `GET /api/v1/places/{place_id}` with the same
   session token, which ends the billed Places session.
4. The map stores the destination, moves the camera to it, drops a distinct
   destination marker (`搜尋目的地（非停車位置）`) and runs a normal nearby search
   at the destination with the current explicit vehicle and filters.

The destination marker never participates in clustering, ranking or filters.
Later `搜尋此區域` searches keep the marker until the rider clears it.

## Backend proxy

The mobile app never holds a Places key. `GOOGLE_PLACES_API_KEY` is a server
secret; leave it blank to disable the feature, in which case both endpoints return
`503 PLACES_UNAVAILABLE` and the map keeps working.

- Autocomplete sends `input`, `sessionToken`, `languageCode=zh-TW`,
  `regionCode=tw`, `includedRegionCodes=["tw"]` and an optional 20 km location
  bias from the map's searched center. The bias is a hint, not a filter.
- Field masks request only `placeId`, prediction text and structured text for
  autocomplete; and only `id`, `displayName`, `formattedAddress` and `location`
  for details. No Places rating, opening hours, parking options or other content
  is requested or returned.
- At most 5 suggestions are returned. Malformed predictions are dropped. A
  details response without valid WGS84 coordinates is `502 PLACES_UPSTREAM_ERROR`.
- Upstream 404 => `404 PLACE_NOT_FOUND`; every other upstream/transport failure
  => `502 PLACES_UPSTREAM_ERROR`. The Google error body is never forwarded.
- A Redis fixed-window limiter allows `PLACES_RATE_LIMIT_PER_MINUTE` requests
  (default 60) per client address across both endpoints; excess requests get
  `429 RATE_LIMITED` before Google is called. A Redis outage fails open so
  search stays available; the key's Google Cloud quota is the hard ceiling.
- Search text is never logged.

Restrict the key to the Places API (New) and the API's egress IP addresses, and
set a daily quota in Google Cloud.

## Mobile

- `data/places_repository.dart` calls only our backend through the shared Dio
  client and maps the common error envelope to `PlacesException`. Dio
  cancellation becomes `CANCELLED`, which is never shown as an error.
- `features/search/destination_search_controller.dart` owns debounce, session
  tokens (random UUIDv4), request generations and cancellation. A newer
  keystroke cancels the pending timer and the in-flight request; late responses
  are discarded. Closing the screen cancels everything as soon as the pop starts.
  Retry after an error reuses the open session. Each opened search screen gets a
  fresh controller and session.
- Screen states: recent history / hint (idle), loading, results, explicit empty
  (`找不到「…」相關地點`), error with `重試`, and a selection error banner when
  resolving coordinates fails. Google attribution and the geocoding-only notice
  are always visible.

## Recent searches

History is device-local (`shared_preferences`, key
`destination_search.recent.v1`), at most 8 entries, de-duplicated by place ID,
and never sent to the backend. Google coordinates are cached for at most 30
days; older entries keep only the place ID, name and address and are re-resolved
through the backend (in a new session) when selected. Corrupt entries are
skipped. Riders can remove one entry or clear all.

## Verification

```bash
cd backend && pytest tests/test_places.py
cd mobile && flutter test test/features/destination_search_controller_test.dart \
  test/map_screen_test.dart test/data/places_repository_test.dart \
  test/data/recent_searches_store_test.dart test/domain/place_test.dart
```

Tests cover the `台北101` end-to-end flow into `/parking/nearby`, debounce and
session reuse, no result, API error and retry, cancellation and superseded
responses, closing during debounce, recent history caching/expiry, Places field
masks, upstream error mapping, validation and rate limiting.

Manual check with a real key: search `台北101`, `台北車站`, an address and a
nonsense string; confirm the camera, marker, nearby results, attribution, and
that Google Cloud metrics show one session per selection.
