"""Migration 006 constraints, exercised with real rejected writes."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import settings

ALEMBIC = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
NOW = datetime(2026, 10, 10, 4, 0, tzinfo=UTC)
SHA = "a" * 64
CHECK, UNIQUE, FK = "23514", "23505", "23503"


@pytest.fixture(scope="module")
def engine():
    command.upgrade(ALEMBIC, "head")
    engine = create_engine(settings.database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def t(engine):
    metadata = MetaData()
    metadata.reflect(engine)
    return metadata.tables


@pytest.fixture
def db(engine):
    with engine.connect() as connection, connection.begin() as transaction:
        yield connection
        transaction.rollback()


def ins(db, table, **values):
    return db.execute(table.insert().values(**values).returning(table.c.id)).scalar_one()


def rejected(db, table, sqlstate, **values):
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        ins(db, table, **values)
    assert error.value.orig.sqlstate == sqlstate, error.value
    return error.value.orig.diag.constraint_name


@pytest.fixture
def g(db, t):
    """Minimal graph: two lots, a zone on each, three participants and one case of each kind."""
    source = ins(db, t["data_sources"], code="op", name="Operator", source_type="OPERATOR")
    lots = [ins(db, t["parking_lots"], name=f"L{i}", location="SRID=4326;POINT(121.5 25.0)") for i in range(2)]
    zones = [ins(db, t["parking_zones"], parking_id=lot, space_type="MOTO_SHARED") for lot in lots]
    users = [ins(db, t["users"], auth_subject=f"s{i}") for i in range(3)]
    people = [ins(db, t["community_participants"], user_id=user) for user in users]
    low = ins(db, t["community_cases"], parking_id=lots[0], fact_type="LIGHTING", author_participant_id=people[0])
    rule = ins(
        db,
        t["community_cases"],
        parking_id=lots[0],
        zone_id=zones[0],
        fact_type="PARKING_PERMISSION",
        vehicle="LARGE_HEAVY",
        author_participant_id=people[0],
    )
    other = ins(db, t["community_cases"], parking_id=lots[1], fact_type="CHARGING", author_participant_id=people[1])
    revision = ins(
        db,
        t["community_case_revisions"],
        case_id=low,
        revision=1,
        proposed_value={"present": True},
        created_by_participant_id=people[0],
    )
    other_revision = ins(
        db,
        t["community_case_revisions"],
        case_id=other,
        revision=1,
        proposed_value={"present": False},
        created_by_participant_id=people[1],
    )
    return {
        "source": source,
        "lots": lots,
        "zones": zones,
        "users": users,
        "people": people,
        "low": low,
        "rule": rule,
        "other": other,
        "revision": revision,
        "other_revision": other_revision,
    }


def photo(g, **overrides):
    values = {
        "case_id": g["low"],
        "revision_id": g["revision"],
        "participant_id": g["people"][0],
        "storage_key": f"community/{overrides.pop('key', 'p')}.jpg",
        "normalized_sha256": SHA,
        "received_at": NOW,
        "exif_datetime_original": "2026:10:10 11:00:00",
        "exif_offset_time_original": "+08:00",
        "captured_at": NOW - timedelta(hours=1),
        "time_status": "VALID",
        "time_parser_version": "exif-time-1",
    }
    return {**values, **overrides}


@pytest.mark.parametrize(
    "fields",
    [
        {"fact_type": "PARKING_PERMISSION", "vehicle": None},
        {"fact_type": "RATE", "vehicle": "NORMAL_HEAVY", "zone_id": None},
        {"fact_type": "ENTRANCE_ACCESS", "vehicle": None},
        {"fact_type": "LIGHTING", "vehicle": "LARGE_HEAVY"},
        {"fact_type": "PARKING_PERMISSION", "vehicle": "CAR"},
    ],
)
def test_case_scope_requires_vehicle_and_zone_only_for_source_resolved_facts(db, t, g, fields):
    base = {"parking_id": g["lots"][0], "zone_id": g["zones"][0], "author_participant_id": g["people"][0]}
    assert rejected(db, t["community_cases"], CHECK, **{**base, **fields}).startswith("ck_community_cases_")


def test_case_zone_must_belong_to_the_lot(db, t, g):
    rejected(
        db,
        t["community_cases"],
        FK,
        parking_id=g["lots"][0],
        zone_id=g["zones"][1],
        fact_type="RATE",
        vehicle="NORMAL_HEAVY",
        author_participant_id=g["people"][0],
    )


def test_case_defaults_are_unpublished_precheck_pending(db, t, g):
    row = db.execute(select(t["community_cases"]).where(t["community_cases"].c.id == g["low"])).one()
    assert (row.review_status, row.publication_state, row.version, row.current_revision) == (
        "PRECHECK_PENDING",
        "UNPUBLISHED",
        1,
        1,
    )


@pytest.mark.parametrize(
    "fields, constraint",
    [
        ({"publication_state": "PUBLISHED"}, "ck_community_cases_publication_recorded"),
        (
            {
                "publication_basis": "MANUAL_REVIEW",
                "first_published_at": NOW,
                "published_until": NOW + timedelta(days=90),
            },
            "ck_community_cases_publication_recorded",
        ),
        (
            {
                "publication_state": "PUBLISHED",
                "publication_basis": "COMMUNITY_CORROBORATED",
                "first_published_at": NOW,
                "published_until": NOW + timedelta(days=91),
            },
            "ck_community_cases_observation_term_90_days",
        ),
        (
            {
                "publication_state": "SUSPENDED",
                "publication_basis": "MANUAL_REVIEW",
                "first_published_at": NOW,
                "published_until": None,
            },
            "ck_community_cases_observation_term_90_days",
        ),
        (
            {
                "publication_state": "PUBLISHED",
                "publication_basis": "COMMUNITY_CORROBORATED",
                "first_published_at": NOW,
                "published_until": NOW + timedelta(days=90),
            },
            "ck_community_cases_published_requires_accepted",
        ),
        (
            {
                "publication_state": "WITHDRAWN",
                "review_status": "REJECTED",
                "publication_basis": "MANUAL_REVIEW",
                "first_published_at": NOW,
                "published_until": NOW + timedelta(days=90),
            },
            "ck_community_cases_withdrawn_recorded",
        ),
        ({"review_status": "SUPERSEDED"}, "ck_community_cases_superseded_has_successor"),
        ({"version": 0}, "ck_community_cases_versions_positive"),
    ],
)
def test_publication_lifecycle_constraints(db, t, g, fields, constraint):
    base = {"parking_id": g["lots"][0], "fact_type": "LIGHTING", "author_participant_id": g["people"][0]}
    assert rejected(db, t["community_cases"], CHECK, **{**base, **fields}) == constraint


def test_community_corroboration_cannot_publish_source_resolved_facts(db, t, g):
    published = {
        "review_status": "ACCEPTED",
        "publication_state": "PUBLISHED",
        "first_published_at": NOW,
        "published_until": NOW + timedelta(days=90),
    }
    base = {
        "parking_id": g["lots"][0],
        "zone_id": g["zones"][0],
        "fact_type": "PARKING_PERMISSION",
        "vehicle": "LARGE_HEAVY",
        "author_participant_id": g["people"][0],
    }
    assert (
        rejected(db, t["community_cases"], CHECK, **base, **published, publication_basis="COMMUNITY_CORROBORATED")
        == "ck_community_cases_corroboration_low_risk_only"
    )
    ins(db, t["community_cases"], **base, **published, publication_basis="MANUAL_REVIEW")
    # Source-confirmed publications carry no observation term.
    ins(
        db,
        t["community_cases"],
        **base,
        **{**published, "published_until": None},
        publication_basis="VERIFIED_SOURCE",
    )


def test_published_observation_and_supersession_roundtrip(db, t, g):
    cases = t["community_cases"]
    db.execute(
        update(cases)
        .where(cases.c.id == g["low"])
        .values(
            review_status="ACCEPTED",
            publication_state="SUSPENDED",
            publication_basis="COMMUNITY_CORROBORATED",
            first_published_at=NOW,
            published_until=NOW + timedelta(days=90),
        )
    )
    db.execute(
        update(cases).where(cases.c.id == g["other"]).values(review_status="SUPERSEDED", superseded_by_case_id=g["low"])
    )
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        db.execute(update(cases).where(cases.c.id == g["low"]).values(superseded_by_case_id=g["low"]))
    assert error.value.orig.diag.constraint_name in {
        "ck_community_cases_not_self_superseded",
        "ck_community_cases_superseded_has_successor",
    }


def test_photo_time_status_must_match_stored_fields(db, t, g):
    table = t["community_evidence_photos"]
    ins(db, table, **photo(g, key="valid"))
    ins(
        db,
        table,
        **photo(g, key="boundary", captured_at=NOW - timedelta(days=30), exif_datetime_original="2026:09:10 12:00:00"),
    )
    ins(
        db,
        table,
        **photo(
            g,
            key="missing",
            time_status="MISSING",
            exif_datetime_original=None,
            exif_offset_time_original=None,
            captured_at=None,
        ),
    )
    ins(
        db,
        table,
        **photo(g, key="tz", time_status="TIMEZONE_UNKNOWN", exif_offset_time_original=None, captured_at=None),
    )
    ins(db, table, **photo(g, key="invalid", time_status="INVALID", exif_datetime_original="bad", captured_at=None))
    ins(db, table, **photo(g, key="future", time_status="FUTURE", captured_at=NOW + timedelta(minutes=1)))
    ins(db, table, **photo(g, key="old", time_status="OUTSIDE_WINDOW", captured_at=NOW - timedelta(days=31)))
    for bad in (
        photo(g, key="b1", captured_at=NOW - timedelta(days=30, seconds=1)),
        photo(g, key="b2", captured_at=NOW + timedelta(seconds=1)),
        photo(g, key="b3", exif_offset_time_original=None),
        photo(g, key="b9", captured_at=None),
        photo(g, key="b10", time_status="FUTURE", captured_at=None),
        photo(g, key="b11", time_status="OUTSIDE_WINDOW", captured_at=None),
        photo(g, key="b4", time_status="MISSING", exif_datetime_original=None, exif_offset_time_original=None),
        photo(g, key="b5", time_status="TIMEZONE_UNKNOWN", captured_at=None),
        photo(g, key="b6", time_status="INVALID"),
        photo(g, key="b7", time_status="FUTURE"),
        photo(g, key="b8", time_status="OUTSIDE_WINDOW"),
    ):
        assert rejected(db, table, CHECK, **bad) == "ck_community_evidence_photos_time_status_consistent", bad


def test_photo_deletion_clears_private_content_but_keeps_audit_row(db, t, g):
    table = t["community_evidence_photos"]
    photo_id = ins(db, table, **photo(g))
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(update(table).where(table.c.id == photo_id).values(deleted_at=NOW))
    db.execute(
        update(table)
        .where(table.c.id == photo_id)
        .values(
            deleted_at=NOW,
            storage_key=None,
            exif_datetime_original=None,
            exif_offset_time_original=None,
            captured_at=None,
        )
    )
    row = db.execute(select(table).where(table.c.id == photo_id)).one()
    assert (row.time_status, row.normalized_sha256, row.storage_key) == ("VALID", SHA, None)
    assert (
        rejected(db, table, CHECK, **photo(g, key="x", storage_key=None))
        == "ck_community_evidence_photos_live_has_object"
    )


def test_photo_needs_exactly_one_owner_on_the_same_case(db, t, g):
    table = t["community_evidence_photos"]
    stance = ins(
        db,
        t["community_case_stances"],
        case_id=g["low"],
        participant_id=g["people"][1],
        stance="SUPPORT",
        observed_value={"present": True},
    )
    assert (
        rejected(db, table, CHECK, **photo(g, key="both", stance_id=stance)) == "ck_community_evidence_photos_one_owner"
    )
    assert (
        rejected(db, table, CHECK, **photo(g, key="none", revision_id=None)) == "ck_community_evidence_photos_one_owner"
    )
    rejected(db, table, FK, **photo(g, key="cross", revision_id=g["other_revision"]))
    ins(db, table, **photo(g, key="stance", revision_id=None, stance_id=stance, participant_id=g["people"][1]))
    assert rejected(db, table, CHECK, **photo(g, key="sha", normalized_sha256="A" * 64)).endswith("sha256_hex")
    # Same normalized image in the same case is stored but is not independent evidence.
    ins(db, table, **photo(g, key="dup"))


def test_one_active_stance_per_participant_and_stance_history(db, t, g):
    table = t["community_case_stances"]
    first = {"case_id": g["low"], "participant_id": g["people"][1]}
    stance = ins(db, table, **first, stance="SUPPORT", observed_value={"present": True})
    assert rejected(db, table, UNIQUE, **first, stance="OPPOSE", observed_value={"present": False}) == (
        "uq_community_case_stances_active"
    )
    db.execute(update(table).where(table.c.id == stance).values(withdrawn_at=text("now()")))
    ins(db, table, **first, stance="OPPOSE", observed_value={"present": False})
    assert rejected(db, table, CHECK, case_id=g["low"], participant_id=g["people"][2], stance="SUPPORT") == (
        "ck_community_case_stances_observation_required"
    )
    ins(db, table, case_id=g["low"], participant_id=g["people"][2], stance="CANNOT_CONFIRM")
    assert (
        rejected(db, table, CHECK, **first, stance="CANNOT_CONFIRM", invalidated_at=NOW)
        == "ck_community_case_stances_invalidation_has_reason"
    )


def test_revisions_are_unique_per_case_and_validate_content(db, t, g):
    table = t["community_case_revisions"]
    base = {"case_id": g["low"], "created_by_participant_id": g["people"][0], "proposed_value": {"present": True}}
    assert rejected(db, table, UNIQUE, **base, revision=1) == "uq_community_case_revisions_case_id_revision"
    ins(db, table, **base, revision=2)
    for fields, name in (
        ({"proposed_value": ["x"]}, "ck_community_case_revisions_proposed_value_object"),
        ({"description": "x" * 1001}, "ck_community_case_revisions_description_length"),
        ({"description_approved_at": NOW}, "ck_community_case_revisions_approval_requires_text"),
        ({"source_url": "http://example.com"}, "ck_community_case_revisions_source_url_https"),
        ({"revision": 0}, "ck_community_case_revisions_revision_positive"),
    ):
        assert rejected(db, table, CHECK, **{**base, "revision": 9, **fields}) == name


@pytest.mark.parametrize(
    "fields, constraint",
    [
        ({"fact_kind": "PERMISSION"}, "ck_source_verifications_rule_tier_only_for_permission"),
        ({"rule_kind": "BASELINE", "authority_priority": 10}, "ck_source_verifications_rule_tier_only_for_permission"),
        ({"parser_code": "operator-site"}, "ck_source_verifications_parser_versioned"),
        ({"evidence_url": "http://operator.example"}, "ck_source_verifications_evidence_url_https"),
        ({"parking_id": None}, "ck_source_verifications_zone_requires_lot"),
        ({"revoked_at": NOW - timedelta(days=1)}, "ck_source_verifications_revoked_after_verified"),
    ],
)
def test_source_verification_constraints(db, t, g, fields, constraint):
    base = {
        "source_id": g["source"],
        "fact_kind": "FACILITY",
        "parking_id": g["lots"][0],
        "zone_id": g["zones"][0],
        "evidence_url": "https://operator.example/lot",
        "verified_by_participant_id": g["people"][2],
        "verified_at": NOW,
    }
    ins(db, t["source_verifications"], **base)
    ins(
        db,
        t["source_verifications"],
        **{**base, "fact_kind": "PERMISSION", "rule_kind": "EXCEPTION", "authority_priority": 20},
        parser_code="operator-site",
        parser_config_version="1",
    )
    assert rejected(db, t["source_verifications"], CHECK, **{**base, **fields}) == constraint


def test_ledger_awards_once_and_is_append_only(db, t, g):
    table = t["contribution_ledger"]
    base = {"participant_id": g["people"][1], "case_id": g["low"], "reason": "CORROBORATION"}
    award = ins(db, table, **base, entry_type="AWARD", points=1)
    assert rejected(db, table, UNIQUE, **base, entry_type="AWARD", points=1) == "uq_contribution_ledger_one_award"
    ins(db, table, **base, entry_type="FREEZE", points=0, reverses_entry_id=award)
    ins(db, table, **base, entry_type="UNFREEZE", points=0, reverses_entry_id=award)
    ins(db, table, **base, entry_type="REVOKE", points=-1, reverses_entry_id=award)
    assert (
        rejected(db, table, UNIQUE, **base, entry_type="REVOKE", points=-1, reverses_entry_id=award)
        == "uq_contribution_ledger_one_revoke"
    )
    for fields in (
        {"entry_type": "AWARD", "points": 2, "case_id": g["other"]},
        {"entry_type": "FREEZE", "points": 1, "reverses_entry_id": award},
        {"entry_type": "REVOKE", "points": -1},
    ):
        rejected(db, table, CHECK, **{**base, **fields})
    ins(db, table, **{**base, "reason": "UPHELD_OBJECTION", "case_id": g["other"]}, entry_type="AWARD", points=1)
    with pytest.raises(DBAPIError) as error, db.begin_nested():
        db.execute(update(table).where(table.c.id == award).values(points=1))
    assert "append-only" in str(error.value)


def test_case_events_require_reasons_for_manual_decisions_and_are_append_only(db, t, g):
    table = t["community_case_events"]
    base = {"case_id": g["low"], "case_version": 1}
    event = ins(db, table, **base, event_type="SUBMITTED", actor_participant_id=g["people"][0])
    ins(db, table, **base, event_type="PUBLISHED")
    for event_type in ("MANUAL_ACCEPTED", "MANUAL_REJECTED", "EVIDENCE_REQUESTED", "SUPERSEDED"):
        assert (
            rejected(
                db, table, CHECK, **base, event_type=event_type, actor_participant_id=g["people"][2], reason_code="X"
            )
            == "ck_community_case_events_manual_decision_has_reason"
        )
    ins(
        db,
        table,
        **base,
        event_type="MANUAL_REJECTED",
        actor_participant_id=g["people"][2],
        reason_code="INSUFFICIENT_EVIDENCE",
        reason_text="照片未顯示照明設備",
    )
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(update(table).where(table.c.id == event).values(reason_text="rewritten"))


def test_precheck_runs_are_immutable_and_non_pass_results_explain_why(db, t, g):
    run = ins(
        db,
        t["community_prechecks"],
        case_id=g["low"],
        revision=1,
        rule_version="precheck-1",
        outcome="NEEDS_MANUAL",
        evaluated_at=NOW,
    )
    results = t["community_precheck_results"]
    ins(db, results, precheck_id=run, check_code="IDENTITY", outcome="PASS")
    assert (
        rejected(db, results, CHECK, precheck_id=run, check_code="PHOTO_TIME", outcome="NEEDS_MANUAL")
        == "ck_community_precheck_results_non_pass_has_reason"
    )
    ins(db, results, precheck_id=run, check_code="PHOTO_TIME", outcome="NEEDS_MANUAL", reason_code="TIMEZONE_UNKNOWN")
    assert (
        rejected(db, results, UNIQUE, precheck_id=run, check_code="IDENTITY", outcome="PASS")
        == "uq_community_precheck_results_precheck_id_check_code"
    )
    for table in (t["community_prechecks"], results):
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(update(table).values(outcome="PASS"))


def test_idempotency_records_are_scoped_per_user_and_operation(db, t, g):
    table = t["idempotency_records"]
    base = {
        "user_id": g["users"][0],
        "operation": "create_case",
        "idempotency_key": "k1",
        "request_sha256": SHA,
        "response_status": 201,
        "response_body": {"id": 1},
        "expires_at": NOW + timedelta(days=3650),
    }
    ins(db, table, **base)
    assert rejected(db, table, UNIQUE, **base) == "uq_idempotency_records_user_id_operation_idempotency_key"
    ins(db, table, **{**base, "user_id": g["users"][1]})
    ins(db, table, **{**base, "operation": "add_stance"})
    for fields in (
        {"idempotency_key": ""},
        {"request_sha256": "x"},
        {"response_status": 100},
        {"expires_at": NOW - timedelta(days=3650)},
    ):
        rejected(db, table, CHECK, **{**base, "idempotency_key": "k2", **fields})


def test_deleting_a_user_keeps_participant_identity_for_recusal(db, t, g):
    participants = t["community_participants"]
    db.execute(delete(t["users"]).where(t["users"].c.id == g["users"][0]))
    row = db.execute(select(participants).where(participants.c.id == g["people"][0])).one()
    assert row.user_id is None
    case = db.execute(select(t["community_cases"]).where(t["community_cases"].c.id == g["low"])).one()
    assert case.author_participant_id == g["people"][0]
    assert rejected(db, participants, UNIQUE, user_id=g["users"][1]) == "uq_community_participants_user_id"


def test_lot_delete_cascades_community_records(db, t, g):
    ins(db, t["community_evidence_photos"], **photo(g))
    ins(db, t["community_case_events"], case_id=g["low"], case_version=1, event_type="SUBMITTED")
    ins(
        db,
        t["contribution_ledger"],
        participant_id=g["people"][0],
        case_id=g["low"],
        entry_type="AWARD",
        reason="ORIGINAL_REPORT",
        points=1,
    )
    db.execute(delete(t["parking_lots"]).where(t["parking_lots"].c.id == g["lots"][0]))
    assert db.execute(select(t["community_cases"].c.id)).scalars().all() == [g["other"]]
    assert db.execute(select(t["community_case_revisions"].c.id)).scalars().all() == [g["other_revision"]]
    for name in ("community_evidence_photos", "community_case_events", "contribution_ledger"):
        assert db.execute(select(t[name])).first() is None, name


def test_participants_cannot_be_deleted_while_referenced(db, t, g):
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        db.execute(delete(t["community_participants"]).where(t["community_participants"].c.id == g["people"][0]))
    assert error.value.orig.sqlstate == "23001"  # restrict_violation


def test_downgrade_to_005_keeps_legacy_reports_and_removes_community_schema(engine):
    command.upgrade(ALEMBIC, "head")
    with engine.begin() as connection:
        lot = connection.execute(
            text(
                "INSERT INTO parking_lots (name, location) "
                "VALUES ('legacy', 'SRID=4326;POINT(121.5 25.0)') RETURNING id"
            )
        ).scalar_one()
        connection.execute(
            text("INSERT INTO user_reports (parking_id, report_type, status) VALUES (:lot, 'WRONG_RATE', 'VERIFIED')"),
            {"lot": lot},
        )
    try:
        command.downgrade(ALEMBIC, "005_vehicle_classes")
        with engine.connect() as connection:
            tables = set(
                connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars()
            )
            assert not {name for name in tables if name.startswith("community_")}
            assert not {"contribution_ledger", "idempotency_records", "source_verifications"} & tables
            assert (
                connection.execute(text("SELECT 1 FROM pg_proc WHERE proname='community_reject_update'")).first()
                is None
            )
            assert connection.execute(text("SELECT 1 FROM pg_type WHERE typname='case_review_status'")).first() is None
        command.upgrade(ALEMBIC, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT status::text FROM user_reports WHERE parking_id = :lot"), {"lot": lot}
                ).scalar_one()
                == "VERIFIED"
            )
            assert connection.execute(text("SELECT count(*) FROM community_cases")).scalar_one() == 0
    finally:
        command.upgrade(ALEMBIC, "head")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM parking_lots WHERE id = :lot"), {"lot": lot})


def _append_only_violation(db, statement):
    with pytest.raises(DBAPIError) as error, db.begin_nested():
        db.execute(statement)
    assert error.value.orig.sqlstate == "23001", error.value


def test_revision_proposals_are_immutable_and_approval_is_set_once(db, t, g):
    table = t["community_case_revisions"]
    row = table.c.id == g["revision"]
    for values in (
        {"proposed_value": {"present": False}},
        {"source_url": "https://example.com/changed"},
        {"description": "rewritten"},
    ):
        _append_only_violation(db, update(table).where(row).values(**values))


def test_revision_description_approval_once(db, t, g):
    table = t["community_case_revisions"]
    revision = ins(
        db,
        table,
        case_id=g["low"],
        revision=2,
        proposed_value={"present": True},
        description="燈光正常",
        created_by_participant_id=g["people"][0],
    )
    row = table.c.id == revision
    db.execute(update(table).where(row).values(description_approved_at=NOW))
    _append_only_violation(db, update(table).where(row).values(description_approved_at=NOW + timedelta(days=1)))
    _append_only_violation(db, update(table).where(row).values(description_approved_at=None))


def test_stances_change_only_by_withdrawal_or_invalidation(db, t, g):
    table = t["community_case_stances"]
    stance = ins(
        db, table, case_id=g["low"], participant_id=g["people"][1], stance="SUPPORT", observed_value={"present": True}
    )
    row = table.c.id == stance
    _append_only_violation(db, update(table).where(row).values(stance="OPPOSE", observed_value={"present": False}))
    _append_only_violation(db, update(table).where(row).values(participant_id=g["people"][2]))
    db.execute(update(table).where(row).values(withdrawn_at=text("now()")))
    _append_only_violation(db, update(table).where(row).values(withdrawn_at=None))
    db.execute(update(table).where(row).values(invalidated_at=NOW + timedelta(days=1), invalidated_reason="DUPLICATE"))
    _append_only_violation(db, update(table).where(row).values(invalidated_reason="OTHER"))


def test_photo_receipt_classification_cannot_be_rewritten(db, t, g):
    table = t["community_evidence_photos"]
    missing = ins(
        db,
        table,
        **photo(
            g,
            key="missing",
            time_status="MISSING",
            exif_datetime_original=None,
            exif_offset_time_original=None,
            captured_at=None,
        ),
    )
    row = table.c.id == missing
    # A self-consistent VALID row is still rejected: classification is fixed at receipt.
    _append_only_violation(
        db,
        update(table)
        .where(row)
        .values(
            time_status="VALID",
            exif_datetime_original="2026:10:10 11:00:00",
            exif_offset_time_original="+08:00",
            captured_at=NOW - timedelta(hours=1),
        ),
    )
    for values in ({"normalized_sha256": "b" * 64}, {"received_at": NOW - timedelta(days=1)}, {"stance_id": None}):
        _append_only_violation(db, update(table).where(row).values(**values))
    db.execute(update(table).where(row).values(deleted_at=NOW, storage_key=None))
    _append_only_violation(db, update(table).where(row).values(deleted_at=NOW + timedelta(days=1)))
    deleted = db.execute(select(table).where(row)).one()
    assert (deleted.time_status, deleted.normalized_sha256, deleted.storage_key) == ("MISSING", SHA, None)


def test_ledger_reversals_must_reference_the_same_award(db, t, g):
    table = t["contribution_ledger"]
    award = ins(
        db, table, participant_id=g["people"][1], case_id=g["low"], entry_type="AWARD", reason="CORROBORATION", points=1
    )
    other_award = ins(
        db, table, participant_id=g["people"][2], case_id=g["low"], entry_type="AWARD", reason="CORROBORATION", points=1
    )
    freeze = ins(
        db,
        table,
        participant_id=g["people"][1],
        case_id=g["low"],
        entry_type="FREEZE",
        reason="CORROBORATION",
        points=0,
        reverses_entry_id=award,
    )
    base = {"participant_id": g["people"][1], "case_id": g["low"], "reason": "CORROBORATION"}
    rejected(db, table, FK, **base, entry_type="REVOKE", points=-1, reverses_entry_id=other_award)
    rejected(db, table, FK, **{**base, "case_id": g["other"]}, entry_type="REVOKE", points=-1, reverses_entry_id=award)
    with pytest.raises(DBAPIError) as error, db.begin_nested():
        ins(db, table, **base, entry_type="UNFREEZE", points=0, reverses_entry_id=freeze)
    assert error.value.orig.sqlstate == CHECK
    ins(db, table, **base, entry_type="UNFREEZE", points=0, reverses_entry_id=award)


def test_verified_source_publications_have_no_term(db, t, g):
    base = {
        "parking_id": g["lots"][0],
        "zone_id": g["zones"][0],
        "fact_type": "PARKING_PERMISSION",
        "vehicle": "NORMAL_HEAVY",
        "author_participant_id": g["people"][0],
        "review_status": "ACCEPTED",
        "publication_state": "PUBLISHED",
        "publication_basis": "VERIFIED_SOURCE",
        "first_published_at": NOW,
    }
    for until in (NOW + timedelta(days=90), NOW + timedelta(days=1)):
        assert (
            rejected(db, t["community_cases"], CHECK, **base, published_until=until)
            == "ck_community_cases_observation_term_90_days"
        )
    ins(db, t["community_cases"], **base)


def test_prechecks_reference_an_existing_revision(db, t, g):
    table = t["community_prechecks"]
    base = {"case_id": g["low"], "rule_version": "precheck-1", "outcome": "PASS", "evaluated_at": NOW}
    ins(db, table, **base, revision=1)
    rejected(db, table, FK, **base, revision=999)
    rejected(db, table, FK, **{**base, "case_id": g["rule"]}, revision=1)


def test_participant_suspension_requires_a_reason(db, t, g):
    table = t["community_participants"]
    row = table.c.id == g["people"][1]
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        db.execute(update(table).where(row).values(suspended_at=NOW))
    assert error.value.orig.diag.constraint_name == "ck_community_participants_suspension_has_reason"
    db.execute(update(table).where(row).values(suspended_at=NOW, suspended_reason="ABUSE"))
    db.execute(update(table).where(row).values(suspended_at=None, suspended_reason=None))
