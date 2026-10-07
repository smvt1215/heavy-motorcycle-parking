"""Profile, favorites and community reports.

Community reports are evidence only: nothing here writes parking rules, rates,
realtime or entrances, so a report can never silently overwrite official data.
Moderation changes only the report's own status.
"""

import contextlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import AuthenticatedUser
from app.domain.errors import DiscoveryError, forbidden, not_found, unauthenticated
from app.models import ReportPhoto, ReportStatus, ReportType, UserReport, VehicleType
from app.repositories.community import ReportRepository, UserRepository
from app.services.photos import process_photo_async
from app.services.storage import ObjectStorage, storage_unavailable

MAX_PHOTOS_PER_REPORT = 3
RESOLVED_STATUSES = {ReportStatus.VERIFIED, ReportStatus.REJECTED, ReportStatus.SUPERSEDED}


def parking_not_found() -> DiscoveryError:
    return not_found("PARKING_NOT_FOUND", "The parking lot was not found.")


def report_not_found() -> DiscoveryError:
    return not_found("REPORT_NOT_FOUND", "The report was not found.")


@dataclass(frozen=True)
class ReportView:
    report: UserReport
    photo_count: int


@dataclass(frozen=True)
class ReportPage:
    views: list[ReportView]
    next_before: int | None


class ProfileService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.users = UserRepository(session)

    async def me(self, principal: AuthenticatedUser):
        user = await self.users.get(principal.id)
        if user is None:
            raise unauthenticated()
        return user

    async def set_vehicle(self, principal: AuthenticatedUser, vehicle: VehicleType | None):
        user = await self.me(principal)
        user.preferred_vehicle = vehicle
        await self.session.commit()
        return user

    async def favorites(self, principal: AuthenticatedUser):
        return await self.users.favorites(principal.id)

    async def add_favorite(self, principal: AuthenticatedUser, parking_id: int) -> bool:
        if not await self.users.lot_exists(parking_id):
            raise parking_not_found()
        created = await self.users.add_favorite(principal.id, parking_id)
        await self.session.commit()
        return created

    async def remove_favorite(self, principal: AuthenticatedUser, parking_id: int) -> None:
        removed = await self.users.remove_favorite(principal.id, parking_id)
        await self.session.commit()
        if not removed:
            raise not_found("FAVORITE_NOT_FOUND", "The parking lot is not in your favorites.")


class ReportService:
    def __init__(self, session: AsyncSession, storage: ObjectStorage | None, max_photo_bytes: int):
        self.session = session
        self.reports = ReportRepository(session)
        self.users = UserRepository(session)
        self.storage = storage
        self.max_photo_bytes = max_photo_bytes

    async def create(
        self,
        principal: AuthenticatedUser,
        parking_id: int,
        zone_id: int | None,
        report_type: ReportType,
        description: str | None,
    ) -> ReportView:
        if not await self.users.lot_exists(parking_id):
            raise parking_not_found()
        if zone_id is not None and not await self.reports.zone_in_lot(zone_id, parking_id):
            raise DiscoveryError("ZONE_NOT_IN_PARKING", "The zone does not belong to this parking lot.", 422)
        report = await self.reports.add(
            UserReport(
                user_id=principal.id,
                parking_id=parking_id,
                zone_id=zone_id,
                report_type=report_type,
                status=ReportStatus.PENDING,
                description=description,
            )
        )
        await self.session.commit()
        return ReportView(report, 0)

    async def for_parking(
        self, parking_id: int, limit: int, before_id: int | None, status: ReportStatus | None
    ) -> ReportPage:
        if not await self.users.lot_exists(parking_id):
            raise parking_not_found()
        return await self._page(await self.reports.for_parking(parking_id, limit, before_id, status), limit)

    async def mine(self, principal: AuthenticatedUser, limit: int, before_id: int | None) -> ReportPage:
        return await self._page(await self.reports.for_user(principal.id, limit, before_id), limit)

    async def _page(self, rows: list[UserReport], limit: int) -> ReportPage:
        page = rows[:limit]
        next_before = page[-1].id if len(rows) > limit else None
        return ReportPage(await self._views(page), next_before)

    async def add_photo(self, principal: AuthenticatedUser, report_id: int, data: bytes) -> ReportPhoto:
        report = await self.reports.get(report_id, for_update=True)
        if report is None:
            raise report_not_found()
        if report.user_id != principal.id:
            raise forbidden()
        if report.status != ReportStatus.PENDING:
            raise DiscoveryError("REPORT_CLOSED", "Photos can only be added to pending reports.", 409)
        counts = await self.reports.photo_counts([report.id])
        if counts.get(report.id, 0) >= MAX_PHOTOS_PER_REPORT:
            raise DiscoveryError(
                "PHOTO_LIMIT_REACHED", f"A report can have at most {MAX_PHOTOS_PER_REPORT} photos.", 409
            )
        if self.storage is None:
            raise storage_unavailable()
        photo = await process_photo_async(data, self.max_photo_bytes)
        key = f"reports/{report.id}/{uuid.uuid4().hex}.jpg"
        await self.storage.put(key, photo.body, photo.content_type)
        try:
            saved = await self.reports.add_photo(
                ReportPhoto(
                    report_id=report.id,
                    storage_key=key,
                    content_type=photo.content_type,
                    byte_size=len(photo.body),
                    width=photo.width,
                    height=photo.height,
                )
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            # Best effort: an orphaned object carries no metadata and no report link.
            with contextlib.suppress(Exception):
                await self.storage.delete(key)
            raise
        return saved

    async def set_status(
        self, principal: AuthenticatedUser, report_id: int, status: ReportStatus, now: datetime
    ) -> ReportView:
        if not principal.is_moderator:
            raise forbidden()
        report = await self.reports.get(report_id, for_update=True)
        if report is None:
            raise report_not_found()
        if status not in RESOLVED_STATUSES:
            raise DiscoveryError("INVALID_STATUS_TRANSITION", "Reports can only be resolved.", 422)
        # VERIFIED may later be SUPERSEDED by newer evidence; nothing reopens a report.
        superseding = report.status == ReportStatus.VERIFIED and status == ReportStatus.SUPERSEDED
        if report.status not in (ReportStatus.PENDING, status) and not superseding:
            raise DiscoveryError("INVALID_STATUS_TRANSITION", "This report has already been resolved.", 409)
        if report.status != status:
            report.status = status
            report.resolved_at = max(now, report.created_at)
            report.resolved_by_user_id = principal.id
        await self.session.commit()
        await self.session.refresh(report)
        counts = await self.reports.photo_counts([report.id])
        return ReportView(report, counts.get(report.id, 0))

    async def _views(self, reports: list[UserReport]) -> list[ReportView]:
        counts = await self.reports.photo_counts(r.id for r in reports)
        return [ReportView(r, counts.get(r.id, 0)) for r in reports]
