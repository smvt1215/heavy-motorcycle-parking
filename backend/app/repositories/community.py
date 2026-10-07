"""User-scoped and community persistence. Every query takes the owner explicitly."""

from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Favorite, ParkingLot, ParkingZone, ReportPhoto, User, UserReport
from app.repositories.parking import coordinates


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def lot_exists(self, parking_id: int) -> bool:
        return (await self.session.scalar(select(ParkingLot.id).where(ParkingLot.id == parking_id))) is not None

    async def favorites(self, user_id: int):
        lat, lng = coordinates(ParkingLot.location)
        return (
            await self.session.execute(
                select(Favorite.parking_id, Favorite.created_at, ParkingLot.name, lat, lng)
                .join(ParkingLot, ParkingLot.id == Favorite.parking_id)
                .where(Favorite.user_id == user_id)
                .order_by(Favorite.created_at.desc(), Favorite.parking_id)
            )
        ).all()

    async def add_favorite(self, user_id: int, parking_id: int) -> bool:
        """True when created; False when it already existed (unique per user/lot)."""
        statement = (
            insert(Favorite)
            .values(user_id=user_id, parking_id=parking_id)
            .on_conflict_do_nothing(constraint="uq_favorites_user_id_parking_id")
            .returning(Favorite.id)
        )
        return (await self.session.execute(statement)).scalar_one_or_none() is not None

    async def remove_favorite(self, user_id: int, parking_id: int) -> bool:
        result = await self.session.execute(
            delete(Favorite).where(Favorite.user_id == user_id, Favorite.parking_id == parking_id)
        )
        return result.rowcount > 0


class ReportRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def zone_in_lot(self, zone_id: int, parking_id: int) -> bool:
        return (
            await self.session.scalar(
                select(ParkingZone.id).where(ParkingZone.id == zone_id, ParkingZone.parking_id == parking_id)
            )
        ) is not None

    async def add(self, report: UserReport) -> UserReport:
        self.session.add(report)
        await self.session.flush()
        await self.session.refresh(report)
        return report

    async def get(self, report_id: int, *, for_update: bool = False) -> UserReport | None:
        statement = select(UserReport).where(UserReport.id == report_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.scalars(statement)).one_or_none()

    async def for_parking(self, parking_id: int, limit: int) -> list[UserReport]:
        return list(
            (
                await self.session.scalars(
                    select(UserReport)
                    .where(UserReport.parking_id == parking_id)
                    .order_by(UserReport.created_at.desc(), UserReport.id.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def for_user(self, user_id: int, limit: int) -> list[UserReport]:
        return list(
            (
                await self.session.scalars(
                    select(UserReport)
                    .where(UserReport.user_id == user_id)
                    .order_by(UserReport.created_at.desc(), UserReport.id.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def photo_counts(self, report_ids: Iterable[int]) -> dict[int, int]:
        ids = list(report_ids)
        if not ids:
            return {}
        rows = await self.session.execute(
            select(ReportPhoto.report_id, func.count())
            .where(ReportPhoto.report_id.in_(ids))
            .group_by(ReportPhoto.report_id)
        )
        return dict(rows.all())

    async def add_photo(self, photo: ReportPhoto) -> ReportPhoto:
        self.session.add(photo)
        await self.session.flush()
        await self.session.refresh(photo)
        return photo
