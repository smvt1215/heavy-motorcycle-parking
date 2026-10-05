"""Indexed PostGIS candidates and batched, source-preserving fact loading."""

from collections import defaultdict

from geoalchemy2 import Geography, Geometry
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.discovery import EntranceFact, LotSnapshot, RateFact, RateRuleFact, RealtimeFact, ZoneSnapshot
from app.domain.parking import ParkingFacts, Provenance, RuleFact, ZoneFacts
from app.models import (
    DataSource,
    ParkingEntrance,
    ParkingLot,
    ParkingRate,
    ParkingRateRule,
    ParkingRateSource,
    ParkingRealtime,
    ParkingRule,
    ParkingZone,
)


def source_evidence(row, source: DataSource) -> Provenance:
    return Provenance(
        source_id=row.source_id,
        source_type=str(source.source_type),
        source_record_id=row.source_record_id,
        source_updated_at=row.source_updated_at,
        fetched_at=row.fetched_at,
        verified_at=getattr(row, "verified_at", None),
    )


def coordinates(column):
    geometry = cast(column, Geometry(geometry_type="POINT", srid=4326))
    return func.ST_Y(geometry).label("lat"), func.ST_X(geometry).label("lng")


def nearby_statement(lat: float, lng: float, radius: int):
    point = cast(func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326), Geography(geometry_type="POINT", srid=4326))
    distance = cast(func.floor(func.ST_Distance(ParkingLot.location, point) + 0.5), Integer).label("distance_m")
    return (
        select(ParkingLot, *coordinates(ParkingLot.location), distance)
        .where(func.ST_DWithin(ParkingLot.location, point, radius))
        .order_by(ParkingLot.id)
    )


class ParkingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def nearby(self, lat: float, lng: float, radius: int) -> tuple[LotSnapshot, ...]:
        rows = (await self.session.execute(nearby_statement(lat, lng, radius))).all()
        return await self._snapshots(rows)

    async def detail(self, parking_id: int) -> LotSnapshot | None:
        row = (
            await self.session.execute(
                select(ParkingLot, *coordinates(ParkingLot.location)).where(ParkingLot.id == parking_id)
            )
        ).first()
        if row is None:
            return None
        return (await self._snapshots([(*row, 0)]))[0]

    async def _snapshots(self, lots) -> tuple[LotSnapshot, ...]:
        if not lots:
            return ()
        lot_ids = [row[0].id for row in lots]
        zones = (await self.session.scalars(select(ParkingZone).where(ParkingZone.parking_id.in_(lot_ids)))).all()
        zone_ids = [zone.id for zone in zones]
        rules = defaultdict(list)
        statement = select(ParkingRule, DataSource).join(DataSource, ParkingRule.source_id == DataSource.id)
        for row, source in (await self.session.execute(statement.where(ParkingRule.parking_id.in_(lot_ids)))).all():
            rules[row.parking_id].append(
                RuleFact(
                    rule_id=row.id,
                    parking_id=row.parking_id,
                    zone_id=row.zone_id,
                    rule_kind=str(row.rule_kind),
                    authority_priority=row.authority_priority,
                    provenance=source_evidence(row, source),
                    green_plate_allowed=row.green_plate_allowed,
                    white_plate_allowed=row.white_plate_allowed,
                    yellow_plate_allowed=row.yellow_plate_allowed,
                    red_plate_allowed=row.red_plate_allowed,
                    car_allowed=row.car_allowed,
                    active=row.active,
                    effective_from=row.effective_from,
                    effective_to=row.effective_to,
                    schedule=row.schedule,
                    confidence=row.confidence,
                )
            )
        rate_rows = (
            await self.session.execute(
                select(ParkingRate, DataSource)
                .join(DataSource, ParkingRate.source_id == DataSource.id)
                .where(ParkingRate.zone_id.in_(zone_ids))
            )
        ).all()
        rate_ids = [row.id for row, _ in rate_rows]
        rate_rules = defaultdict(list)
        for row in (
            await self.session.scalars(select(ParkingRateRule).where(ParkingRateRule.rate_id.in_(rate_ids)))
        ).all():
            rate_rules[row.rate_id].append(
                RateRuleFact(
                    rule_id=row.id,
                    day_type=str(row.day_type),
                    start_time=row.start_time,
                    end_time=row.end_time,
                    start_minute=row.start_minute,
                    end_minute=row.end_minute,
                    amount=row.amount,
                    unit_minutes=row.unit_minutes,
                    max_amount=row.max_amount,
                    schedule=row.schedule,
                )
            )
        supporting_sources = defaultdict(list)
        for row, source in (
            await self.session.execute(
                select(ParkingRateSource, DataSource)
                .join(DataSource, ParkingRateSource.source_id == DataSource.id)
                .where(ParkingRateSource.rate_id.in_(rate_ids))
            )
        ).all():
            supporting_sources[row.rate_id].append(source_evidence(row, source))
        rates = defaultdict(list)
        for row, source in rate_rows:
            rates[row.zone_id].append(
                RateFact(
                    rate_id=row.id,
                    zone_id=row.zone_id,
                    vehicle=str(row.vehicle_type) if row.vehicle_type is not None else None,
                    rate_type=str(row.rate_type) if row.rate_type is not None else None,
                    parse_status=str(row.parse_status),
                    provenance=source_evidence(row, source),
                    currency=row.currency,
                    base_amount=row.base_amount,
                    unit_minutes=row.unit_minutes,
                    free_minutes=row.free_minutes,
                    daily_max_amount=row.daily_max_amount,
                    raw_text=row.raw_text,
                    description=row.description,
                    effective_from=row.effective_from,
                    effective_to=row.effective_to,
                    schedule=row.schedule,
                    rules=tuple(sorted(rate_rules[row.id], key=lambda rule: rule.rule_id)),
                    supporting_sources=tuple(supporting_sources[row.id]),
                )
            )
        # Newest normalized observation per zone, NULL fetches last. DISTINCT ON
        # avoids loading the entire observation history; IDs only break realtime
        # timestamp ties and never choose permission/rate legality.
        realtime = {}
        statement = (
            select(ParkingRealtime, DataSource)
            .join(DataSource, ParkingRealtime.source_id == DataSource.id)
            .where(ParkingRealtime.zone_id.in_(zone_ids))
            .distinct(ParkingRealtime.zone_id)
            .order_by(
                ParkingRealtime.zone_id, ParkingRealtime.fetched_at.desc().nulls_last(), ParkingRealtime.id.desc()
            )
        )
        for row, source in (await self.session.execute(statement)).all():
            realtime[row.zone_id] = RealtimeFact(
                observation_id=row.id,
                zone_id=row.zone_id,
                status=str(row.status),
                available=row.available_spaces,
                total=row.total_spaces,
                provenance=source_evidence(row, source),
                freshness_seconds=source.freshness_seconds if source.freshness_seconds is not None else 120,
            )
        entrances = defaultdict(list)
        statement = (
            select(ParkingEntrance, DataSource, *coordinates(ParkingEntrance.location))
            .join(DataSource, ParkingEntrance.source_id == DataSource.id)
            .where(ParkingEntrance.parking_id.in_(lot_ids))
            .order_by(ParkingEntrance.id)
        )
        for row, source, lat, lng in (await self.session.execute(statement)).all():
            access = (
                "UNKNOWN"
                if row.heavy_motorcycle_access is None
                else ("ALLOWED" if row.heavy_motorcycle_access else "NOT_ALLOWED")
            )
            entrances[row.parking_id].append(
                EntranceFact(
                    entrance_id=row.id,
                    name=row.name,
                    lat=lat,
                    lng=lng,
                    access=access,
                    provenance=source_evidence(row, source),
                    entrance_type=str(row.entrance_type) if row.entrance_type is not None else None,
                    notes=row.notes,
                )
            )
        zone_snapshots = defaultdict(list)
        for row in sorted(zones, key=lambda zone: zone.id):
            zone_snapshots[row.parking_id].append(
                ZoneSnapshot(
                    facts=ZoneFacts(row.id, row.parking_id, str(row.space_type)),
                    name=row.name,
                    capacity=row.capacity,
                    rates=tuple(sorted(rates[row.id], key=lambda rate: rate.rate_id)),
                    realtime=realtime.get(row.id),
                )
            )
        return tuple(
            LotSnapshot(
                facts=ParkingFacts(row.id, tuple(rules[row.id])),
                name=row.name,
                lat=lat,
                lng=lng,
                distance_m=distance,
                zones=tuple(zone_snapshots[row.id]),
                entrances=tuple(entrances[row.id]),
            )
            for row, lat, lng, distance in lots
        )
