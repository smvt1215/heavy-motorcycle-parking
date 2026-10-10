"""M8 user-scoped API: bearer auth, favorites isolation, reports, photos and moderation."""

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth.tokens import AccessTokenService, token_digest
from app.config import settings
from app.db import get_session
from app.main import create_app
from app.models import (
    AccessToken,
    DataSource,
    Favorite,
    ParkingLot,
    ParkingRule,
    ParkingZone,
    ReportPhoto,
    User,
    UserReport,
    UserRole,
)

NOW = datetime(2026, 10, 7, 4, 0, tzinfo=UTC)
PROTECTED = [
    ("GET", "/api/v1/me", None),
    ("PUT", "/api/v1/me/vehicle", {"vehicle": "LARGE_HEAVY"}),
    ("GET", "/api/v1/me/reports", None),
    ("GET", "/api/v1/favorites", None),
    ("POST", "/api/v1/favorites", {"parking_id": 1}),
    ("DELETE", "/api/v1/favorites/1", None),
    ("POST", "/api/v1/reports", {"parking_id": 1, "report_type": "OTHER"}),
    ("PATCH", "/api/v1/reports/1/status", {"status": "VERIFIED"}),
    ("POST", "/api/v1/auth/logout", None),
]


class MemoryStorage:
    def __init__(self, fail=False):
        self.objects = {}
        self.fail = fail

    async def put(self, key, body, content_type):
        if self.fail:
            from app.services.storage import storage_unavailable

            raise storage_unavailable()
        self.objects[key] = (body, content_type)

    async def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture(scope="module")
def migrated():
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")


@pytest.fixture
async def env(migrated):
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        outer = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            source = DataSource(code="M8-TEST", name="M8", source_type="GOVERNMENT")
            session.add(source)
            await session.flush()
            lots = [
                ParkingLot(name=f"Lot {i}", location=f"SRID=4326;POINT(121.56{i} 25.03{i})", source_id=source.id)
                for i in range(2)
            ]
            session.add_all(lots)
            await session.flush()
            zones = [ParkingZone(parking_id=lot.id, name="Z", space_type="HEAVY_ONLY") for lot in lots]
            session.add_all(zones)
            await session.flush()
            tokens = AccessTokenService(session)
            users = {}
            for name, role in (("alice", None), ("bob", None), ("mod", UserRole.MODERATOR)):
                user = await tokens.ensure_user(f"test:{name}", display_name=name, role=role)
                token, _ = await tokens.issue(user, NOW - timedelta(minutes=1), timedelta(days=1))
                users[name] = (user, token)
            await session.commit()

            app = create_app()
            app.state.clock = lambda: NOW
            app.state.object_storage = MemoryStorage()

            async def override_session():
                yield session

            app.dependency_overrides[get_session] = override_session
            async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
                yield {"client": client, "session": session, "lots": lots, "zones": zones, "users": users, "app": app}
        await outer.rollback()
    await engine.dispose()


def auth(env, name):
    return {"Authorization": f"Bearer {env['users'][name][1]}"}


def jpeg_with_gps(size=(64, 48)):
    image = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x8825] = {2: (25.0, 2.0, 1.0)}  # GPSInfo latitude
    exif[0x010F] = "PhoneMaker"
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


# --- authentication ----------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
@pytest.mark.parametrize(
    "header",
    [
        None,
        "Bearer",
        "Basic dXNlcjpwYXNz",
        "Bearer not-a-token",
        "Bearer hmp_" + "A" * 43,
    ],
)
async def test_protected_endpoints_reject_missing_or_invalid_tokens(env, method, path, body, header):
    headers = {} if header is None else {"Authorization": header}
    response = await env["client"].request(method, path, json=body, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "UNAUTHENTICATED", "message": "A valid access token is required."}}
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


async def test_invalid_body_without_token_is_still_401(env):
    response = await env["client"].post("/api/v1/favorites", json={"parking_id": "oops"})
    assert response.status_code == 401


async def test_expired_and_revoked_tokens_are_rejected(env):
    session, client = env["session"], env["client"]
    alice = env["users"]["alice"][0]
    tokens = AccessTokenService(session)
    expired, _ = await tokens.issue(alice, NOW - timedelta(days=2), timedelta(days=1))
    await session.commit()
    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401

    assert (await client.post("/api/v1/auth/logout", headers=auth(env, "alice"))).status_code == 204
    assert (await client.get("/api/v1/me", headers=auth(env, "alice"))).status_code == 401
    # Other users' sessions are unaffected.
    assert (await client.get("/api/v1/me", headers=auth(env, "bob"))).status_code == 200


async def test_tokens_are_stored_only_as_digests(env):
    session = env["session"]
    token = env["users"]["alice"][1]
    hashes = (await session.scalars(select(AccessToken.token_hash))).all()
    assert token_digest(token) in hashes and token not in hashes


async def test_me_identity_comes_only_from_the_token(env):
    client = env["client"]
    alice = env["users"]["alice"][0]
    bob = env["users"]["bob"][0]
    response = await client.get(
        f"/api/v1/me?user_id={bob.id}", headers={**auth(env, "alice"), "X-User-Id": str(bob.id)}
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": alice.id,
        "display_name": "alice",
        "email": None,
        "role": "USER",
        "preferred_vehicle": None,
    }


async def test_vehicle_preference_is_heavy_only_and_never_drives_parking_queries(env):
    client = env["client"]
    response = await client.put("/api/v1/me/vehicle", json={"vehicle": "NORMAL_HEAVY"}, headers=auth(env, "alice"))
    assert response.status_code == 200 and response.json()["preferred_vehicle"] == "NORMAL_HEAVY"
    for invalid in ("GREEN", "WHITE", "YELLOW", "RED", "CAR", "PINK"):
        response = await client.put("/api/v1/me/vehicle", json={"vehicle": invalid}, headers=auth(env, "alice"))
        assert response.status_code == 422
    cleared = await client.put("/api/v1/me/vehicle", json={"vehicle": None}, headers=auth(env, "alice"))
    assert cleared.json()["preferred_vehicle"] is None
    # Selected-vehicle endpoints still require an explicit vehicle even with a token.
    nearby = await client.get(
        "/api/v1/parking/nearby", params={"lat": 25.03, "lng": 121.56}, headers=auth(env, "alice")
    )
    assert nearby.status_code == 422


# --- favorites ---------------------------------------------------------------------------


async def test_favorite_create_list_remove_and_uniqueness(env):
    client, lot = env["client"], env["lots"][0]
    first = await client.post("/api/v1/favorites", json={"parking_id": lot.id}, headers=auth(env, "alice"))
    again = await client.post("/api/v1/favorites", json={"parking_id": lot.id}, headers=auth(env, "alice"))
    assert (first.status_code, again.status_code) == (201, 200)
    listed = (await client.get("/api/v1/favorites", headers=auth(env, "alice"))).json()["items"]
    assert [(item["parking_id"], item["name"]) for item in listed] == [(lot.id, "Lot 0")]
    assert listed[0]["location"] == pytest.approx({"lat": 25.030, "lng": 121.560})
    assert await env["session"].scalar(select(func.count()).select_from(Favorite)) == 1

    assert (await client.delete(f"/api/v1/favorites/{lot.id}", headers=auth(env, "alice"))).status_code == 204
    missing = await client.delete(f"/api/v1/favorites/{lot.id}", headers=auth(env, "alice"))
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "FAVORITE_NOT_FOUND"
    unknown = await client.post("/api/v1/favorites", json={"parking_id": 999_999}, headers=auth(env, "alice"))
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "PARKING_NOT_FOUND"


async def test_users_cannot_read_create_or_delete_each_others_favorites(env):
    client, session = env["client"], env["session"]
    lot_a, lot_b = env["lots"]
    alice, bob = env["users"]["alice"][0], env["users"]["bob"][0]
    await client.post("/api/v1/favorites", json={"parking_id": lot_a.id}, headers=auth(env, "alice"))

    # Bob cannot read Alice's favorites by supplying her ID anywhere.
    bob_view = await client.get(f"/api/v1/favorites?user_id={alice.id}", headers=auth(env, "bob"))
    assert bob_view.status_code == 200 and bob_view.json()["items"] == []

    # A user_id in the body is rejected, not used to create a favorite for Alice.
    forged = await client.post(
        "/api/v1/favorites", json={"parking_id": lot_b.id, "user_id": alice.id}, headers=auth(env, "bob")
    )
    assert forged.status_code == 422

    # Bob deleting the same lot only touches his own (absent) favorite.
    deleted = await client.delete(f"/api/v1/favorites/{lot_a.id}?user_id={alice.id}", headers=auth(env, "bob"))
    assert deleted.status_code == 404
    owners = (await session.execute(select(Favorite.user_id, Favorite.parking_id))).all()
    assert owners == [(alice.id, lot_a.id)]

    await client.post("/api/v1/favorites", json={"parking_id": lot_a.id}, headers=auth(env, "bob"))
    alice_items = (await client.get("/api/v1/favorites", headers=auth(env, "alice"))).json()["items"]
    assert len(alice_items) == 1
    assert bob.id != alice.id


# --- reports -----------------------------------------------------------------------------


async def create_report(env, name="alice", **fields):
    body = {"parking_id": env["lots"][0].id, "report_type": "PARKING_ALLOWED", **fields}
    return await env["client"].post("/api/v1/reports", json=body, headers=auth(env, name))


async def test_report_is_community_evidence_and_never_changes_official_rules(env):
    session = env["session"]
    lot, zone = env["lots"][0], env["zones"][0]
    rules_before = await session.scalar(select(func.count()).select_from(ParkingRule))
    response = await create_report(env, zone_id=zone.id, description="  重機格在 B2  ")
    assert response.status_code == 201
    report = response.json()
    assert report["status"] == "PENDING" and report["description"] == "重機格在 B2"
    assert report["provenance"] == {"source_type": "COMMUNITY"}
    assert "user_id" not in report
    assert await session.scalar(select(func.count()).select_from(ParkingRule)) == rules_before

    public = (await env["client"].get(f"/api/v1/parking/{lot.id}/reports")).json()["items"]
    # Unmoderated free text is not published.
    assert public[0]["id"] == report["id"] and public[0]["description"] is None
    mine = (await env["client"].get("/api/v1/me/reports", headers=auth(env, "alice"))).json()["items"]
    assert mine[0]["description"] == "重機格在 B2"
    assert (await env["client"].get("/api/v1/me/reports", headers=auth(env, "bob"))).json()["items"] == []


@pytest.mark.parametrize(
    "report_type",
    [
        "PARKING_ALLOWED",
        "PARKING_NOT_ALLOWED",
        "WRONG_SPACE_TYPE",
        "WRONG_RATE",
        "WRONG_AVAILABILITY",
        "WRONG_ENTRANCE",
        "CLOSED",
        "PLATE_RECOGNITION_FAILED",
        "GATE_SENSOR_FAILED",
        "OTHER",
    ],
)
async def test_all_report_types_are_accepted(env, report_type):
    assert (await create_report(env, report_type=report_type)).status_code == 201


async def test_report_validation(env):
    assert (await create_report(env, report_type="PERMISSION_CORRECTION")).status_code == 422
    other_zone = env["zones"][1]
    response = await create_report(env, zone_id=other_zone.id)
    assert response.status_code == 422 and response.json()["error"]["code"] == "ZONE_NOT_IN_PARKING"
    response = await create_report(env, parking_id=999_999)
    assert response.status_code == 404
    assert (await create_report(env, description="x" * 1001)).status_code == 422
    assert (await create_report(env, user_id=1)).status_code == 422
    assert (await env["client"].get("/api/v1/parking/999999/reports")).status_code == 404


async def test_moderation_requires_moderator_and_only_changes_report_status(env):
    client, session = env["client"], env["session"]
    report_id = (await create_report(env, description="確認可停")).json()["id"]
    path = f"/api/v1/reports/{report_id}/status"

    for name in ("alice", "bob"):
        denied = await client.patch(path, json={"status": "VERIFIED"}, headers=auth(env, name))
        assert denied.status_code == 403 and denied.json()["error"]["code"] == "FORBIDDEN"

    verified = await client.patch(path, json={"status": "VERIFIED"}, headers=auth(env, "mod"))
    assert verified.status_code == 200
    assert verified.json()["status"] == "VERIFIED" and verified.json()["resolved_at"] is not None
    public = (await client.get(f"/api/v1/parking/{env['lots'][0].id}/reports")).json()["items"][0]
    assert public["description"] == "確認可停"

    superseded = await client.patch(path, json={"status": "SUPERSEDED"}, headers=auth(env, "mod"))
    assert superseded.json()["status"] == "SUPERSEDED"
    reopened = await client.patch(path, json={"status": "VERIFIED"}, headers=auth(env, "mod"))
    assert reopened.status_code == 409
    assert (await client.patch(path, json={"status": "PENDING"}, headers=auth(env, "mod"))).status_code == 422
    missing = await client.patch("/api/v1/reports/999999/status", json={"status": "REJECTED"}, headers=auth(env, "mod"))
    assert missing.status_code == 404
    row = await session.get(UserReport, report_id)
    assert row.resolved_by_user_id == env["users"]["mod"][0].id
    assert await session.scalar(select(func.count()).select_from(ParkingRule)) == 0


# --- photos ------------------------------------------------------------------------------


async def upload(env, report_id, data, name="alice", filename="p.jpg"):
    return await env["client"].post(
        f"/api/v1/reports/{report_id}/photos",
        files={"file": (filename, data, "image/jpeg")},
        headers=auth(env, name),
    )


async def test_photo_upload_strips_metadata_and_persists_storage_reference(env):
    report_id = (await create_report(env)).json()["id"]
    original = jpeg_with_gps()
    assert b"PhoneMaker" in original
    response = await upload(env, report_id, original)
    assert response.status_code == 201
    body = response.json()
    assert (body["width"], body["height"], body["content_type"]) == (64, 48, "image/jpeg")
    storage = env["app"].state.object_storage
    (key, (stored, content_type)), *_ = storage.objects.items()
    assert key.startswith(f"reports/{report_id}/") and content_type == "image/jpeg"
    with Image.open(io.BytesIO(stored)) as image:
        assert not image.getexif()
    assert b"PhoneMaker" not in stored
    photo = (await env["session"].scalars(select(ReportPhoto))).one()
    assert photo.storage_key == key and photo.byte_size == len(stored)
    report = (await env["client"].get("/api/v1/me/reports", headers=auth(env, "alice"))).json()["items"][0]
    assert report["photo_count"] == 1


async def test_photo_permissions_limits_and_validation(env):
    report_id = (await create_report(env)).json()["id"]
    denied = await upload(env, report_id, jpeg_with_gps(), name="bob")
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "FORBIDDEN"
    assert (await upload(env, 999_999, jpeg_with_gps())).status_code == 404
    bad = await upload(env, report_id, b"<svg></svg>", filename="x.svg")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_PHOTO"
    gif = io.BytesIO()
    Image.new("RGB", (4, 4)).save(gif, format="GIF")
    assert (await upload(env, report_id, gif.getvalue())).status_code == 422

    huge = await upload(env, report_id, b"\xff" * (settings.report_photo_max_bytes + 1))
    assert huge.status_code == 413 and huge.json()["error"]["code"] == "PHOTO_TOO_LARGE"
    # Bodies far beyond the limit are refused before multipart parsing spools them.
    oversized = await upload(env, report_id, b"\xff" * (settings.report_photo_max_bytes + 200_000))
    assert oversized.status_code == 413 and oversized.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    for _ in range(3):
        assert (await upload(env, report_id, jpeg_with_gps())).status_code == 201
    limit = await upload(env, report_id, jpeg_with_gps())
    assert limit.status_code == 409 and limit.json()["error"]["code"] == "PHOTO_LIMIT_REACHED"

    await env["client"].patch(
        f"/api/v1/reports/{report_id}/status", json={"status": "REJECTED"}, headers=auth(env, "mod")
    )
    other = (await create_report(env)).json()["id"]
    await env["client"].patch(f"/api/v1/reports/{other}/status", json={"status": "REJECTED"}, headers=auth(env, "mod"))
    closed = await upload(env, other, jpeg_with_gps())
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "REPORT_CLOSED"


async def test_storage_unavailable_keeps_no_photo_row(env):
    report_id = (await create_report(env)).json()["id"]
    env["app"].state.object_storage = MemoryStorage(fail=True)
    failed = await upload(env, report_id, jpeg_with_gps())
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    env["app"].state.object_storage = None
    assert (await upload(env, report_id, jpeg_with_gps())).status_code == 503
    assert await env["session"].scalar(select(func.count()).select_from(ReportPhoto)) == 0


# --- guest mode, dev sessions and OpenAPI ------------------------------------------------


async def test_guest_can_use_public_endpoints(env):
    client, lot = env["client"], env["lots"][0]
    assert (await client.get(f"/api/v1/parking/{lot.id}?vehicle=LARGE_HEAVY")).status_code == 200
    assert (await client.get(f"/api/v1/parking/{lot.id}/reports")).status_code == 200
    assert (await client.get("/api/v1/me")).status_code == 401


async def test_dev_session_only_in_dev(env, monkeypatch):
    client = env["client"]
    response = await client.post("/api/v1/auth/dev-session", json={"subject": "rider-1", "display_name": "Rider"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    me = (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})).json()
    assert me["display_name"] == "Rider" and me["role"] == "USER"
    user = (await env["session"].scalars(select(User).where(User.id == me["id"]))).one()
    assert user.auth_subject == "dev:rider-1"
    # A dev session can never self-assign a role.
    forged = await client.post("/api/v1/auth/dev-session", json={"subject": "x", "role": "MODERATOR"})
    assert forged.status_code == 422


async def test_dev_session_route_does_not_exist_outside_dev(monkeypatch):
    monkeypatch.setattr(settings, "environment", "PROD")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        for body in ({"subject": "rider-2"}, {}, None):
            response = await client.post("/api/v1/auth/dev-session", json=body)
            assert response.status_code == 404
    assert "/api/v1/auth/dev-session" not in app.openapi()["paths"]


def test_openapi_marks_protected_operations_with_bearer_scheme():
    schema = create_app().openapi()
    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "description": "Opaque access token",
    }
    for method, path, _ in PROTECTED:
        template = path.replace("/1", "/{parking_id}") if "favorites/1" in path else path
        template = template.replace("/reports/1/", "/reports/{report_id}/")
        assert schema["paths"][template][method.lower()]["security"] == [{"BearerAuth": []}], path
    assert schema["paths"]["/api/v1/reports/{report_id}/photos"]["post"]["security"] == [{"BearerAuth": []}]
    for public in ("/api/v1/parking/nearby", "/api/v1/parking/{parking_id}/reports", "/api/v1/places/autocomplete"):
        assert "security" not in schema["paths"][public]["get"]


async def test_photo_upload_accepts_heic_and_reencodes_jpeg(env):
    report_id = (await create_report(env)).json()["id"]
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 120, 200)).save(buffer, format="HEIF")
    response = await upload(env, report_id, buffer.getvalue(), filename="IMG_0001.HEIC")
    assert response.status_code == 201
    assert (response.json()["content_type"], response.json()["width"]) == ("image/jpeg", 40)
    (stored, _), *_ = env["app"].state.object_storage.objects.values()
    assert stored[:3] == b"\xff\xd8\xff"


async def test_report_lists_are_keyset_paged_and_filterable(env):
    client, lot = env["client"], env["lots"][0]
    ids = [(await create_report(env, report_type="OTHER")).json()["id"] for _ in range(5)]
    await client.patch(f"/api/v1/reports/{ids[0]}/status", json={"status": "VERIFIED"}, headers=auth(env, "mod"))

    seen, before = [], None
    while True:
        params = {"limit": 2, **({"before": before} if before else {})}
        body = (await client.get(f"/api/v1/parking/{lot.id}/reports", params=params)).json()
        seen += [item["id"] for item in body["items"]]
        if not body["page"]["has_more"]:
            assert body["page"]["next_before"] is None
            break
        before = body["page"]["next_before"]
    assert seen == sorted(ids, reverse=True)

    # Older verified evidence stays reachable even behind newer pending reports.
    verified = (await client.get(f"/api/v1/parking/{lot.id}/reports", params={"status": "VERIFIED", "limit": 1})).json()
    assert [item["id"] for item in verified["items"]] == [ids[0]]

    mine = (await client.get("/api/v1/me/reports", params={"limit": 3}, headers=auth(env, "alice"))).json()
    assert len(mine["items"]) == 3 and mine["page"]["has_more"] is True
    for bad in ({"limit": 0}, {"limit": 101}, {"before": 0}, {"user_id": 1}):
        response = await client.get("/api/v1/me/reports", params=bad, headers=auth(env, "alice"))
        assert response.status_code == 422
