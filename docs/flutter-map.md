# M5 Flutter map experience

Issue #6 adds the Google Maps home screen. Parking data comes exclusively from
our `/api/v1/parking` API; the map SDK supplies the basemap and user interaction.

## Architecture

- `domain/parking.dart` preserves the backend's zone base, tri-state permissions,
  observation status, independent freshness, fact-specific provenance, and
  server-derived aggregate status. It never calculates ranking or hourly rates.
- `data/parking_repository.dart` uses Dio for nearby/detail/rates/realtime. Every
  request includes the selected vehicle. Transport/invalid-response/cursor errors
  become stable client errors. Network requests have 15-second timeouts.
- `features/map/map_controller.dart` keeps vehicle and filters in persistent
  Riverpod app state. Camera frames only update the pending center. Camera idle
  exposes `搜尋此區域`; pressing it commits the center and requests nearby data.
  Filter changes search at the previously committed center. New queries invalidate
  previous search/detail responses. Pagination reuses the original query and
  opaque cursor; detail uses nearby's `evaluation_at` as `at`. Continuations
  with mismatched evaluation time or sort version are rejected without appending
  results. Detail responses must match the requested lot, vehicle and pinned
  instant. Unexpected repository errors finish loading and retain race guards.
- `features/map/parking_presentation.dart` contains formatting and display guards.
  Widgets render these facts without resolving legality or synthesizing totals.
- Foreground location is a one-shot request after the location button is pressed.
  No location stream, background permission, or trajectory storage is used.
- Navigation uses an ALLOWED entrance with coordinates. Otherwise its action
  explicitly targets the lot center. UNKNOWN and NOT_ALLOWED entrances are not
  automatically selected.

## Map and interaction

Built-in `ClusterManager` groups markers; a cluster tap fits its bounds. Marker
silhouettes plus motorcycle/car icons distinguish the domain space types.
Unverified markers use a triangle and question mark; their result and zone rows
say `尚未確認`. Normal filters offer HEAVY_ONLY, MOTO_SHARED, CAR_SHARED only.

Vehicle defaults to explicit RED, with YELLOW/RED selection in app state. Quick
filters send `available_only` and confirmed hourly thresholds to the backend;
advanced filters add radius, space type, daily cap and `include_unknown`.
Preferences survive route/widget rebuilds for the app session. Persistence across
process restarts and profile preferences belong to M8.

The bottom panel keeps each matched zone's availability, rates and evidence
together. A stale AVAILABLE observation remains AVAILABLE and is labeled as the
last observation with possibly outdated freshness. Numeric lot totals require
the API's COMPLETE/FRESH summary with valid counts and complete fetch provenance;
PARTIAL/NONE remain unknown totals. It uses the server aggregate status rather
than reconstructing it from child statuses. Nearby facts retain their own fetch
timestamps; there is no automatic background refresh in M5.

System light/dark mode changes both app and map styles. Standard Flutter controls
provide accessibility semantics; controls are at least 48 logical pixels.

## Run locally

Use Flutter stable (validated with 3.47.6). Enable Maps SDK for Android and Maps
SDK for iOS in a Google Cloud project. Restrict each key to its platform bundle
or application/signing identity. Do not commit keys.

Android reads `MAPS_API_KEY` from the environment or a Gradle property, such as
the user-level `~/.gradle/gradle.properties`:

```properties
MAPS_API_KEY=<restricted Android Maps SDK key>
```

For iOS create ignored `mobile/ios/Flutter/Maps.xcconfig`:

```xcconfig
MAPS_API_KEY = <restricted iOS Maps SDK key>
```

iOS uses CocoaPods for native plugins so the geolocator target receives
`BYPASS_PERMISSION_LOCATION_ALWAYS=1`. Only
`NSLocationWhenInUseUsageDescription` is declared. The app's minimum iOS version
is 15.0, matching the existing Runner project. A future SwiftPM migration must
preserve the same foreground-only permission behavior.

Both platform entry points expose whether a nonempty Maps SDK key was configured
through the `tw.heavyparking/config` channel. Flutter checks this before creating
the map; absent configuration shows a fallback while parking controls and results
remain usable. This checks initialization, not Google key authorization.

```bash
cd mobile
flutter pub get
# Android emulator reaches the host backend at 10.0.2.2.
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1
# iOS simulator can use localhost; devices need a reachable HTTPS backend.
flutter run --dart-define=API_BASE_URL=https://<backend-host>/api/v1
```

The debug-only Android manifest permits local cleartext development. Release
traffic requires HTTPS.
Google map rendering requires configured SDK keys and network access. Widget
tests inject a map canvas and repository to run independently of those services.

## Verification

```bash
cd mobile
flutter analyze
flutter test
flutter build apk --debug
# macOS + Xcode + CocoaPods:
flutter build ios --simulator --no-codesign
```

Tests cover backend-shaped parsing, explicit vehicles on all four endpoints,
time-pinned detail and pagination, camera request gating, persistent app state,
request races, cursor errors, mixed zones, freshness/count/provenance edges,
server aggregate status combinations, unknown opt-in, confirmed hourly filter
clearing, tri-state entrances, external navigation coordinates and widget states.
CI pins Flutter 3.47.6 and builds Android and iOS native projects while running mobile checks and
the existing backend/migration/Docker gates.

Manual device verification with restricted keys remains necessary for basemap
rendering, native marker clustering, the operating system permission dialog,
Google/Apple Maps app dispatch, VoiceOver/TalkBack and platform appearance. Check
on both platforms: deny location and keep searching; drag without API requests;
press `搜尋此區域`; change YELLOW/RED and filters; open mixed-zone detail; check
stale/partial/unknown wording; confirm navigation prefers the allowed entrance;
switch system light/dark appearance. Destination search is M6, outside M5.
