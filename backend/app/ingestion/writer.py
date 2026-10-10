"""Transactional source-owned upserts. Other sources' facts are never replaced."""

import hashlib
import json
from dataclasses import asdict
from datetime import datetime

from geoalchemy2 import WKTElement
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.contracts import FeedSnapshot, NormalizedLot, NormalizedRealtime, NormalizedZone, RecordError
from app.ingestion.policies import POLICIES
from app.ingestion.sources import TAIPEI, CitySource
from app.models import (
    DataSource,
    DataSourceType,
    ParkingEntrance,
    ParkingLot,
    ParkingRate,
    ParkingRateRule,
    ParkingRateSource,
    ParkingRealtime,
    ParkingRule,
    ParkingZone,
    RealtimeStatus,
    RuleKind,
)


def identity(external_id: str, entity: str, key: str, prefix: str = TAIPEI.key) -> str:
    digest = hashlib.sha256(f"{external_id}\0{entity}\0{key}".encode()).hexdigest()
    return f"{prefix}:{entity}:{digest}"


def _close_rate(rate: ParkingRate, at: datetime) -> None:
    if rate.effective_from is not None and at <= rate.effective_from:
        raise RecordError("INVALID_RETIREMENT_INSTANT", "Rate retirement must follow its start instant")
    rate.effective_to = at


def point(lat: float, lng: float) -> WKTElement:
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


def _older(snapshot: FeedSnapshot, source_updated_at: datetime | None, fetched_at: datetime | None) -> bool:
    # A stored source timestamp later than this fetch is an upstream clock error,
    # not ordering evidence; honoring it would freeze every later snapshot.
    return (fetched_at is not None and snapshot.fetched_at < fetched_at) or (
        snapshot.source_updated_at is not None
        and source_updated_at is not None
        and source_updated_at <= snapshot.fetched_at
        and snapshot.source_updated_at < source_updated_at
    )


class ParkingIngestionWriter:
    def __init__(self, session: AsyncSession, source: CitySource = TAIPEI):
        self.session = session
        self.source = source

    def identity(self, external_id: str, entity: str, key: str) -> str:
        return identity(external_id, entity, key, self.source.identity_prefix())

    async def active_lot_count(self, source_id: int) -> int:
        return (
            await self.session.scalar(
                select(func.count(func.distinct(ParkingZone.parking_id))).where(
                    ParkingZone.source_id == source_id, ParkingZone.source_active.is_(True)
                )
            )
        ) or 0

    async def ensure_sources(self) -> dict[str, int]:
        ids = {}
        for kind, policy in self.source.feeds.items():
            values = dict(
                code=policy.code,
                name=policy.name,
                source_type=DataSourceType.GOVERNMENT,
                url=policy.url,
                license=self.source.license_url,
                attribution=self.source.attribution,
                freshness_seconds=policy.freshness_seconds,
                freshness_uses_source_timestamp=policy.freshness_uses_source_timestamp,
            )
            statement = insert(DataSource).values(**values)
            statement = statement.on_conflict_do_update(
                constraint="uq_data_sources_code", set_={**values, "updated_at": datetime.now().astimezone()}
            ).returning(DataSource.id)
            ids[kind] = (await self.session.execute(statement)).scalar_one()
        return ids

    async def ensure_policy_source(self, code: str) -> int:
        policy = POLICIES[code]
        values = dict(
            code=code,
            name=policy.name,
            source_type=DataSourceType.GOVERNMENT,
            url=policy.url,
            attribution=policy.scope,
            freshness_seconds=86400,
            freshness_uses_source_timestamp=False,
        )
        statement = insert(DataSource).values(**values)
        statement = statement.on_conflict_do_update(constraint="uq_data_sources_code", set_=values)
        return (await self.session.execute(statement.returning(DataSource.id))).scalar_one()

    async def retire_policy_rules(self, parking_id: int, live_ids: set[str], at: datetime) -> None:
        rules = (
            await self.session.scalars(
                select(ParkingRule).where(
                    ParkingRule.parking_id == parking_id,
                    ParkingRule.source_record_id.startswith(f"{self.source.key}:policy:", autoescape=True),
                    ParkingRule.source_id.in_(select(DataSource.id).where(DataSource.code.in_(POLICIES))),
                    ParkingRule.effective_to.is_(None),
                    ParkingRule.active.is_(True),
                )
            )
        ).all()
        for rule in rules:
            if rule.source_record_id not in live_ids:
                if rule.effective_from >= at:
                    rule.active = False
                else:
                    rule.effective_to = at

    async def write_lot(self, lot: NormalizedLot, snapshot: FeedSnapshot, source_id: int) -> None:
        existing = (
            await self.session.scalars(
                select(ParkingLot).where(ParkingLot.source_id == source_id, ParkingLot.external_id == lot.external_id)
            )
        ).one_or_none()
        if existing is not None and _older(snapshot, existing.source_updated_at, existing.fetched_at):
            raise RecordError("STALE_SNAPSHOT", "Older static snapshot cannot replace newer facts")
        if existing is None:
            existing = ParkingLot(source_id=source_id, external_id=lot.external_id)
            self.session.add(existing)
        existing.name, existing.address, existing.district = lot.name, lot.address, lot.district
        existing.city, existing.location = self.source.city_name, point(lot.lat, lot.lng)
        existing.source_updated_at, existing.fetched_at = snapshot.source_updated_at, snapshot.fetched_at
        await self.session.flush()

        old_zones = (
            await self.session.scalars(
                select(ParkingZone).where(ParkingZone.parking_id == existing.id, ParkingZone.source_id == source_id)
            )
        ).all()
        live_zone_keys = {f"{lot.external_id}:{zone.key}" for zone in lot.zones}
        for zone in lot.zones:
            await self._write_zone(existing.id, lot.external_id, zone, snapshot, source_id)
        # A missing category does not become confirmed prohibited. Keep its stable
        # ID and an active NULL rule, preventing M2's space-type default from granting access.
        for old in old_zones:
            if old.external_id not in live_zone_keys:
                old.capacity = None
                old.source_active = False
                await self._write_permission(
                    existing.id,
                    old.id,
                    lot.external_id,
                    old.external_id or str(old.id),
                    None,
                    None,
                    None,
                    None,
                    None,
                    snapshot,
                    source_id,
                    source_record_id=self.identity(lot.external_id, "rule", (old.external_id or "").rsplit(":", 1)[-1]),
                )

        await self.retire_policy_rules(
            existing.id,
            {
                self.identity(lot.external_id, "policy", f"{zone.key}:{rule.key}")
                for zone in lot.zones
                for rule in zone.rules
            },
            snapshot.fetched_at,
        )

        rates = (
            await self.session.scalars(
                select(ParkingRate)
                .join(ParkingZone)
                .where(
                    ParkingZone.parking_id == existing.id,
                    ParkingRate.source_record_id.startswith(f"{self.source.key}:rate:", autoescape=True),
                    or_(
                        ParkingRate.source_id == source_id,
                        ParkingRate.source_id.in_(select(DataSource.id).where(DataSource.code.in_(POLICIES))),
                    ),
                )
            )
        ).all()
        live_rates = {
            self.identity(lot.external_id, "rate", f"{zone.key}:{rate.key}")
            for zone in lot.zones
            for rate in zone.rates
        }
        for rate in rates:
            if rate.source_record_id not in live_rates and rate.effective_to is None:
                _close_rate(rate, snapshot.fetched_at)

        old_entrances = (
            await self.session.scalars(
                select(ParkingEntrance).where(
                    ParkingEntrance.parking_id == existing.id, ParkingEntrance.source_id == source_id
                )
            )
        ).all()
        live_entrances = set()
        for entry in lot.entrances:
            key = self.identity(lot.external_id, "entrance", entry.key)
            live_entrances.add(key)
            entity = next((e for e in old_entrances if e.source_record_id == key), None)
            if entity is None:
                entity = ParkingEntrance(parking_id=existing.id, source_id=source_id, source_record_id=key)
                self.session.add(entity)
            entity.name = entry.name
            entity.notes = None
            entity.location = point(entry.lat, entry.lng) if entry.lat is not None and entry.lng is not None else None
            entity.heavy_motorcycle_access = entry.heavy_access
            entity.source_updated_at, entity.fetched_at = snapshot.source_updated_at, snapshot.fetched_at
        for entity in old_entrances:
            if entity.source_record_id not in live_entrances:
                # Keep source evidence but no longer offer a coordinate as a current entrance.
                entity.location = None
                entity.heavy_motorcycle_access = None
                entity.notes = "Entrance absent from latest source snapshot"

    async def _write_zone(
        self, parking_id: int, external_id: str, zone: NormalizedZone, snapshot: FeedSnapshot, source_id: int
    ) -> None:
        entity = (
            await self.session.scalars(
                select(ParkingZone).where(
                    ParkingZone.source_id == source_id, ParkingZone.external_id == f"{external_id}:{zone.key}"
                )
            )
        ).one_or_none()
        if entity is None:
            entity = ParkingZone(parking_id=parking_id, source_id=source_id, external_id=f"{external_id}:{zone.key}")
            self.session.add(entity)
        entity.name, entity.space_type, entity.capacity = zone.name, zone.space_type, zone.capacity
        entity.source_active = True
        await self.session.flush()
        await self._write_permission(
            parking_id,
            entity.id,
            external_id,
            zone.key,
            zone.green,
            zone.white,
            zone.yellow,
            zone.red,
            zone.car,
            snapshot,
            source_id,
            normal_heavy=zone.normal_heavy,
            large_heavy=zone.large_heavy,
        )
        for rule in zone.rules:
            policy_id = await self.ensure_policy_source(rule.policy_code)
            key = self.identity(external_id, "policy", f"{zone.key}:{rule.key}")
            versions = (
                await self.session.scalars(
                    select(ParkingRule).where(
                        ParkingRule.source_id == policy_id,
                        ParkingRule.source_record_id == key,
                    )
                )
            ).all()
            row = next((r for r in versions if r.active and r.effective_to is None), None)
            if row is not None and (
                row.normal_heavy_allowed != rule.normal_heavy
                or row.large_heavy_allowed != rule.large_heavy
                or row.schedule != rule.schedule
                or row.rule_kind != rule.rule_kind
                or row.authority_priority != rule.authority_priority
            ):
                if row.effective_from >= snapshot.fetched_at:
                    row.active = False
                else:
                    row.effective_to = snapshot.fetched_at
                row = None
            if row is None:
                row = ParkingRule(
                    parking_id=parking_id,
                    zone_id=entity.id,
                    source_id=policy_id,
                    source_record_id=key,
                    effective_from=max(rule.effective_from, snapshot.fetched_at) if versions else rule.effective_from,
                )
                self.session.add(row)
            row.normal_heavy_allowed, row.large_heavy_allowed = rule.normal_heavy, rule.large_heavy
            row.rule_kind, row.authority_priority, row.active = rule.rule_kind, rule.authority_priority, True
            row.schedule = rule.schedule
            row.notes = json.dumps(rule.evidence, ensure_ascii=False, sort_keys=True)
            row.source_updated_at = POLICIES[rule.policy_code].published_at
            row.fetched_at = snapshot.fetched_at
            row.verified_at = snapshot.fetched_at
        for rate in zone.rates:
            # Upcoming policy rates are activated by a later import; a future rate
            # cannot be retired into a backwards or empty effective interval.
            if rate.effective_from is not None and rate.effective_from > snapshot.fetched_at:
                continue
            rate_source_id = await self.ensure_policy_source(rate.policy_code) if rate.policy_code else source_id
            updated_at = POLICIES[rate.policy_code].published_at if rate.policy_code else snapshot.source_updated_at
            key = self.identity(external_id, "rate", f"{zone.key}:{rate.key}")
            versions = (
                await self.session.scalars(
                    select(ParkingRate).where(
                        ParkingRate.source_id == rate_source_id, ParkingRate.source_record_id == key
                    )
                )
            ).all()
            row = next((version for version in versions if version.effective_to is None), None)
            if row is not None and rate.policy_code:
                old_schedules = (
                    await self.session.scalars(
                        select(ParkingRateRule.schedule)
                        .where(ParkingRateRule.rate_id == row.id)
                        .order_by(ParkingRateRule.id)
                    )
                ).all()
                if old_schedules != [r.schedule for r in rate.parsed.rules]:
                    _close_rate(row, snapshot.fetched_at)
                    row = None
            if row is None:
                # A retired interval stays closed; a reappearance is a new version
                # starting now, so evaluation inside the gap never sees this rate.
                row = ParkingRate(
                    zone_id=entity.id,
                    source_id=rate_source_id,
                    source_record_id=key,
                    effective_from=max(snapshot.fetched_at, rate.effective_from)
                    if versions and rate.effective_from
                    else snapshot.fetched_at
                    if versions
                    else rate.effective_from,
                )
                self.session.add(row)
            parsed = rate.parsed
            row.vehicle_type = rate.vehicle
            row.parse_status, row.rate_type, row.raw_text = parsed.parse_status, parsed.rate_type, parsed.raw_text
            row.currency = "TWD"
            row.base_amount, row.unit_minutes = parsed.base_amount, parsed.unit_minutes
            row.free_minutes, row.daily_max_amount = parsed.free_minutes, parsed.daily_max_amount
            row.source_updated_at, row.fetched_at = updated_at, snapshot.fetched_at
            await self.session.flush()
            await self.session.execute(delete(ParkingRateRule).where(ParkingRateRule.rate_id == row.id))
            for rule in parsed.rules:
                self.session.add(ParkingRateRule(rate_id=row.id, **asdict(rule)))
            evidence = (
                await self.session.scalars(
                    select(ParkingRateSource).where(
                        ParkingRateSource.rate_id == row.id,
                        ParkingRateSource.source_id == rate_source_id,
                        ParkingRateSource.source_record_id == key,
                    )
                )
            ).one_or_none()
            if evidence is None:
                evidence = ParkingRateSource(rate_id=row.id, source_id=rate_source_id, source_record_id=key)
                self.session.add(evidence)
            evidence.raw_text, evidence.raw_payload = parsed.raw_text, rate.raw_payload
            evidence.source_updated_at, evidence.fetched_at = updated_at, snapshot.fetched_at

    async def _write_permission(
        self,
        parking_id,
        zone_id,
        external_id,
        key,
        green,
        white,
        yellow,
        red,
        car,
        snapshot,
        source_id,
        *,
        source_record_id=None,
        normal_heavy=None,
        large_heavy=None,
    ):
        record_id = source_record_id or self.identity(external_id, "rule", key)
        row = (
            await self.session.scalars(
                select(ParkingRule).where(ParkingRule.source_id == source_id, ParkingRule.source_record_id == record_id)
            )
        ).one_or_none()
        if row is None:
            row = ParkingRule(parking_id=parking_id, zone_id=zone_id, source_id=source_id, source_record_id=record_id)
            self.session.add(row)
        row.normal_heavy_allowed, row.large_heavy_allowed = normal_heavy, large_heavy
        row.green_plate_allowed, row.white_plate_allowed = green, white
        row.yellow_plate_allowed, row.red_plate_allowed, row.car_allowed = yellow, red, car
        row.rule_kind, row.authority_priority, row.active = RuleKind.BASELINE, self.source.authority_priority, True
        row.confidence = None
        row.notes = self.source.rule_note
        row.source_updated_at, row.fetched_at = snapshot.source_updated_at, snapshot.fetched_at

    async def write_realtime(
        self, record: NormalizedRealtime, snapshot: FeedSnapshot, source_id: int, static_source_id: int
    ) -> None:
        lot = (
            await self.session.scalars(
                select(ParkingLot).where(
                    ParkingLot.source_id == static_source_id, ParkingLot.external_id == record.external_id
                )
            )
        ).one_or_none()
        if lot is None:
            raise RecordError("LOT_NOT_IMPORTED", "Import static parking data before realtime")
        zones = (
            await self.session.scalars(
                select(ParkingZone).where(ParkingZone.parking_id == lot.id, ParkingZone.source_id == static_source_id)
            )
        ).all()
        matched = 0
        for observation in record.observations:
            zone = next((z for z in zones if z.external_id == f"{record.external_id}:{observation.zone_key}"), None)
            if zone is None:
                # No known zone exists for this category; never substitute another zone.
                continue
            matched += 1
            latest = (
                await self.session.scalars(
                    select(ParkingRealtime)
                    .where(ParkingRealtime.zone_id == zone.id, ParkingRealtime.source_id == source_id)
                    .order_by(ParkingRealtime.fetched_at.desc().nullslast(), ParkingRealtime.id.desc())
                    .limit(1)
                )
            ).one_or_none()
            if latest is not None and _older(snapshot, latest.source_updated_at, latest.fetched_at):
                raise RecordError("STALE_SNAPSHOT", "Older realtime snapshot cannot replace newer observations")
            # Cross-feed total is safe only at the same upstream instant. Otherwise
            # counts are current but independently timed static capacity is not a confirmed realtime total.
            total = (
                zone.capacity
                if (snapshot.source_updated_at is not None and snapshot.source_updated_at == lot.source_updated_at)
                else None
            )
            if observation.available is not None and total is not None and observation.available > total:
                raise RecordError("COUNT_EXCEEDS_CAPACITY", "Available count exceeds confirmed category total")
            status = observation.status
            available = observation.available
            if zone.source_active is False:
                status = RealtimeStatus.UNKNOWN
            if status == RealtimeStatus.UNKNOWN:
                available, total = None, None
            record_key = self.identity(record.external_id, "realtime", observation.zone_key)
            if (
                latest is not None
                and latest.fetched_at == snapshot.fetched_at
                and latest.source_updated_at == snapshot.source_updated_at
            ):
                if (latest.status, latest.available_spaces, latest.total_spaces) != (status, available, total):
                    raise RecordError("OBSERVATION_CONFLICT", "Same observation instant has conflicting facts")
                continue
            self.session.add(
                ParkingRealtime(
                    zone_id=zone.id,
                    source_id=source_id,
                    source_record_id=record_key,
                    status=status,
                    available_spaces=available,
                    total_spaces=total,
                    source_updated_at=snapshot.source_updated_at,
                    fetched_at=snapshot.fetched_at,
                )
            )
        if matched == 0 and any(o.status != RealtimeStatus.UNKNOWN for o in record.observations):
            # An UNKNOWN-only record for a lot without that category has nothing to
            # record or supersede; a known count without a zone is a real mismatch.
            raise RecordError("ZONE_NOT_IMPORTED", "No corresponding category zone exists in static data")

    async def reconcile_missing_lots(self, present_ids: set[str], snapshot: FeedSnapshot, source_id: int) -> None:
        lots = (await self.session.scalars(select(ParkingLot).where(ParkingLot.source_id == source_id))).all()
        for lot in lots:
            if lot.external_id in present_ids or _older(snapshot, lot.source_updated_at, lot.fetched_at):
                continue
            lot.source_updated_at, lot.fetched_at = snapshot.source_updated_at, snapshot.fetched_at
            zones = (
                await self.session.scalars(
                    select(ParkingZone).where(ParkingZone.parking_id == lot.id, ParkingZone.source_id == source_id)
                )
            ).all()
            for zone in zones:
                zone.capacity, zone.source_active = None, False
                await self._write_permission(
                    lot.id,
                    zone.id,
                    lot.external_id,
                    (zone.external_id or "").rsplit(":", 1)[-1],
                    None,
                    None,
                    None,
                    None,
                    None,
                    snapshot,
                    source_id,
                )
            await self.retire_policy_rules(lot.id, set(), snapshot.fetched_at)
            rates = (
                await self.session.scalars(
                    select(ParkingRate)
                    .join(ParkingZone)
                    .where(
                        ParkingZone.parking_id == lot.id,
                        ParkingRate.source_record_id.startswith(f"{self.source.key}:rate:", autoescape=True),
                        or_(
                            ParkingRate.source_id == source_id,
                            ParkingRate.source_id.in_(select(DataSource.id).where(DataSource.code.in_(POLICIES))),
                        ),
                    )
                )
            ).all()
            for rate in rates:
                if rate.effective_to is None:
                    _close_rate(rate, snapshot.fetched_at)
            entrances = (
                await self.session.scalars(
                    select(ParkingEntrance).where(
                        ParkingEntrance.parking_id == lot.id, ParkingEntrance.source_id == source_id
                    )
                )
            ).all()
            for entrance in entrances:
                entrance.location, entrance.heavy_motorcycle_access = None, None
                entrance.notes = "Lot absent from latest source snapshot"
