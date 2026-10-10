# M8 User features

Issue #9 adds sign-in state, a preferred vehicle, favorites, community reports and
report photos without turning the app into a social platform. Guests keep every
public parking, search and detail feature.

## Authentication

- Protected endpoints require `Authorization: Bearer <access_token>`. Identity comes
  only from the validated token; request bodies reject unknown fields such as
  `user_id`, and query/header user IDs are ignored.
- Access tokens are opaque (`hmp_` + 256 random bits). The `access_tokens` table
  stores only their SHA-256 digest, an expiry (`ACCESS_TOKEN_TTL_DAYS`, default 30)
  and a revocation time. Missing, malformed, unknown, expired and revoked tokens all
  return `401 UNAUTHENTICATED` with `WWW-Authenticate: Bearer`.
- Roles are `USER` and `MODERATOR`. A valid user without the required role or
  ownership gets `403 FORBIDDEN` (report moderation, uploading photos to someone
  else's report).
- OpenAPI declares the `BearerAuth` scheme on every protected operation.
- Apple/Google/email sign-in is not implemented yet. Their future job is to end by
  issuing one of these tokens. Until then:
  - `POST /api/v1/auth/dev-session {"subject": "..."}` issues a token for subject
    `dev:<subject>`. The route is **registered only when `ENVIRONMENT=DEV`**, so
    elsewhere every request (even a malformed one) gets 404 and it is absent from
    OpenAPI. It can never assign a role.
  - Operators can use `python -m app.auth.cli issue --subject <s> [--role MODERATOR]`
    and `python -m app.auth.cli revoke-user --subject <s>`.
- `POST /api/v1/auth/logout` revokes the current token.

## Preferred vehicle

`PUT /api/v1/me/vehicle {"vehicle": "NORMAL_HEAVY" | "LARGE_HEAVY" | null}`. The app uses it only to
seed the map's selected vehicle; every selected-vehicle request still sends an
explicit `vehicle`, and the server never reads the preference (AGENTS rule 13).

## Favorites

`GET /favorites`, `POST /favorites {"parking_id"}` (201 created, 200 if it already
existed) and `DELETE /favorites/{parking_id}` (204, or 404 `FAVORITE_NOT_FOUND`).
Every query is scoped to the token's user, and `(user_id, parking_id)` is unique.

## Community reports

`POST /reports` (protected) accepts `parking_id`, optional `zone_id` (must belong to
the lot, else 422 `ZONE_NOT_IN_PARKING`), `report_type` and an optional description
(≤1000 characters). Types: PARKING_ALLOWED, PARKING_NOT_ALLOWED, WRONG_SPACE_TYPE,
WRONG_RATE, WRONG_AVAILABILITY, WRONG_ENTRANCE, CLOSED, PLATE_RECOGNITION_FAILED,
GATE_SENSOR_FAILED, OTHER.

- Reports are community evidence only. Nothing writes parking rules, rates, realtime
  or entrances, so a report never overwrites official data. Responses carry
  `provenance.source_type = "COMMUNITY"`.
- Statuses: PENDING -> VERIFIED / REJECTED / SUPERSEDED, set by a moderator through
  `PATCH /reports/{id}/status`. VERIFIED may later become SUPERSEDED; nothing reopens
  a report. The moderator and time are recorded.
- `GET /parking/{id}/reports` is public. It never exposes the author, and free text
  is shown only after the report is VERIFIED. `GET /me/reports` returns the rider's
  own reports with their text.
- Both lists are keyset-paged newest first: `limit` (1–100, default 20) and
  `before=<report id>`; responses include `page.next_before` and `page.has_more`.
  The public list also accepts `status`, so older VERIFIED evidence stays reachable
  behind newer pending reports.
- Promoting verified evidence into normalized facts is a future, separately
  attributed workflow.

## Report photos

`POST /reports/{id}/photos` (multipart `file`, owner only, pending reports only, at
most 3 per report, `REPORT_PHOTO_MAX_BYTES` default 15 MB since #32, so phones can send unmodified originals):

- An ASGI guard rejects photo request bodies larger than the limit + 64 KB with 413
  `PAYLOAD_TOO_LARGE` **before** multipart parsing spools them (declared
  Content-Length is refused unread; streamed bodies stop at the limit).
- Pillow decodes JPEG/PNG/WebP and HEIC/HEIF (pillow-heif, for Android galleries that
  return HEIC), rejects oversized pixel counts, applies EXIF orientation, resizes to
  ≤2048 px and re-encodes JPEG **without metadata**, so phone GPS coordinates are not
  stored. Decoding runs in a worker thread, at most two at a time per process, so
  large uploads do not stall the event loop.
- The object goes to S3-compatible storage at `reports/<report_id>/<uuid>.jpg`
  (server-side encryption requested). `report_photos` stores the key, content type,
  byte size and dimensions, never credentials. If the database write fails the
  object is deleted best effort.
- Storage is configured only from the environment: `S3_BUCKET`, optional
  `S3_ENDPOINT_URL` for non-AWS services, `S3_REGION`, `S3_ACCESS_KEY_ID`,
  `S3_SECRET_ACCESS_KEY`. Without a bucket uploads return 503 `STORAGE_UNAVAILABLE`.
- Photos are not served publicly in v1; moderators read them from storage.

## Mobile

- `AuthController` keeps the token in the platform keychain/keystore
  (`flutter_secure_storage`). It restores the session on launch, signs out locally
  on any 401, and treats network errors as guest mode while keeping the token for
  retry. Only user-scoped requests send the token; parking requests stay anonymous.
- The account sheet (`我的帳號`) shows guest state, the debug-only developer sign-in
  (`--dart-define=DEV_SIGN_IN=true` or debug builds), the preferred vehicle, saved
  favorites (tap to search around a favorite) and sign-out.
- Parking details gain `收藏` and `回報問題`. Guests are offered sign-in instead.
  The report sheet explains that reports are reviewed and do not change official
  data. Photos come from `image_picker` with JPEG output; if the photo upload fails,
  the saved report is kept and the rider is told.
- iOS declares camera and photo-library usage descriptions, and the Runner has a
  `keychain-access-groups` entitlement (empty array, default group) as
  `flutter_secure_storage` requires.
- Picker permission/platform failures show a message in the report sheet. On
  Android, a photo delivered after the activity was recreated is recovered at
  launch (`retrieveLostData`) and offered to the next report.
- Vehicle-preference responses are applied only if they are the latest request for
  the still-active token, so rapid changes or a sign-out cannot restore a stale value.
- Opening a favorite clears any active destination search and its marker.

## Database (migration 004)

- `user_report_type` / `user_report_status` are replaced by the v1 vocabulary.
  Existing rows map RATE/AVAILABILITY/ENTRANCE corrections to their WRONG_* types,
  CLOSURE to CLOSED, ACCEPTED to VERIFIED and UNDER_REVIEW to PENDING; anything
  else becomes OTHER / PENDING. The downgrade maps back the same way.
- `users.role` (USER/MODERATOR) and `users.preferred_vehicle` (NORMAL_HEAVY/LARGE_HEAVY/NULL).
- `user_reports.resolved_by_user_id` (SET NULL on user deletion).
- New `access_tokens` table (unique digest, expiry after creation, cascade on user
  deletion).
