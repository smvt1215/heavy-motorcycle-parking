"""Community verification API: corroboration, objections, review, points, media and identity."""

import asyncio
import contextlib
import io
import itertools
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth.tokens import AccessTokenService
from app.config import settings
from app.db import get_session
from app.main import create_app
from app.models import (
    CommunityCase,
    CommunityEvidencePhoto,
    CommunityParticipant,
    ContributionLedgerEntry,
    DataSource,
    ParkingLot,
    ParkingRule,
    ParkingZone,
    StorageDeletion,
    User,
    UserRole,
)

NOW = datetime(2026, 10, 10, 4, 0, tzinfo=UTC)  # 12:00 Asia/Taipei
TAIPEI = timezone(timedelta(hours=8))
PEOPLE = ("alice", "bob", "carol", "dave", "erin", "frank")
LIGHT = {"present": True}
_colors = itertools.count(1)


class MemoryStorage:
    def __init__(self):
        self.objects = {}

    async def put(self, key, body, content_type):
        self.objects[key] = (body, content_type)

    async def delete(self, key):
        self.objects.pop(key, None)

    async def get(self, key):
        return self.objects[key][0]


class AllowAll:
    def __init__(self):
        self.allowed = True

    async def allow(self, key):
        return self.allowed


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
            source = DataSource(code="COMMUNITY-TEST", name="Test", source_type="GOVERNMENT")
            session.add(source)
            await session.flush()
            lots = [
                ParkingLot(
                    name=f"Lot {i}", city="臺北市", location=f"SRID=4326;POINT(121.56{i} 25.03{i})", source_id=source.id
                )
                for i in range(2)
            ]
            session.add_all(lots)
            await session.flush()
            zones = [ParkingZone(parking_id=lot.id, name="Z", space_type="MOTO_SHARED") for lot in lots]
            session.add_all(zones)
            await session.flush()
            tokens = AccessTokenService(session)
            users = {}
            for name in (*PEOPLE, "mod", "mod2"):
                role = UserRole.MODERATOR if name.startswith("mod") else None
                user = await tokens.ensure_user(f"test:{name}", display_name=name, role=role)
                token, _ = await tokens.issue(user, NOW - timedelta(minutes=1), timedelta(days=400))
                users[name] = (user, token)
            await session.commit()
            app = create_app()
            clock = [NOW]
            app.state.clock = lambda: clock[0]
            app.state.object_storage = MemoryStorage()
            app.state.community_rate_limiter = AllowAll()
            app.state.community_auto_publish_enabled = True

            async def override():
                yield session

            app.dependency_overrides[get_session] = override
            async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
                yield {
                    "client": client,
                    "session": session,
                    "lots": lots,
                    "zones": zones,
                    "users": users,
                    "app": app,
                    "clock": clock,
                    "source": source,
                }
        await outer.rollback()
    await engine.dispose()


def auth(env, name, key=None):
    headers = {"Authorization": f"Bearer {env['users'][name][1]}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def jpeg(*, taken=NOW - timedelta(hours=1), offset="+08:00", exif_time=True, gps=True):
    """A distinct JPEG whose EXIF original time is `taken` rendered in `offset`."""
    color = next(_colors)
    image = Image.new("RGB", (48, 32), (color % 256, (color * 7) % 256, (color * 13) % 256))
    exif = Image.Exif()
    if gps:
        exif[0x8825] = {2: (25.0, 2.0, 1.0)}
    if exif_time:
        # An empty offset writes Taipei local time without OffsetTimeOriginal.
        zone = offset or "+08:00"
        sign = -1 if zone.startswith("-") else 1
        hours, minutes = map(int, zone[1:].split(":"))
        local = taken.astimezone(timezone(sign * timedelta(hours=hours, minutes=minutes)))
        ifd = exif.get_ifd(0x8769)
        ifd[36867] = local.strftime("%Y:%m:%d %H:%M:%S")
        if offset:
            ifd[36881] = offset
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


_keys = itertools.count()


def key():
    return f"k-{next(_keys)}"


async def create(env, name="alice", **body):
    payload = {"parking_id": env["lots"][0].id, "fact_type": "LIGHTING", "proposed_value": LIGHT, **body}
    response = await env["client"].post("/api/v1/community/cases", json=payload, headers=auth(env, name, key()))
    assert response.status_code == 201, response.text
    return response.json()


async def upload(env, case_id, name, owner, data=None, expect=201):
    response = await env["client"].post(
        f"/api/v1/community/cases/{case_id}/photos",
        files={"file": ("p.jpg", data if data is not None else jpeg(), "image/jpeg")},
        data={"owner": owner},
        headers=auth(env, name),
    )
    assert response.status_code == expect, response.text
    return response.json()


async def stance(env, case_id, name, value="SUPPORT", observed=LIGHT, expect=200):
    body = {"stance": value, "observed_value": observed if value != "CANNOT_CONFIRM" else None}
    response = await env["client"].put(
        f"/api/v1/community/cases/{case_id}/stance", json=body, headers=auth(env, name, key())
    )
    assert response.status_code == expect, response.text
    return response.json()


async def support(env, case_id, name, data=None, observed=LIGHT, value="SUPPORT"):
    await stance(env, case_id, name, value, observed)
    return await upload(env, case_id, name, "stance", data)


async def detail(env, case_id, name="alice"):
    response = await env["client"].get(f"/api/v1/me/community/cases/{case_id}", headers=auth(env, name))
    assert response.status_code == 200, response.text
    return response.json()


async def moderation(env, case_id, name="mod"):
    response = await env["client"].get(f"/api/v1/moderation/cases/{case_id}", headers=auth(env, name))
    assert response.status_code == 200, response.text
    return response.json()


async def decide(env, case_id, decision, name="mod", expect=200, **extra):
    version = (await moderation(env, case_id, name))["case"]["version"]
    body = {
        "decision": decision,
        "reason_code": "TEST_REASON",
        "reason_text": "審查理由",
        "expected_version": version,
        **extra,
    }
    response = await env["client"].post(
        f"/api/v1/moderation/cases/{case_id}/decisions", json=body, headers=auth(env, name, key())
    )
    assert response.status_code == expect, response.text
    return response.json()


async def points(env, name):
    response = await env["client"].get("/api/v1/me/contributions", headers=auth(env, name))
    assert response.status_code == 200
    return response.json()


async def observations(env):
    response = await env["client"].get(f"/api/v1/parking/{env['lots'][0].id}/community-observations")
    assert response.status_code == 200
    return response.json()["items"]


async def published_case(env):
    case = await create(env)
    await upload(env, case["id"], "alice", "revision")
    for name in ("bob", "carol", "dave"):
        await support(env, case["id"], name)
    return case


# --- authentication and authorization ---------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("POST", "/api/v1/community/cases"),
        ("POST", "/api/v1/community/cases/1/revisions"),
        ("PUT", "/api/v1/community/cases/1/stance"),
        ("DELETE", "/api/v1/community/cases/1/stance"),
        ("GET", "/api/v1/me/community/cases"),
        ("GET", "/api/v1/me/community/cases/1"),
        ("GET", "/api/v1/me/contributions"),
        ("GET", "/api/v1/community/photos/1"),
        ("GET", "/api/v1/moderation/cases"),
        ("GET", "/api/v1/moderation/cases/1"),
        ("POST", "/api/v1/moderation/cases/1/decisions"),
        ("DELETE", "/api/v1/moderation/photos/1"),
        ("POST", "/api/v1/moderation/source-verifications"),
        ("PUT", "/api/v1/moderation/participants/1/suspension"),
    ],
)
async def test_protected_endpoints_require_bearer_identity(env, method, path):
    response = await env["client"].request(method, path, json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_moderation_requires_moderator_role(env):
    case = await create(env)
    for method, path in (
        ("GET", "/api/v1/moderation/cases"),
        ("GET", f"/api/v1/moderation/cases/{case['id']}"),
    ):
        response = await env["client"].request(method, path, headers=auth(env, "bob"))
        assert response.status_code == 403


async def test_identity_comes_from_token_not_body(env):
    response = await env["client"].post(
        "/api/v1/community/cases",
        json={"parking_id": env["lots"][0].id, "fact_type": "LIGHTING", "proposed_value": LIGHT, "user_id": 99},
        headers=auth(env, "alice", key()),
    )
    assert response.status_code == 422


def test_openapi_declares_bearer_on_every_community_operation():
    spec = create_app().openapi()
    for path, operations in spec["paths"].items():
        if path.startswith(("/api/v1/community", "/api/v1/moderation", "/api/v1/me/community", "/api/v1/me/contrib")):
            for operation in operations.values():
                assert {"BearerAuth": []} in operation.get("security", []), path


# --- submission scope and idempotency ----------------------------------------------------


@pytest.mark.parametrize(
    "body, code",
    [
        ({"fact_type": "PARKING_PERMISSION", "proposed_value": {"allowed": True}}, "SCOPE_INVALID"),
        ({"fact_type": "LIGHTING", "vehicle": "LARGE_HEAVY"}, "SCOPE_INVALID"),
        ({"fact_type": "LIGHTING", "proposed_value": {"present": 1}}, "VALUE_INVALID"),
        ({"fact_type": "LIGHTING", "proposed_value": {"present": True, "extra": 1}}, "VALUE_INVALID"),
        ({"fact_type": "ENTRANCE_LOCATION", "proposed_value": {"latitude": 0, "longitude": 0}}, "VALUE_INVALID"),
    ],
)
async def test_scope_and_value_are_validated(env, body, code):
    payload = {"parking_id": env["lots"][0].id, "proposed_value": LIGHT, **body}
    response = await env["client"].post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", key()))
    assert response.status_code == 422 and response.json()["error"]["code"] == code


async def test_car_is_not_a_rider_vehicle(env):
    payload = {
        "parking_id": env["lots"][0].id,
        "zone_id": env["zones"][0].id,
        "fact_type": "PARKING_PERMISSION",
        "vehicle": "CAR",
        "proposed_value": {"allowed": True},
    }
    response = await env["client"].post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", key()))
    assert response.status_code == 422


async def test_idempotency_key_required_replayed_and_reuse_rejected(env):
    payload = {"parking_id": env["lots"][0].id, "fact_type": "LIGHTING", "proposed_value": LIGHT}
    client = env["client"]
    missing = await client.post("/api/v1/community/cases", json=payload, headers=auth(env, "alice"))
    assert missing.status_code == 428 and missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    first = await client.post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", "same"))
    again = await client.post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", "same"))
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    changed = await client.post(
        "/api/v1/community/cases",
        json={**payload, "proposed_value": {"present": False}},
        headers=auth(env, "alice", "same"),
    )
    assert changed.status_code == 422 and changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    # Keys are per user: another rider may use the same string.
    other = await client.post("/api/v1/community/cases", json=payload, headers=auth(env, "bob", "same"))
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]
    mine = await client.get("/api/v1/me/community/cases", headers=auth(env, "alice"))
    assert len(mine.json()["items"]) == 1
    # Records last 24 hours; afterwards the key starts a new request.
    env["clock"][0] = NOW + timedelta(hours=24)
    later = await client.post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", "same"))
    assert later.status_code == 201 and later.json()["id"] != first.json()["id"]


async def test_rate_limit_applies_to_community_writes(env):
    env["app"].state.community_rate_limiter.allowed = False
    payload = {"parking_id": env["lots"][0].id, "fact_type": "LIGHTING", "proposed_value": LIGHT}
    response = await env["client"].post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", key()))
    assert response.status_code == 429 and response.json()["error"]["code"] == "RATE_LIMITED"


# --- photo metadata time ------------------------------------------------------------------


async def test_photo_time_is_extracted_before_metadata_is_stripped(env):
    case = await create(env)
    valid = await upload(env, case["id"], "alice", "revision", jpeg(taken=NOW - timedelta(hours=2)))
    assert valid["metadata_time"] == {
        "status": "VALID",
        "captured_at": "2026-10-10T02:00:00Z",
        "datetime_original": "2026:10:10 10:00:00",
        "offset_time_original": "+08:00",
    }
    assert valid["counts_toward_corroboration"] is True and valid["message_code"] == "ELIGIBLE"
    stored = next(iter(env["app"].state.object_storage.objects.values()))[0]
    with Image.open(io.BytesIO(stored)) as image:
        assert not image.getexif()  # no GPS, no EXIF in the stored object
    row = (await env["session"].scalars(select(CommunityEvidencePhoto))).one()
    assert len(row.normalized_sha256) == 64 and row.time_parser_version == "exif-time-1"


@pytest.mark.parametrize(
    "data, status",
    [
        (lambda: jpeg(exif_time=False), "MISSING"),
        (lambda: jpeg(offset=""), "TIMEZONE_UNKNOWN"),
        (lambda: jpeg(taken=NOW + timedelta(minutes=5)), "FUTURE"),
        (lambda: jpeg(taken=NOW - timedelta(days=31)), "OUTSIDE_WINDOW"),
    ],
)
async def test_unverified_photo_time_is_admissible_but_needs_manual_review(env, data, status):
    case = await create(env)
    photo = await upload(env, case["id"], "alice", "revision", data())
    assert photo["metadata_time"]["status"] == status
    assert photo["counts_toward_corroboration"] is False and photo["message_code"] == "MANUAL_REVIEW_REQUIRED"
    assert (await detail(env, case["id"]))["case"]["review_status"] == "MANUAL_REVIEW"


async def test_case_without_upload_waits_and_client_cannot_supply_time(env):
    case = await create(env)
    assert case["review_status"] == "AWAITING_CORROBORATION"
    response = await env["client"].post(
        f"/api/v1/community/cases/{case['id']}/photos",
        files={"file": ("p.jpg", jpeg(), "image/jpeg")},
        data={"owner": "revision", "captured_at": "2026-10-10T00:00:00Z"},
        headers=auth(env, "alice"),
    )
    assert response.status_code == 201
    assert response.json()["metadata_time"]["captured_at"] != "2026-10-10T00:00:00Z"


# --- corroboration and publication ----------------------------------------------------------


async def test_three_independent_supporters_publish_for_90_days_with_points(env):
    case = await create(env)
    await upload(env, case["id"], "alice", "revision")
    await support(env, case["id"], "bob")
    await support(env, case["id"], "carol")
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "UNPUBLISHED"
    assert await observations(env) == []
    await support(env, case["id"], "dave")
    summary = (await detail(env, case["id"]))["case"]
    assert summary["review_status"] == "ACCEPTED" and summary["publication_status"] == "PUBLISHED"
    assert summary["publication_basis"] == "COMMUNITY_CORROBORATED"
    assert summary["first_published_at"] == "2026-10-10T04:00:00Z"
    assert summary["published_until"] == "2027-01-08T04:00:00Z"
    (item,) = await observations(env)
    assert item["corroborator_count"] == 3 and item["value"] == LIGHT and item["label"] is None
    assert item["provenance"] == {"source_type": "COMMUNITY"}
    assert "author" not in str(item) and "participant" not in str(item)
    for name in ("alice", "bob", "carol", "dave"):
        assert (await points(env, name))["points"] == 1
    assert (await points(env, "alice"))["level"] == "L1"


async def test_author_cannot_support_and_duplicate_images_do_not_count(env):
    case = await create(env)
    original = jpeg()
    await upload(env, case["id"], "alice", "revision", original)
    await stance(env, case["id"], "alice", expect=403)
    duplicate = await support(env, case["id"], "bob", original)
    assert duplicate["counts_toward_corroboration"] is False and duplicate["message_code"] == "DUPLICATE_EVIDENCE"
    await support(env, case["id"], "carol")
    await support(env, case["id"], "dave")
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "UNPUBLISHED"
    await support(env, case["id"], "erin")
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "PUBLISHED"


async def test_cannot_confirm_and_support_without_photo_do_not_count(env):
    case = await create(env)
    await upload(env, case["id"], "alice", "revision")
    await stance(env, case["id"], "bob", "CANNOT_CONFIRM")
    await upload(env, case["id"], "bob", "stance", expect=409)
    await stance(env, case["id"], "carol")
    await support(env, case["id"], "dave")
    await support(env, case["id"], "erin")
    assert (await detail(env, case["id"]))["case"]["supporters"] == 2


async def test_disabled_flag_sends_eligible_cases_to_manual_review(env):
    env["app"].state.community_auto_publish_enabled = False
    case = await published_case(env)
    summary = (await detail(env, case["id"]))["case"]
    assert summary["publication_status"] == "UNPUBLISHED" and summary["review_status"] == "MANUAL_REVIEW"
    assert (await points(env, "alice"))["points"] == 0


async def test_repeated_prechecks_never_award_twice(env):
    case = await published_case(env)
    await stance(env, case["id"], "erin", "CANNOT_CONFIRM")
    await stance(env, case["id"], "erin", "CANNOT_CONFIRM")
    awards = await env["session"].scalar(
        select(func.count()).select_from(ContributionLedgerEntry).where(ContributionLedgerEntry.entry_type == "AWARD")
    )
    assert awards == 4


async def test_late_valid_corroboration_during_publication_earns_one_point(env):
    case = await published_case(env)
    await support(env, case["id"], "erin")
    assert (await points(env, "erin"))["points"] == 1


async def test_withdrawn_support_below_threshold_suspends_and_freezes(env):
    case = await published_case(env)
    response = await env["client"].delete(
        f"/api/v1/community/cases/{case['id']}/stance", headers=auth(env, "bob", key())
    )
    assert response.status_code == 204
    summary = (await detail(env, case["id"]))["case"]
    assert summary["publication_status"] == "SUSPENDED" and summary["review_status"] == "MANUAL_REVIEW"
    assert await observations(env) == []
    assert (await points(env, "alice"))["points"] == 0  # frozen, not removed


# --- objections and manual review -------------------------------------------------------------


async def test_valid_objection_suspends_and_manual_accept_resumes_without_new_term(env):
    case = await published_case(env)
    before = (await detail(env, case["id"]))["case"]["published_until"]
    await support(env, case["id"], "erin", value="OPPOSE", observed={"present": False})
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "SUSPENDED"
    queue = (await env["client"].get("/api/v1/moderation/cases", headers=auth(env, "mod"))).json()["items"]
    assert queue[0]["case"]["id"] == case["id"]
    env["clock"][0] = NOW + timedelta(days=10)
    resumed = await decide(env, case["id"], "ACCEPT")
    assert resumed["publication_status"] == "PUBLISHED" and resumed["published_until"] == before
    assert (await points(env, "alice"))["points"] == 1
    stances = (await moderation(env, case["id"]))["stances"]
    assert any(s["invalidated_reason"] == "OVERRULED" for s in stances)


async def test_invalid_objection_without_eligible_evidence_does_not_suspend(env):
    case = await published_case(env)
    await stance(env, case["id"], "erin", "OPPOSE", {"present": False})
    await upload(env, case["id"], "erin", "stance", jpeg(exif_time=False))
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "PUBLISHED"


async def test_reject_after_objection_withdraws_revokes_and_upholds_objection(env):
    case = await published_case(env)
    await support(env, case["id"], "erin", value="OPPOSE", observed={"present": False})
    rejected = await decide(env, case["id"], "REJECT")
    assert rejected["publication_status"] == "WITHDRAWN" and rejected["review_status"] == "REJECTED"
    for name in ("alice", "bob", "carol", "dave"):
        assert (await points(env, name))["points"] == 0
    erin = await points(env, "erin")
    assert erin["points"] == 1 and erin["entries"][0]["reason"] == "UPHELD_OBJECTION"
    await stance(env, case["id"], "frank", expect=409)


async def test_recusal_version_conflict_and_reasons(env):
    case = await create(env)
    await upload(env, case["id"], "alice", "revision", jpeg(exif_time=False))
    await stance(env, case["id"], "mod2", "CANNOT_CONFIRM")
    recused = await moderation(env, case["id"], "mod2")
    assert recused["recused"] is True
    await decide(env, case["id"], "ACCEPT", name="mod2", expect=403)
    stale = {
        "decision": "ACCEPT",
        "reason_code": "OK",
        "reason_text": "理由",
        "expected_version": 1,
    }
    response = await env["client"].post(
        f"/api/v1/moderation/cases/{case['id']}/decisions", json=stale, headers=auth(env, "mod", key())
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "VERSION_CONFLICT"
    missing_reason = {**stale, "reason_text": " "}
    response = await env["client"].post(
        f"/api/v1/moderation/cases/{case['id']}/decisions", json=missing_reason, headers=auth(env, "mod", key())
    )
    assert response.status_code == 422


async def test_manual_accept_of_unverified_photo_is_labelled_and_does_not_validate_time(env):
    case = await create(env, fact_type="ENTRANCE_LOCATION", proposed_value={"latitude": 25.03, "longitude": 121.56})
    await upload(env, case["id"], "alice", "revision", jpeg(exif_time=False))
    accepted = await decide(env, case["id"], "ACCEPT")
    assert accepted["publication_basis"] == "MANUAL_REVIEW"
    assert accepted["published_until"] == "2027-01-08T04:00:00Z"
    (item,) = await observations(env)
    assert item["label"] == "人工複審採納位置・通行性未確認" and item["corroborator_count"] is None
    photo = (await detail(env, case["id"]))["my_photos"][0]
    assert photo["metadata_time"]["status"] == "MISSING"


async def test_request_evidence_creates_a_new_revision(env):
    case = await create(env)
    await upload(env, case["id"], "alice", "revision", jpeg(exif_time=False))
    await decide(env, case["id"], "REQUEST_EVIDENCE")
    body = {"proposed_value": {"present": False}}
    other = await env["client"].post(
        f"/api/v1/community/cases/{case['id']}/revisions", json=body, headers=auth(env, "bob", key())
    )
    assert other.status_code == 403
    revised = await env["client"].post(
        f"/api/v1/community/cases/{case['id']}/revisions", json=body, headers=auth(env, "alice", key())
    )
    assert revised.status_code == 201 and revised.json()["current_revision"] == 2
    full = await detail(env, case["id"])
    assert [r["revision"] for r in full["revisions"]] == [1, 2]
    assert full["revisions"][0]["proposed_value"] == LIGHT  # history kept
    events = [e["event_type"] for e in full["timeline"]]
    assert "EVIDENCE_REQUESTED" in events and "REVISION_SUBMITTED" in events
    requested = next(e for e in full["timeline"] if e["event_type"] == "EVIDENCE_REQUESTED")
    assert requested["actor"] == "MODERATOR" and requested["reason_text"] == "審查理由"
    again = await env["client"].post(
        f"/api/v1/community/cases/{case['id']}/revisions", json=body, headers=auth(env, "alice", key())
    )
    assert again.status_code == 409


async def test_supersede_needs_a_successor_on_the_same_lot(env):
    first = await create(env)
    second = await create(env)
    await decide(env, first["id"], "SUPERSEDE", expect=422, superseded_by_case_id=first["id"])
    result = await decide(env, first["id"], "SUPERSEDE", superseded_by_case_id=second["id"])
    assert result["review_status"] == "SUPERSEDED"


async def test_source_resolved_facts_need_manual_review_and_never_write_rules(env):
    rules_before = await env["session"].scalar(select(func.count()).select_from(ParkingRule))
    case = await create(
        env,
        zone_id=env["zones"][0].id,
        fact_type="PARKING_PERMISSION",
        vehicle="LARGE_HEAVY",
        proposed_value={"allowed": True},
    )
    await upload(env, case["id"], "alice", "revision")
    for name in ("bob", "carol", "dave"):
        await support(env, case["id"], name, observed={"allowed": True})
    summary = (await detail(env, case["id"]))["case"]
    assert summary["publication_status"] == "UNPUBLISHED" and summary["review_status"] == "MANUAL_REVIEW"
    accepted = await decide(env, case["id"], "ACCEPT")
    assert accepted["publication_basis"] == "MANUAL_REVIEW" and accepted["vehicle"] == "LARGE_HEAVY"
    assert await env["session"].scalar(select(func.count()).select_from(ParkingRule)) == rules_before


# --- expiry ------------------------------------------------------------------------------------


async def test_expiry_is_derived_and_a_new_round_needs_a_new_case(env):
    case = await published_case(env)
    env["clock"][0] = NOW + timedelta(days=90)
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "EXPIRED"
    assert await observations(env) == []
    response = await env["client"].put(
        f"/api/v1/community/cases/{case['id']}/stance",
        json={"stance": "SUPPORT", "observed_value": LIGHT},
        headers=auth(env, "erin", key()),
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "CASE_EXPIRED"
    # Natural expiry keeps lawfully earned points.
    assert (await points(env, "alice"))["points"] == 1


# --- private media -------------------------------------------------------------------------------


async def test_private_photo_access_and_moderator_deletion(env):
    case = await published_case(env)
    photo_id = (await detail(env, case["id"]))["my_photos"][0]["photo_id"]
    path = f"/api/v1/community/photos/{photo_id}"
    own = await env["client"].get(path, headers=auth(env, "alice"))
    assert own.status_code == 200 and own.headers["content-type"] == "image/jpeg"
    assert "no-store" in own.headers["cache-control"]
    assert (await env["client"].get(path, headers=auth(env, "bob"))).status_code == 403
    assert (await env["client"].get(path, headers=auth(env, "mod"))).status_code == 200
    assert (await env["client"].get(path)).status_code == 401
    # Bob sees only his own uploads in the case detail.
    assert all(p["photo_id"] != photo_id for p in (await detail(env, case["id"], "bob"))["my_photos"])
    row = await env["session"].get(CommunityEvidencePhoto, photo_id)
    stored_key = row.storage_key
    assert stored_key in env["app"].state.object_storage.objects
    deleted = await env["client"].delete(f"/api/v1/moderation/photos/{photo_id}", headers=auth(env, "mod"))
    assert deleted.status_code == 204
    assert (await env["client"].get(path, headers=auth(env, "mod"))).status_code == 404
    await env["session"].refresh(row)
    assert row.storage_key is None and row.exif_datetime_original is None and row.deleted_at is not None
    assert row.time_status == "VALID" and row.normalized_sha256  # audit and duplicate detection remain
    assert stored_key not in env["app"].state.object_storage.objects
    # The original evidence is gone, so the community observation is suspended for review.
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "SUSPENDED"


# --- participant suspension ----------------------------------------------------------------------


async def test_suspended_participant_cannot_submit_and_stops_counting(env):
    case = await published_case(env)
    bob = (await moderation(env, case["id"]))["stances"][0]["participant_id"]
    response = await env["client"].put(
        f"/api/v1/moderation/participants/{bob}/suspension",
        json={"reason_code": "ABUSE"},
        headers=auth(env, "mod"),
    )
    assert response.status_code == 200 and response.json()["suspended_reason"] == "ABUSE"
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "SUSPENDED"
    blocked = await env["client"].post(
        "/api/v1/community/cases",
        json={"parking_id": env["lots"][0].id, "fact_type": "LIGHTING", "proposed_value": LIGHT},
        headers=auth(env, "bob", key()),
    )
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "PARTICIPANT_SUSPENDED"
    lifted = await env["client"].delete(f"/api/v1/moderation/participants/{bob}/suspension", headers=auth(env, "mod"))
    assert lifted.status_code == 200 and lifted.json()["suspended_at"] is None


# --- source verification ---------------------------------------------------------------------------


async def test_source_verification_configuration_and_revocation(env):
    base = {
        "source_id": env["source"].id,
        "fact_kind": "PERMISSION",
        "parking_id": env["lots"][0].id,
        "evidence_url": "https://operator.example/lot",
    }
    bad = await env["client"].post("/api/v1/moderation/source-verifications", json=base, headers=auth(env, "mod"))
    assert bad.status_code == 422
    user = await env["client"].post(
        "/api/v1/moderation/source-verifications",
        json={**base, "rule_kind": "BASELINE", "authority_priority": 150},
        headers=auth(env, "bob"),
    )
    assert user.status_code == 403
    created = await env["client"].post(
        "/api/v1/moderation/source-verifications",
        json={**base, "rule_kind": "BASELINE", "authority_priority": 150},
        headers=auth(env, "mod"),
    )
    assert created.status_code == 201 and created.json()["verified_at"] == "2026-10-10T04:00:00Z"
    path = f"/api/v1/moderation/source-verifications/{created.json()['id']}/revoke"
    revoked = await env["client"].post(path, headers=auth(env, "mod"))
    assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None
    assert (await env["client"].post(path, headers=auth(env, "mod"))).status_code == 409


async def test_moderation_detail_shows_preview_and_private_evidence(env):
    case = await create(env)
    await upload(env, case["id"], "alice", "revision", jpeg(exif_time=False))
    await support(env, case["id"], "bob")
    view = await moderation(env, case["id"])
    assert view["preview"]["accept_basis"] == "MANUAL_REVIEW"
    assert view["preview"]["award_corroboration"] == [view["stances"][0]["participant_id"]]
    assert len(view["photos"]) == 2 and all(len(p["normalized_sha256"]) == 64 for p in view["photos"])
    assert view["precheck"]["rule_version"] == "precheck-1"
    assert {r["check_code"] for r in view["precheck"]["results"]} >= {"PHOTO_TIME", "CORROBORATION"}


def test_heic_original_time_is_read_before_re_encoding():
    from pillow_heif import register_heif_opener

    from app.services.photos import process_photo

    register_heif_opener()
    image = Image.new("RGB", (40, 30), (10, 20, 30))
    exif = Image.Exif()
    exif[0x8825] = {2: (25.0, 2.0, 1.0)}
    ifd = exif.get_ifd(0x8769)
    ifd[36867], ifd[36881], ifd[37521] = "2026:10:10 11:30:00", "+08:00", "250"
    buffer = io.BytesIO()
    image.save(buffer, format="HEIF", exif=exif.tobytes())
    processed = process_photo(buffer.getvalue(), 15_000_000)
    assert processed.exif_time.datetime_original == "2026:10:10 11:30:00"
    assert processed.exif_time.offset_time_original == "+08:00"
    assert processed.exif_time.subsec_time_original == "250"
    with Image.open(io.BytesIO(processed.body)) as stored:
        assert stored.format == "JPEG" and not stored.getexif()


async def test_invalid_decisions_conflict(env):
    case = await published_case(env)
    await decide(env, case["id"], "ACCEPT", expect=409)
    await decide(env, case["id"], "REQUEST_EVIDENCE", expect=409)
    await decide(env, case["id"], "REJECT")
    await decide(env, case["id"], "ACCEPT", expect=409)


async def test_superseding_a_published_observation_withdraws_it(env):
    case = await published_case(env)
    successor = await create(env)
    result = await decide(env, case["id"], "SUPERSEDE", superseded_by_case_id=successor["id"])
    assert result["review_status"] == "SUPERSEDED" and result["publication_status"] == "WITHDRAWN"
    assert await observations(env) == []
    # Superseded is not invalid: earned points stay.
    assert (await points(env, "alice"))["points"] == 1


async def test_unmoderated_text_is_private_to_the_author(env):
    case = await create(env, description="作者的私人說明")
    await stance(env, case["id"], "bob", "CANNOT_CONFIRM")
    assert (await detail(env, case["id"], "alice"))["revisions"][0]["description"] == "作者的私人說明"
    assert (await detail(env, case["id"], "bob"))["revisions"][0]["description"] is None
    outsider = await env["client"].get(f"/api/v1/me/community/cases/{case['id']}", headers=auth(env, "carol"))
    assert outsider.status_code == 403
    assert (await moderation(env, case["id"]))["revisions"][0]["description"] == "作者的私人說明"


# --- review fixes (PR #43) ---------------------------------------------------------------------


async def test_support_must_report_the_proposed_value(env):
    case = await create(env)
    response = await env["client"].put(
        f"/api/v1/community/cases/{case['id']}/stance",
        json={"stance": "SUPPORT", "observed_value": {"present": False}},
        headers=auth(env, "bob", key()),
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "SUPPORT_VALUE_MISMATCH"


def _jpeg_with_raw_exif(subsec):
    image = Image.new("RGB", (40, 30), (next(_colors) % 256, 1, 2))
    exif = Image.Exif()
    ifd = exif.get_ifd(0x8769)
    ifd[36867] = (NOW - timedelta(hours=1)).astimezone(TAIPEI).strftime("%Y:%m:%d %H:%M:%S")
    ifd[36881] = "+08:00"
    ifd[37521] = subsec
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


@pytest.mark.parametrize("subsec", ["1" * 40, "12a"])
async def test_overlong_or_malformed_subseconds_are_invalid_not_ignored(env, subsec):
    case = await create(env)
    photo = await upload(env, case["id"], "alice", "revision", _jpeg_with_raw_exif(subsec))
    assert photo["metadata_time"]["status"] == "INVALID" and photo["counts_toward_corroboration"] is False


async def test_idempotent_replay_bypasses_the_rate_limit(env):
    payload = {"parking_id": env["lots"][0].id, "fact_type": "LIGHTING", "proposed_value": LIGHT}
    first = await env["client"].post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", "rl"))
    env["app"].state.community_rate_limiter.allowed = False
    replay = await env["client"].post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", "rl"))
    assert replay.status_code == 201 and replay.json() == first.json()
    fresh = await env["client"].post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", "rl2"))
    assert fresh.status_code == 429


async def test_stance_withdrawal_is_idempotent(env):
    case = await published_case(env)
    path = f"/api/v1/community/cases/{case['id']}/stance"
    missing = await env["client"].delete(path, headers=auth(env, "bob"))
    assert missing.status_code == 428
    first = await env["client"].delete(path, headers=auth(env, "bob", "wd"))
    again = await env["client"].delete(path, headers=auth(env, "bob", "wd"))
    assert first.status_code == again.status_code == 204
    other = await env["client"].delete(path, headers=auth(env, "bob", "wd2"))
    assert other.status_code == 404


async def test_source_verification_rejects_unknown_references(env):
    base = {"fact_kind": "FACILITY", "evidence_url": "https://operator.example/lot"}
    for body, code in (
        ({**base, "source_id": 999999}, "SOURCE_NOT_FOUND"),
        ({**base, "source_id": env["source"].id, "parking_id": 999999}, "PARKING_NOT_FOUND"),
    ):
        response = await env["client"].post(
            "/api/v1/moderation/source-verifications", json=body, headers=auth(env, "mod")
        )
        assert response.status_code == 404 and response.json()["error"]["code"] == code


async def test_participating_moderator_cannot_delete_case_evidence(env):
    case = await create(env)
    photo = await upload(env, case["id"], "alice", "revision")
    await stance(env, case["id"], "mod2", "CANNOT_CONFIRM")
    response = await env["client"].delete(f"/api/v1/moderation/photos/{photo['photo_id']}", headers=auth(env, "mod2"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "CONFLICT_OF_INTEREST"


async def test_accepting_an_unpublished_case_overrules_its_objection(env):
    case = await create(env)
    await upload(env, case["id"], "alice", "revision")
    await support(env, case["id"], "bob", value="OPPOSE", observed={"present": False})
    assert (await detail(env, case["id"]))["case"]["review_status"] == "MANUAL_REVIEW"
    accepted = await decide(env, case["id"], "ACCEPT")
    assert accepted["publication_status"] == "PUBLISHED"
    # The next precheck (any later write) must not suspend it again for the overruled objection.
    await stance(env, case["id"], "carol", "CANNOT_CONFIRM")
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "PUBLISHED"


async def test_photo_eligibility_in_views_follows_publication(env):
    case = await create(env)
    await upload(env, case["id"], "alice", "revision", jpeg(taken=NOW - timedelta(days=25)))
    for name in ("bob", "carol", "dave"):
        await support(env, case["id"], name, jpeg(taken=NOW - timedelta(days=25)))
    env["clock"][0] = NOW + timedelta(days=10)
    photo = (await detail(env, case["id"]))["my_photos"][0]
    assert photo["counts_toward_corroboration"] is True and photo["message_code"] == "ELIGIBLE"


async def test_failed_object_deletion_stays_pending_until_retried(env):
    from app.services.storage_outbox import drain

    case = await create(env)
    photo = await upload(env, case["id"], "alice", "revision")
    storage = env["app"].state.object_storage
    stored_key = next(iter(storage.objects))

    async def failing_delete(key):
        raise RuntimeError("transient")

    storage.delete, original = failing_delete, storage.delete
    response = await env["client"].delete(f"/api/v1/moderation/photos/{photo['photo_id']}", headers=auth(env, "mod"))
    assert response.status_code == 204
    entry = (await env["session"].scalars(select(StorageDeletion))).one()
    assert entry.storage_key == stored_key and entry.completed_at is None and entry.attempts == 1
    assert entry.last_error == "RuntimeError" and stored_key in storage.objects
    storage.delete = original
    assert await drain(env["session"], storage, NOW + timedelta(minutes=5)) == (1, 0)
    await env["session"].refresh(entry)
    assert entry.completed_at is not None and stored_key not in storage.objects


async def test_suspending_an_author_suspends_their_live_observation_only(env):
    case = await published_case(env)
    closed = await create(env)
    await decide(env, closed["id"], "REJECT")
    closed_version = (await moderation(env, closed["id"]))["case"]["version"]
    author = (await moderation(env, case["id"]))["author_participant_id"]
    response = await env["client"].put(
        f"/api/v1/moderation/participants/{author}/suspension", json={"reason_code": "ABUSE"}, headers=auth(env, "mod")
    )
    assert response.status_code == 200
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "SUSPENDED"
    # Closed cases are not recounted: no new precheck, event or version bump.
    assert (await moderation(env, closed["id"]))["case"]["version"] == closed_version


async def test_failed_request_with_a_completed_key_replays_the_stored_result(env):
    payload = {"parking_id": env["lots"][0].id, "fact_type": "LIGHTING", "proposed_value": LIGHT}
    first = await env["client"].post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", "same-key"))
    assert first.status_code == 201
    # Pinned behaviour: once a key has completed, an identical retry replays it even if it would
    # now fail (here: the author was suspended in between), because the digest matches.
    author = (await moderation(env, first.json()["id"]))["author_participant_id"]
    await env["client"].put(
        f"/api/v1/moderation/participants/{author}/suspension", json={"reason_code": "ABUSE"}, headers=auth(env, "mod")
    )
    retry = await env["client"].post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", "same-key"))
    assert retry.status_code == 201 and retry.json() == first.json()
    fresh = await env["client"].post("/api/v1/community/cases", json=payload, headers=auth(env, "alice", "new-key"))
    assert fresh.status_code == 403 and fresh.json()["error"]["code"] == "PARTICIPANT_SUSPENDED"


# --- third review round (PR #43) ---------------------------------------------------------------


async def test_manual_resume_below_threshold_is_relabelled_as_manual_review(env):
    case = await published_case(env)
    await env["client"].delete(f"/api/v1/community/cases/{case['id']}/stance", headers=auth(env, "bob", key()))
    resumed = await decide(env, case["id"], "ACCEPT")
    assert resumed["publication_status"] == "PUBLISHED" and resumed["publication_basis"] == "MANUAL_REVIEW"
    (item,) = await observations(env)
    assert item["publication_basis"] == "MANUAL_REVIEW" and item["corroborator_count"] is None


async def test_upload_whose_commit_fails_leaves_no_orphaned_object(env, monkeypatch):
    case = await create(env)
    storage = env["app"].state.object_storage
    session = env["session"]
    real_commit = session.commit

    async def failing_commit():
        raise RuntimeError("commit lost")

    monkeypatch.setattr(session, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        await env["client"].post(
            f"/api/v1/community/cases/{case['id']}/photos",
            files={"file": ("p.jpg", jpeg(), "image/jpeg")},
            data={"owner": "revision"},
            headers=auth(env, "alice"),
        )
    monkeypatch.setattr(session, "commit", real_commit)
    assert storage.objects == {}


async def test_inactive_stance_photos_still_reserve_the_image(env):
    case = await create(env)
    await upload(env, case["id"], "alice", "revision")
    image = jpeg()
    await support(env, case["id"], "bob", image)
    await env["client"].delete(f"/api/v1/community/cases/{case['id']}/stance", headers=auth(env, "bob", key()))
    again = await support(env, case["id"], "carol", image)
    assert again["counts_toward_corroboration"] is False and again["message_code"] == "DUPLICATE_EVIDENCE"
    withdrawn = (await detail(env, case["id"], "bob"))["my_photos"][0]
    assert withdrawn["counts_toward_corroboration"] is False and withdrawn["message_code"] == "NOT_COUNTED"


async def test_conflicting_live_observation_sends_new_case_to_manual_review(env):
    await published_case(env)
    other = await create(env, proposed_value={"present": False})
    await upload(env, other["id"], "alice", "revision")
    for name in ("bob", "carol", "dave"):
        await support(env, other["id"], name, observed={"present": False})
    summary = (await detail(env, other["id"]))["case"]
    assert summary["publication_status"] == "UNPUBLISHED" and summary["review_status"] == "MANUAL_REVIEW"
    reasons = {r["check_code"]: r["reason_code"] for r in (await moderation(env, other["id"]))["precheck"]["results"]}
    assert reasons["SOURCE_CONFLICT"] == "CONFLICTS_WITH_PUBLISHED_OBSERVATION"


async def test_superseding_a_suspended_observation_restores_frozen_points(env):
    case = await published_case(env)
    await support(env, case["id"], "erin", value="OPPOSE", observed={"present": False})
    assert (await points(env, "alice"))["points"] == 0  # frozen while suspended
    successor = await create(env)
    await decide(env, case["id"], "SUPERSEDE", superseded_by_case_id=successor["id"])
    assert (await points(env, "alice"))["points"] == 1


async def test_supporter_whose_later_objection_is_upheld_keeps_one_point(env):
    case = await published_case(env)
    await support(env, case["id"], "erin")
    assert (await points(env, "erin"))["points"] == 1
    await support(env, case["id"], "erin", value="OPPOSE", observed={"present": False})
    await decide(env, case["id"], "REJECT")
    assert (await points(env, "erin"))["points"] == 1
    assert (await points(env, "bob"))["points"] == 0


async def test_suspension_is_refused_when_it_would_recount_the_moderators_case(env):
    case = await published_case(env)
    await stance(env, case["id"], "mod2", "CANNOT_CONFIRM")
    bob = (await moderation(env, case["id"]))["stances"][0]["participant_id"]
    response = await env["client"].put(
        f"/api/v1/moderation/participants/{bob}/suspension", json={"reason_code": "ABUSE"}, headers=auth(env, "mod2")
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "CONFLICT_OF_INTEREST"
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "PUBLISHED"


@pytest.mark.parametrize("failure", ["put", "precheck"])
async def test_failed_upload_and_failed_cleanup_leave_a_durable_deletion(env, monkeypatch, failure):
    from app.services.community_cases import CommunityCaseService
    from app.services.storage_outbox import drain

    case = await create(env)
    storage = env["app"].state.object_storage
    original_put, original_delete = storage.put, storage.delete

    async def failing_put(*args):
        await original_put(*args)
        raise RuntimeError("upload response lost")

    async def failing_precheck(*args):
        raise RuntimeError("precheck failed")

    async def failing_delete(key):
        raise RuntimeError("cleanup unavailable")

    monkeypatch.setattr(storage, "delete", failing_delete)
    if failure == "put":
        monkeypatch.setattr(storage, "put", failing_put)
    else:
        monkeypatch.setattr(CommunityCaseService, "_precheck", failing_precheck)
    with pytest.raises(RuntimeError):
        await env["client"].post(
            f"/api/v1/community/cases/{case['id']}/photos",
            files={"file": ("p.jpg", jpeg(), "image/jpeg")},
            data={"owner": "revision"},
            headers=auth(env, "alice"),
        )
    (stored_key,) = storage.objects
    entry = (await env["session"].scalars(select(StorageDeletion))).one()
    assert entry.storage_key == stored_key and entry.completed_at is None
    assert (await env["session"].scalars(select(CommunityEvidencePhoto))).all() == []
    monkeypatch.setattr(storage, "delete", original_delete)
    assert await drain(env["session"], storage, NOW) == (1, 0)
    assert storage.objects == {}


@pytest.mark.parametrize("kind", ["revised_support", "same_value_objection"])
async def test_noncounting_stance_evidence_is_false_in_participant_and_moderator_views(env, kind):
    case = await create(env)
    if kind == "revised_support":
        await support(env, case["id"], "bob")
        await decide(env, case["id"], "REQUEST_EVIDENCE")
        response = await env["client"].post(
            f"/api/v1/community/cases/{case['id']}/revisions",
            json={"proposed_value": {"present": False}},
            headers=auth(env, "alice", key()),
        )
        assert response.status_code == 201
    else:
        await support(env, case["id"], "bob", value="OPPOSE", observed=LIGHT)
    own = (await detail(env, case["id"], "bob"))["my_photos"][0]
    assert own["counts_toward_corroboration"] is False and own["message_code"] == "NOT_COUNTED"
    photos = (await moderation(env, case["id"]))["photos"]
    assert len(photos) == 1 and photos[0]["counts_toward_corroboration"] is False


async def test_accept_overrules_only_valid_objections_and_preserves_pending_evidence(env):
    case = await create(env)
    await support(env, case["id"], "bob", value="OPPOSE", observed={"present": False})
    await support(env, case["id"], "carol", value="OPPOSE", observed=LIGHT)
    await stance(env, case["id"], "dave", "OPPOSE", {"present": False})
    await decide(env, case["id"], "ACCEPT")
    assert (await detail(env, case["id"], "bob"))["my_stance"] is None
    assert (await detail(env, case["id"], "carol"))["my_stance"] is not None
    assert (await detail(env, case["id"], "dave"))["my_stance"] is not None
    await upload(env, case["id"], "dave", "stance")
    assert (await detail(env, case["id"]))["case"]["publication_status"] == "SUSPENDED"


async def test_overruled_stance_timeline_is_attributed_to_the_moderator(env):
    case = await create(env)
    await support(env, case["id"], "bob", value="OPPOSE", observed={"present": False})
    await decide(env, case["id"], "ACCEPT")
    events = (await detail(env, case["id"]))["timeline"]
    (invalidated,) = [event for event in events if event["event_type"] == "STANCE_INVALIDATED"]
    assert invalidated["actor"] == "MODERATOR"


async def test_manual_resume_without_original_evidence_uses_manual_basis(env):
    case = await published_case(env)
    photo = (await detail(env, case["id"]))["my_photos"][0]
    response = await env["client"].delete(f"/api/v1/moderation/photos/{photo['photo_id']}", headers=auth(env, "mod"))
    assert response.status_code == 204
    before = (await detail(env, case["id"]))["case"]
    assert before["publication_status"] == "SUSPENDED" and before["supporters"] == 3
    resumed = await decide(env, case["id"], "ACCEPT")
    assert resumed["publication_basis"] == "MANUAL_REVIEW"
    assert resumed["published_until"] == before["published_until"]
    (observation,) = await observations(env)
    assert observation["corroborator_count"] is None


@pytest.mark.parametrize("auto_publish_enabled", [True, False])
async def test_resume_preserves_valid_corroboration_after_overruling_and_natural_aging(env, auto_publish_enabled):
    case = await published_case(env)
    before = (await detail(env, case["id"]))["case"]["published_until"]
    await support(env, case["id"], "erin", value="OPPOSE", observed={"present": False})
    env["clock"][0] += timedelta(days=31)
    env["app"].state.community_auto_publish_enabled = auto_publish_enabled
    resumed = await decide(env, case["id"], "ACCEPT")
    assert resumed["publication_basis"] == "COMMUNITY_CORROBORATED"
    assert resumed["published_until"] == before
    (observation,) = await observations(env)
    assert observation["corroborator_count"] == 3
    for name in ("alice", "bob", "carol", "dave"):
        assert (await points(env, name))["points"] == 1


@pytest.mark.parametrize("length", [129, 200, 201, 4096])
@pytest.mark.parametrize("operation", ["create", "revise", "stance", "withdraw", "decide"])
async def test_all_overlong_idempotency_keys_use_the_documented_error(env, operation, length):
    case = await create(env)
    if operation == "create":
        method, path, body, name = (
            "POST",
            "/community/cases",
            {"parking_id": env["lots"][0].id, "fact_type": "LIGHTING", "proposed_value": LIGHT},
            "alice",
        )
    elif operation == "revise":
        method, path, body, name = (
            "POST",
            f"/community/cases/{case['id']}/revisions",
            {"proposed_value": LIGHT},
            "alice",
        )
    elif operation == "stance":
        method, path, body, name = (
            "PUT",
            f"/community/cases/{case['id']}/stance",
            {"stance": "SUPPORT", "observed_value": LIGHT},
            "bob",
        )
    elif operation == "withdraw":
        method, path, body, name = "DELETE", f"/community/cases/{case['id']}/stance", None, "bob"
    else:
        method, path, body, name = (
            "POST",
            f"/moderation/cases/{case['id']}/decisions",
            {"decision": "REJECT", "reason_code": "TEST", "reason_text": "Reason", "expected_version": case["version"]},
            "mod",
        )
    response = await env["client"].request(method, f"/api/v1{path}", json=body, headers=auth(env, name, "k" * length))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_INVALID"


async def test_rejecting_an_unpublished_case_awards_only_valid_upheld_objectors(env):
    case = await create(env)
    await support(env, case["id"], "bob", value="OPPOSE", observed={"present": False})
    await stance(env, case["id"], "carol", "OPPOSE", {"present": False})
    await support(env, case["id"], "dave", value="OPPOSE", observed=LIGHT)
    rejected = await decide(env, case["id"], "REJECT")
    assert rejected["publication_status"] == "UNPUBLISHED" and rejected["review_status"] == "REJECTED"
    assert (await points(env, "bob"))["points"] == 1
    for name in ("alice", "carol", "dave"):
        assert (await points(env, name))["points"] == 0


async def test_concurrent_conflicting_cases_publish_only_one_observation(migrated, monkeypatch):
    """Independent committed transactions must serialize their conflict snapshots."""
    from app.repositories.community_cases import CommunityCaseRepository

    engine = create_async_engine(settings.database_url)
    prefix = f"concurrency:{uuid.uuid4()}"
    user_ids = []
    lot_id = None
    tasks = []
    first_read, second_read, release_first = asyncio.Event(), asyncio.Event(), asyncio.Event()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            lot = ParkingLot(name=prefix, city="臺北市", location="SRID=4326;POINT(121.56 25.03)")
            session.add(lot)
            await session.flush()
            lot_id = lot.id
            tokens = AccessTokenService(session)
            users = {}
            for name in ("alice", "frank", "bob", "carol", "dave"):
                user = await tokens.ensure_user(f"{prefix}:{name}", display_name=name)
                token, _ = await tokens.issue(user, NOW - timedelta(minutes=1), timedelta(days=400))
                users[name] = (user, token)
                user_ids.append(user.id)
            await session.commit()

        app = create_app()
        app.state.clock = lambda: NOW
        app.state.object_storage = MemoryStorage()
        app.state.community_rate_limiter = AllowAll()
        app.state.community_auto_publish_enabled = False

        async def independent_session():
            async with AsyncSession(engine, expire_on_commit=False) as session:
                yield session

        app.dependency_overrides[get_session] = independent_session
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            concurrent_env = {"client": client, "lots": [lot], "users": users}
            first = await create(concurrent_env)
            second = await create(concurrent_env, "frank", proposed_value={"present": False})
            for case, author, value in ((first, "alice", LIGHT), (second, "frank", {"present": False})):
                await upload(concurrent_env, case["id"], author, "revision")
                for name in ("bob", "carol", "dave"):
                    await support(concurrent_env, case["id"], name, observed=value)
            app.state.community_auto_publish_enabled = True

            original = CommunityCaseRepository.published_for_parking

            async def hold_first_snapshot(repository, parking_id, now):
                rows = await original(repository, parking_id, now)
                if not first_read.is_set():
                    first_read.set()
                    await release_first.wait()
                else:
                    second_read.set()
                return rows

            monkeypatch.setattr(CommunityCaseRepository, "published_for_parking", hold_first_snapshot)
            tasks.append(asyncio.create_task(upload(concurrent_env, first["id"], "alice", "revision")))
            await asyncio.wait_for(first_read.wait(), timeout=10)
            tasks.append(asyncio.create_task(upload(concurrent_env, second["id"], "frank", "revision")))
            # The second transaction should be waiting for the shared scope lock.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(second_read.wait(), timeout=1)
            release_first.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
            first_state = (await detail(concurrent_env, first["id"]))["case"]
            second_state = (await detail(concurrent_env, second["id"], "frank"))["case"]
            assert first_state["publication_status"] == "PUBLISHED"
            assert second_state["publication_status"] == "UNPUBLISHED"
            assert second_state["review_status"] == "MANUAL_REVIEW"
            assert len(await observations(concurrent_env)) == 1
    finally:
        release_first.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with AsyncSession(engine) as session:
            if lot_id is not None:
                await session.execute(delete(CommunityCase).where(CommunityCase.parking_id == lot_id))
                await session.execute(delete(ParkingLot).where(ParkingLot.id == lot_id))
            await session.execute(delete(CommunityParticipant).where(CommunityParticipant.user_id.in_(user_ids)))
            await session.execute(delete(User).where(User.id.in_(user_ids)))
            await session.commit()
        await engine.dispose()
