"""Exercise the migrated PostgreSQL schema, including actual rejected writes."""

from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, Enum, MetaData, create_engine, delete, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, configure_mappers

from app.config import settings
from app.domain.parking import ParkingFacts, Provenance, RuleFact, ZoneFacts
from app.models import Base, ParkingLot, ParkingZone
from app.services import ParkingCompatibilityService
from tests.fixtures.parking import (
    EVALUATION_AT,
    INVALID_REALTIME_CASES,
    MIXED_REALTIME_CASES,
    NONINTEGER_REALTIME_CASES,
    RULE_CASES,
)

TABLES = {
    "parking_lots",
    "parking_zones",
    "parking_rules",
    "parking_rates",
    "parking_rate_rules",
    "parking_rate_sources",
    "parking_realtime",
    "parking_entrances",
    "parking_facilities",
    "data_sources",
    "raw_import_batches",
    "raw_parking_records",
    "users",
    "user_vehicles",
    "favorites",
    "user_reports",
    "report_photos",
    "access_tokens",
    "community_participants",
    "source_verifications",
    "community_cases",
    "community_case_revisions",
    "community_case_stances",
    "community_evidence_photos",
    "community_prechecks",
    "community_precheck_results",
    "community_case_events",
    "contribution_ledger",
    "idempotency_records",
}


@pytest.fixture(scope="module")
def schema_engine():
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    engine = create_engine(settings.database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db(schema_engine):
    with schema_engine.connect() as connection, connection.begin() as transaction:
        yield connection
        transaction.rollback()


@pytest.fixture(scope="module")
def tables(schema_engine):
    metadata = MetaData()
    metadata.reflect(schema_engine, only=sorted(TABLES))
    return metadata.tables


def insert_id(db, table, **values):
    return db.execute(table.insert().values(**values).returning(table.c.id)).scalar_one()


@pytest.fixture
def evidence(db, tables):
    sources = [
        insert_id(db, tables["data_sources"], code=code, name=code, source_type=kind)
        for code, kind in (("test-city", "GOVERNMENT"), ("test-operator", "OPERATOR"))
    ]
    lots = [
        insert_id(
            db,
            tables["parking_lots"],
            name=f"Test lot {i}",
            city="Taipei",
            location=f"SRID=4326;POINT({121.56 + i * 0.001} 25.03)",
        )
        for i in range(2)
    ]
    zones = [
        insert_id(db, tables["parking_zones"], parking_id=lots[0], name=kind, space_type=kind, capacity=20)
        for kind in ("HEAVY_ONLY", "MOTO_SHARED", "CAR_SHARED", "LIGHT_MOTO_ONLY")
    ]
    other_zone = insert_id(db, tables["parking_zones"], parking_id=lots[1], name="Other lot", space_type="HEAVY_ONLY")
    return {"sources": sources, "lots": lots, "zones": zones, "other_zone": other_zone}


def test_all_tables_and_orm_mappings(schema_engine):
    assert (
        set(inspect(schema_engine).get_table_names(schema="public")) - {"alembic_version", "spatial_ref_sys"} == TABLES
    )
    assert set(Base.metadata.tables) == TABLES
    configure_mappers()


@pytest.mark.parametrize("extra_visible_schema", [False, True])
def test_migration_matches_orm_metadata(db, extra_visible_schema):
    if extra_visible_schema:
        # PostGIS images expose Tiger/Topology schemas on search_path. Reproduce
        # that shape even when a preceding downgrade removed those extensions.
        db.execute(text("CREATE SCHEMA m1_extension_fixture"))
        db.execute(text("CREATE TABLE m1_extension_fixture.extension_record (id INTEGER PRIMARY KEY)"))
        db.execute(text("SET LOCAL search_path TO public, m1_extension_fixture"))
        assert "extension_record" in inspect(db).get_table_names()
    # Alembic's default reflection uses all visible schemas. Our models belong
    # to public; extension-owned tables must never be proposed for removal.
    db.execute(text("SET LOCAL search_path TO public"))
    context = MigrationContext.configure(
        db,
        opts={
            "compare_type": True,
            "compare_server_default": True,
            "include_object": lambda obj, name, type_, reflected, compare_to: name != "spatial_ref_sys",
        },
    )
    assert compare_metadata(context, Base.metadata) == []


@pytest.mark.parametrize("table_name", ["parking_lots", "parking_entrances"])
def test_geography_points_and_gist_indexes(db, table_name):
    row = db.execute(
        text("SELECT type, srid FROM geography_columns WHERE f_table_schema='public' AND f_table_name=:table"),
        {"table": table_name},
    ).one()
    assert row == ("Point", 4326)
    indexes = (
        db.execute(
            text("SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=:table"),
            {"table": table_name},
        )
        .scalars()
        .all()
    )
    assert sum("USING gist (location)" in definition for definition in indexes) == 1


def test_rule_tiers_preserve_conflicts_nulls_and_provenance(db, tables, evidence):
    rules = tables["parking_rules"]
    for index, case in enumerate(RULE_CASES):
        fields = {key: value for key, value in case.items() if key != "scope"}
        insert_id(
            db,
            rules,
            parking_id=evidence["lots"][0],
            zone_id=evidence["zones"][0] if case["scope"] == "zone" else None,
            source_id=evidence["sources"][index % 2],
            source_record_id=f"rule-{index}",
            effective_from=EVALUATION_AT,
            schedule={"timezone": "Asia/Taipei", "weekdays": [0, 1]},
            fetched_at=EVALUATION_AT,
            **fields,
        )
    rows = db.execute(select(rules).order_by(rules.c.id)).mappings().all()
    assert len(rows) == 5
    assert rows[0]["zone_id"] is None
    assert [row["large_heavy_allowed"] for row in rows[2:]] == [True, False, None]
    for row in rows:
        assert row["normal_heavy_allowed"] is None
        assert row["green_plate_allowed"] is None
        assert row["white_plate_allowed"] is None
        assert row["car_allowed"] is None
        assert row["effective_from"] == EVALUATION_AT
        assert row["schedule"]["timezone"] == "Asia/Taipei"
        assert row["source_record_id"]


@pytest.mark.parametrize("vehicle", ["NORMAL_HEAVY", "LARGE_HEAVY"])
@pytest.mark.parametrize("uncertain_schedule", [False, True])
def test_persisted_rule_fixture_preserves_unknown_in_domain_service(db, tables, evidence, vehicle, uncertain_schedule):
    schedule = (
        {"timezone": "UTC"}
        if uncertain_schedule
        else {
            "timezone": "Asia/Taipei",
            "weekdays": [0],
            "start_time": "12:00",
            "end_time": "13:00",
        }
    )
    for index, case in enumerate(RULE_CASES):
        fields = {key: value for key, value in case.items() if key != "scope"}
        insert_id(
            db,
            tables["parking_rules"],
            parking_id=evidence["lots"][0],
            zone_id=evidence["zones"][0] if case["scope"] == "zone" else None,
            source_id=evidence["sources"][index % 2],
            fetched_at=EVALUATION_AT,
            source_record_id=f"persisted-{index}",
            effective_from=EVALUATION_AT,
            effective_to=EVALUATION_AT + timedelta(hours=1),
            schedule=schedule,
            confidence=0.5,
            **fields,
        )
    facts = []
    for row in db.execute(select(tables["parking_rules"]).order_by(tables["parking_rules"].c.id)).mappings():
        facts.append(
            RuleFact(
                rule_id=row["id"],
                parking_id=row["parking_id"],
                zone_id=row["zone_id"],
                rule_kind=row["rule_kind"],
                authority_priority=row["authority_priority"],
                active=row["active"],
                normal_heavy_allowed=row["normal_heavy_allowed"],
                large_heavy_allowed=row["large_heavy_allowed"],
                effective_from=row["effective_from"],
                effective_to=row["effective_to"],
                schedule=row["schedule"],
                confidence=row["confidence"],
                provenance=Provenance(
                    source_id=row["source_id"], source_record_id=row["source_record_id"], fetched_at=row["fetched_at"]
                ),
            )
        )
    result = ParkingCompatibilityService().evaluate(
        ParkingFacts(evidence["lots"][0], tuple(facts)),
        ZoneFacts(evidence["zones"][0], evidence["lots"][0], "HEAVY_ONLY"),
        vehicle,
        EVALUATION_AT,
    )
    assert result.status == "UNKNOWN"
    assert result.rule_ids == tuple(fact.rule_id for fact in facts[2:])
    assert len(result.provenance) == 3
    assert all(source.fetched_at == EVALUATION_AT for source in result.provenance)
    assert result.confidence is None
    expected_reason = (
        "schedule_unknown"
        if uncertain_schedule
        else ("conflicting_permissions" if vehicle == "LARGE_HEAVY" else "unknown_permission")
    )
    assert result.reason == expected_reason


def test_entrance_access_unknown_is_distinct_from_false(db, tables, evidence):
    entrances = tables["parking_entrances"]
    for index, permission in enumerate((None, False, True)):
        insert_id(
            db,
            entrances,
            parking_id=evidence["lots"][0],
            name=f"Entrance {index}",
            location="SRID=4326;POINT(121.561 25.031)",
            heavy_motorcycle_access=permission,
            source_id=evidence["sources"][1],
            source_record_id=f"entrance-{index}",
        )
    assert db.execute(select(entrances.c.heavy_motorcycle_access).order_by(entrances.c.id)).scalars().all() == [
        None,
        False,
        True,
    ]


def test_mixed_zones_keep_independent_observations(db, tables, evidence):
    realtime = tables["parking_realtime"]
    for index, case in enumerate(MIXED_REALTIME_CASES):
        insert_id(
            db,
            realtime,
            zone_id=evidence["zones"][index],
            source_id=evidence["sources"][index % 2],
            source_record_id=f"realtime-{index}",
            **case,
        )
    rows = db.execute(select(realtime).order_by(realtime.c.id)).mappings().all()
    assert [row["status"] for row in rows] == ["AVAILABLE", "FULL", "UNKNOWN", "AVAILABLE"]
    assert rows[1]["fetched_at"] < rows[0]["fetched_at"]
    assert rows[0]["source_id"] != rows[1]["source_id"]
    assert rows[2]["available_spaces"] is None
    assert rows[2]["fetched_at"] is None
    assert rows[3]["total_spaces"] is None
    assert not {"freshness", "available_spaces", "total_spaces"}.intersection(tables["parking_lots"].c.keys())


@pytest.mark.parametrize("case", INVALID_REALTIME_CASES)
def test_invalid_realtime_rejected(db, tables, evidence, case):
    with pytest.raises(IntegrityError), db.begin_nested():
        insert_id(
            db,
            tables["parking_realtime"],
            zone_id=evidence["zones"][0],
            source_id=evidence["sources"][0],
            **case,
        )


def test_raw_evidence_preserves_invalid_counts(db, tables, evidence):
    batch = insert_id(db, tables["raw_import_batches"], source_id=evidence["sources"][0])
    cases = (*INVALID_REALTIME_CASES, *NONINTEGER_REALTIME_CASES)
    for case in cases:
        insert_id(
            db,
            tables["raw_parking_records"],
            batch_id=batch,
            source_id=evidence["sources"][0],
            external_id="duplicate-upstream-id",
            payload=case,
        )
    rows = (
        db.execute(select(tables["raw_parking_records"].c.payload).order_by(tables["raw_parking_records"].c.id))
        .scalars()
        .all()
    )
    assert rows == list(cases)


@pytest.mark.parametrize("column", ["available_spaces", "total_spaces"])
def test_normalized_count_columns_are_integer(schema_engine, column):
    columns = {item["name"]: item for item in inspect(schema_engine).get_columns("parking_realtime")}
    assert columns[column]["type"].python_type is int
    assert columns[column]["nullable"]


def test_rule_cannot_reference_another_lots_zone(db, tables, evidence):
    with pytest.raises(IntegrityError), db.begin_nested():
        insert_id(
            db,
            tables["parking_rules"],
            parking_id=evidence["lots"][0],
            zone_id=evidence["other_zone"],
            rule_kind="BASELINE",
            authority_priority=10,
            source_id=evidence["sources"][0],
        )


def test_orphan_zone_rejected(db, tables):
    with pytest.raises(IntegrityError), db.begin_nested():
        insert_id(db, tables["parking_zones"], parking_id=-1, name="Orphan", space_type="HEAVY_ONLY")


def test_negative_capacity_rejected(db, tables, evidence):
    with pytest.raises(IntegrityError), db.begin_nested():
        insert_id(
            db,
            tables["parking_zones"],
            parking_id=evidence["lots"][0],
            name="Invalid",
            space_type="HEAVY_ONLY",
            capacity=-1,
        )


def test_spatial_roundtrip_keeps_entrance_separate(db, tables, evidence):
    insert_id(
        db,
        tables["parking_entrances"],
        parking_id=evidence["lots"][0],
        name="Separate entrance",
        location="SRID=4326;POINT(121.561 25.031)",
        source_id=evidence["sources"][0],
    )
    distance = db.execute(
        text(
            "SELECT ST_Distance(l.location, e.location) FROM parking_lots l "
            "JOIN parking_entrances e ON e.parking_id=l.id WHERE l.id=:id"
        ),
        {"id": evidence["lots"][0]},
    ).scalar_one()
    assert 100 < distance < 200


@pytest.fixture
def graph(db, tables, evidence):
    """One valid write per table, also reused to test every declared FK."""
    lot, zone, source = evidence["lots"][0], evidence["zones"][0], evidence["sources"][0]
    user = insert_id(db, tables["users"], auth_subject="fixture-subject")
    rate = insert_id(
        db,
        tables["parking_rates"],
        zone_id=zone,
        source_id=source,
        parse_status="RAW_ONLY",
        raw_text="Unparsed source rate",
    )
    batch = insert_id(db, tables["raw_import_batches"], source_id=source)
    report = insert_id(db, tables["user_reports"], user_id=user, parking_id=lot, zone_id=zone, report_type="OTHER")
    return {
        "data_sources": {"code": "another-source", "name": "Test", "source_type": "MANUAL"},
        "parking_lots": {
            "name": "Test",
            "location": "SRID=4326;POINT(121.56 25.03)",
            "source_id": source,
            "external_id": "unique-lot",
        },
        "parking_zones": {"parking_id": lot, "space_type": "HEAVY_ONLY"},
        "parking_rules": {
            "parking_id": lot,
            "zone_id": zone,
            "source_id": source,
            "rule_kind": "BASELINE",
            "authority_priority": 10,
        },
        "parking_rates": {
            "zone_id": zone,
            "source_id": source,
            "parse_status": "RAW_ONLY",
            "raw_text": "Unparsed rate",
        },
        "parking_rate_rules": {"rate_id": rate, "day_type": "ALL"},
        "parking_rate_sources": {"rate_id": rate, "source_id": source},
        "parking_realtime": {"zone_id": zone, "source_id": source, "status": "UNKNOWN"},
        "parking_entrances": {"parking_id": lot, "source_id": source},
        "parking_facilities": {"parking_id": lot, "source_id": source, "code": "ROOF"},
        "raw_import_batches": {"source_id": source},
        "raw_parking_records": {"batch_id": batch, "source_id": source, "payload": {}},
        "users": {"auth_subject": "another-subject"},
        "user_vehicles": {"user_id": user, "vehicle_type": "LARGE_HEAVY"},
        "favorites": {"user_id": user, "parking_id": lot},
        "user_reports": {"user_id": user, "parking_id": lot, "zone_id": zone, "report_type": "OTHER"},
        "report_photos": {"report_id": report, "storage_key": "test/photo.jpg"},
    }


def test_all_foreign_keys_reject_orphans(db, tables, graph):
    checked = 0
    for name, fields in graph.items():
        table = tables[name]
        # Validate the base write without leaving a duplicate unique key behind.
        with db.begin_nested() as valid_write:
            insert_id(db, table, **fields)
            valid_write.rollback()
        for constraint in table.foreign_key_constraints:
            bad_fields = {**fields, next(iter(constraint.columns)).name: -999999}
            with pytest.raises(IntegrityError) as error, db.begin_nested():
                insert_id(db, table, **bad_fields)
            assert error.value.orig.sqlstate == "23503", (name, constraint.name)
            checked += 1
    assert checked >= 25


@pytest.mark.parametrize(
    "table_name, constraint",
    [
        ("data_sources", "uq_data_sources_code"),
        ("parking_lots", "uq_parking_lots_source_id_external_id"),
        ("parking_facilities", "uq_parking_facilities_parking_id_code"),
        ("users", "uq_users_auth_subject"),
        ("favorites", "uq_favorites_user_id_parking_id"),
        ("report_photos", "uq_report_photos_storage_key"),
    ],
)
def test_business_uniqueness(db, tables, graph, table_name, constraint):
    insert_id(db, tables[table_name], **graph[table_name])
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert_id(db, tables[table_name], **graph[table_name])
    assert error.value.orig.diag.constraint_name == constraint


@pytest.mark.parametrize(
    "enum_name, labels",
    [
        ("parking_space_type", ["HEAVY_ONLY", "MOTO_SHARED", "CAR_SHARED", "LIGHT_MOTO_ONLY"]),
        ("vehicle_type", ["NORMAL_HEAVY", "LARGE_HEAVY", "CAR"]),
        ("parking_rule_kind", ["BASELINE", "EXCEPTION"]),
        (
            "parking_rate_type",
            ["FREE", "HOURLY", "PER_ENTRY", "TIME_BLOCK", "PROGRESSIVE", "FLAT", "DAILY", "MONTHLY", "CUSTOM"],
        ),
        ("rate_parse_status", ["PARSED", "PARTIALLY_PARSED", "RAW_ONLY", "INVALID"]),
        ("rate_day_type", ["ALL", "WEEKDAY", "WEEKEND", "HOLIDAY", "SPECIAL"]),
        ("realtime_status", ["AVAILABLE", "FULL", "UNKNOWN", "CLOSED"]),
        ("data_source_type", ["GOVERNMENT", "OPERATOR", "COMMUNITY", "MANUAL"]),
    ],
)
def test_native_enums(schema_engine, enum_name, labels):
    actual = {item["name"]: item["labels"] for item in inspect(schema_engine).get_enums()}
    assert actual[enum_name] == labels


def test_stale_is_not_an_observation_status(db, tables, evidence):
    with pytest.raises(DBAPIError) as error, db.begin_nested():
        insert_id(
            db,
            tables["parking_realtime"],
            zone_id=evidence["zones"][0],
            source_id=evidence["sources"][0],
            status="STALE",
        )
    assert error.value.orig.sqlstate == "22P02"


def test_raw_rate_and_independent_rate_provenance(db, tables, evidence):
    raw_text = "大型重機依現場公告收費"
    rate = insert_id(
        db,
        tables["parking_rates"],
        zone_id=evidence["zones"][1],
        source_id=evidence["sources"][1],
        raw_text=raw_text,
        parse_status="RAW_ONLY",
    )
    insert_id(
        db,
        tables["parking_rate_sources"],
        rate_id=rate,
        source_id=evidence["sources"][0],
        source_record_id="original-city-rate",
        raw_text=raw_text,
        fetched_at=EVALUATION_AT,
    )
    row = db.execute(select(tables["parking_rates"]).where(tables["parking_rates"].c.id == rate)).mappings().one()
    assert row["rate_type"] is None
    assert row["base_amount"] is None
    assert row["daily_max_amount"] is None
    assert row["raw_text"] == raw_text
    assert row["zone_id"] == evidence["zones"][1]
    source = db.execute(select(tables["parking_rate_sources"])).mappings().one()
    assert source["source_id"] != row["source_id"]


@pytest.mark.parametrize(
    "rate_type", ["FREE", "HOURLY", "PER_ENTRY", "TIME_BLOCK", "PROGRESSIVE", "FLAT", "DAILY", "MONTHLY", "CUSTOM"]
)
def test_all_rate_types_with_exact_money(db, tables, evidence, rate_type):
    rate = insert_id(
        db,
        tables["parking_rates"],
        zone_id=evidence["zones"][0],
        source_id=evidence["sources"][0],
        rate_type=rate_type,
        parse_status="PARSED",
        base_amount=Decimal("20.15"),
        vehicle_type="LARGE_HEAVY",
    )
    amount = db.execute(select(tables["parking_rates"].c.base_amount).where(tables["parking_rates"].c.id == rate))
    assert amount.scalar_one() == Decimal("20.15")


def test_all_space_types_and_lot_zone_relationship(db, evidence):
    with Session(bind=db) as session:
        lot = session.get(ParkingLot, evidence["lots"][0])
        assert {zone.space_type for zone in lot.zones} == {
            "HEAVY_ONLY",
            "MOTO_SHARED",
            "CAR_SHARED",
            "LIGHT_MOTO_ONLY",
        }
        assert session.get(ParkingZone, evidence["zones"][0]).lot is lot


def test_report_cannot_reference_another_lots_zone(db, tables, graph, evidence):
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert_id(db, tables["user_reports"], **{**graph["user_reports"], "zone_id": evidence["other_zone"]})
    assert error.value.orig.sqlstate == "23503"


def test_raw_record_cannot_claim_another_sources_batch(db, tables, graph, evidence):
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert_id(
            db, tables["raw_parking_records"], **{**graph["raw_parking_records"], "source_id": evidence["sources"][1]}
        )
    assert error.value.orig.sqlstate == "23503"


@pytest.mark.parametrize("status, available", [("AVAILABLE", 2), ("FULL", 0), ("CLOSED", 0)])
def test_aging_retains_observed_status(db, tables, evidence, status, available):
    realtime = tables["parking_realtime"]
    row_id = insert_id(
        db,
        realtime,
        zone_id=evidence["zones"][0],
        source_id=evidence["sources"][0],
        status=status,
        available_spaces=available,
        total_spaces=10,
        fetched_at=EVALUATION_AT,
    )
    db.execute(update(realtime).where(realtime.c.id == row_id).values(fetched_at=EVALUATION_AT - timedelta(days=1)))
    assert db.execute(select(realtime.c.status).where(realtime.c.id == row_id)).scalar_one() == status


@pytest.mark.parametrize(
    "permission",
    ["green_plate_allowed", "white_plate_allowed", "normal_heavy_allowed", "large_heavy_allowed", "car_allowed"],
)
def test_every_vehicle_permission_roundtrips_three_states(db, tables, graph, permission):
    rules = tables["parking_rules"]
    ids = [insert_id(db, rules, **{**graph["parking_rules"], permission: value}) for value in (None, False, True)]
    assert db.execute(select(rules.c[permission]).where(rules.c.id.in_(ids)).order_by(rules.c.id)).scalars().all() == [
        None,
        False,
        True,
    ]


@pytest.mark.parametrize("missing", ["rule_kind", "authority_priority"])
def test_rule_authority_must_be_explicit(db, tables, graph, missing):
    fields = {key: value for key, value in graph["parking_rules"].items() if key != missing}
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert_id(db, tables["parking_rules"], **fields)
    assert error.value.orig.sqlstate == "23502"


@pytest.mark.parametrize("table_name", ["parking_rules", "parking_rates"])
def test_effective_windows_must_be_ordered(db, tables, graph, table_name):
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert_id(
            db,
            tables[table_name],
            **graph[table_name],
            effective_from=EVALUATION_AT,
            effective_to=EVALUATION_AT - timedelta(seconds=1),
        )
    assert error.value.orig.sqlstate == "23514"


@pytest.mark.parametrize(
    "fields",
    [
        {"start_minute": -1},
        {"end_minute": -1},
        {"end_minute": 0},
        {"start_minute": 60, "end_minute": 30},
        {"start_time": time(8, 0)},
        {"start_time": "24:00", "end_time": "06:00"},
        {"start_time": "22:00", "end_time": "24:00"},
        {"start_time": time(8, 0), "end_time": time(8, 0)},
    ],
)
def test_invalid_rate_windows_rejected(db, tables, graph, fields):
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert_id(db, tables["parking_rate_rules"], **graph["parking_rate_rules"], **fields)
    assert error.value.orig.sqlstate == "23514"


def test_overnight_rate_window_is_valid(db, tables, graph):
    insert_id(
        db, tables["parking_rate_rules"], **graph["parking_rate_rules"], start_time=time(22, 0), end_time=time(6, 0)
    )


def test_nullable_entrance_coordinate_does_not_inherit_lot_constraint(db, tables, graph):
    insert_id(db, tables["parking_entrances"], **graph["parking_entrances"])
    assert Base.metadata.tables["parking_entrances"].c.location.nullable
    assert not Base.metadata.tables["parking_lots"].c.location.nullable


def test_source_and_raw_evidence_cannot_be_cascade_deleted(db, tables, graph, evidence):
    insert_id(db, tables["raw_parking_records"], **graph["raw_parking_records"])
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(delete(tables["data_sources"]).where(tables["data_sources"].c.id == evidence["sources"][0]))
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            delete(tables["raw_import_batches"]).where(
                tables["raw_import_batches"].c.id == graph["raw_parking_records"]["batch_id"]
            )
        )


def test_lot_delete_cascades_zone_scoped_facts_and_reports(db, tables, graph, evidence):
    for table_name in (
        "parking_rules",
        "parking_rates",
        "parking_realtime",
        "parking_entrances",
        "parking_facilities",
        "favorites",
        "report_photos",
    ):
        insert_id(db, tables[table_name], **graph[table_name])
    db.execute(delete(tables["parking_lots"]).where(tables["parking_lots"].c.id == evidence["lots"][0]))
    for table_name in (
        "parking_rules",
        "parking_rates",
        "parking_realtime",
        "parking_entrances",
        "parking_facilities",
        "favorites",
        "user_reports",
        "report_photos",
    ):
        assert db.execute(select(tables[table_name])).first() is None
    assert db.execute(select(tables["data_sources"])).first() is not None


def test_check_constraints_fk_policies_and_all_enum_labels_match_models(schema_engine):
    inspector = inspect(schema_engine)
    expected_enums = {}
    for name, table in Base.metadata.tables.items():
        assert {item["name"] for item in inspector.get_check_constraints(name)} == {
            constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint)
        }, name
        expected_fks = {
            constraint.name: (tuple(column.name for column in constraint.columns), constraint.ondelete)
            for constraint in table.foreign_key_constraints
        }
        actual_fks = {
            item["name"]: (tuple(item["constrained_columns"]), item["options"].get("ondelete"))
            for item in inspector.get_foreign_keys(name)
        }
        assert actual_fks == expected_fks, name
        for column in table.c:
            if isinstance(column.type, Enum):
                expected_enums[column.type.name] = column.type.enums
    assert {item["name"]: item["labels"] for item in inspector.get_enums()} == expected_enums


@pytest.mark.parametrize(
    "table_name, fields",
    [
        ("parking_rates", {"parse_status": "PARSED", "rate_type": None}),
        ("parking_rates", {"raw_text": None}),
        ("parking_rates", {"parse_status": "INVALID", "raw_text": None}),
        ("parking_rates", {"parse_status": "PARTIALLY_PARSED", "raw_text": None}),
        ("parking_rates", {"base_amount": -1}),
        ("parking_rates", {"unit_minutes": 0}),
        ("parking_rates", {"free_minutes": -1}),
        ("parking_rates", {"daily_max_amount": -1}),
        ("parking_rate_rules", {"amount": -1}),
        ("parking_rate_rules", {"unit_minutes": 0}),
        ("parking_rate_rules", {"max_amount": -1}),
        ("parking_rules", {"confidence": -0.1}),
        ("parking_rules", {"confidence": 1.1}),
    ],
)
def test_invalid_normalized_rate_and_confidence_values(db, tables, graph, table_name, fields):
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert_id(db, tables[table_name], **{**graph[table_name], **fields})
    assert error.value.orig.sqlstate == "23514"


def test_postgresql_numeric_cast_requires_strict_validation_before_binding(db, tables, evidence):
    # The INTEGER column constrains stored values, not the original payload type.
    # A normalizer must reject this fractional input before PostgreSQL casts it.
    row_id = insert_id(
        db,
        tables["parking_realtime"],
        source_id=evidence["sources"][0],
        zone_id=evidence["zones"][0],
        **NONINTEGER_REALTIME_CASES[0],
    )
    realtime = tables["parking_realtime"]
    assert db.execute(select(realtime.c.available_spaces).where(realtime.c.id == row_id)).scalar_one() == 2


def test_individual_zone_delete_is_blocked_by_report(db, tables, graph, evidence):
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        db.execute(delete(tables["parking_zones"]).where(tables["parking_zones"].c.id == evidence["zones"][0]))
    assert error.value.orig.sqlstate == "23503"
