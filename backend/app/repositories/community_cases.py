"""Persistence for community verification cases. Callers pass identities explicitly."""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CommunityCase,
    CommunityCaseEvent,
    CommunityCaseRevision,
    CommunityCaseStance,
    CommunityEvidencePhoto,
    CommunityParticipant,
    CommunityPrecheck,
    CommunityPrecheckResult,
    ContributionLedgerEntry,
    IdempotencyRecord,
    ParkingLot,
    SourceVerification,
)
from app.models.enums import (
    CasePublicationState,
)


class CommunityCaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # --- participants ---------------------------------------------------------------------

    async def participant_for_user(self, user_id: int) -> CommunityParticipant:
        """The user's permanent participant row, created on first community action."""
        await self.session.execute(
            insert(CommunityParticipant)
            .values(user_id=user_id)
            .on_conflict_do_nothing(constraint="uq_community_participants_user_id")
        )
        return (
            await self.session.scalars(select(CommunityParticipant).where(CommunityParticipant.user_id == user_id))
        ).one()

    async def existing_participant(self, user_id: int) -> CommunityParticipant | None:
        return (
            await self.session.scalars(select(CommunityParticipant).where(CommunityParticipant.user_id == user_id))
        ).one_or_none()

    async def participant(self, participant_id: int, *, for_update: bool = False) -> CommunityParticipant | None:
        statement = select(CommunityParticipant).where(CommunityParticipant.id == participant_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.scalars(statement)).one_or_none()

    async def suspended(self, participant_ids: Iterable[int]) -> set[int]:
        ids = list(set(participant_ids))
        if not ids:
            return set()
        rows = await self.session.scalars(
            select(CommunityParticipant.id).where(
                CommunityParticipant.id.in_(ids), CommunityParticipant.suspended_at.is_not(None)
            )
        )
        return set(rows.all())

    # --- cases ----------------------------------------------------------------------------

    async def add(self, row):
        self.session.add(row)
        await self.session.flush()
        return row

    async def case(self, case_id: int, *, for_update: bool = False) -> CommunityCase | None:
        statement = select(CommunityCase).where(CommunityCase.id == case_id)
        if for_update:
            await self.lock_case_scopes([case_id])
            statement = statement.with_for_update()
        return (await self.session.scalars(statement)).one_or_none()

    async def lock_parking_scopes(self, parking_ids: Iterable[int]) -> None:
        """Serialize observation writes by lot, before locking or inserting cases.

        NO KEY UPDATE permits foreign-key KEY SHARE locks while excluding other
        observation writers. Multiple scopes always lock in parking ID order.
        """
        await self.session.execute(
            select(ParkingLot.id)
            .where(ParkingLot.id.in_(parking_ids))
            .order_by(ParkingLot.id)
            .with_for_update(key_share=True)
        )

    async def lock_case_scopes(self, case_ids: Iterable[int]) -> None:
        parking_ids = await self.session.scalars(
            select(CommunityCase.parking_id).where(CommunityCase.id.in_(case_ids)).distinct()
        )
        await self.lock_parking_scopes(parking_ids.all())

    async def revisions(self, case_id: int) -> list[CommunityCaseRevision]:
        return list(
            (
                await self.session.scalars(
                    select(CommunityCaseRevision)
                    .where(CommunityCaseRevision.case_id == case_id)
                    .order_by(CommunityCaseRevision.revision)
                )
            ).all()
        )

    async def revision(self, case_id: int, revision: int) -> CommunityCaseRevision:
        return (
            await self.session.scalars(
                select(CommunityCaseRevision).where(
                    CommunityCaseRevision.case_id == case_id, CommunityCaseRevision.revision == revision
                )
            )
        ).one()

    async def stances(self, case_id: int, *, active_only: bool = False) -> list[CommunityCaseStance]:
        statement = select(CommunityCaseStance).where(CommunityCaseStance.case_id == case_id)
        if active_only:
            statement = statement.where(
                CommunityCaseStance.withdrawn_at.is_(None), CommunityCaseStance.invalidated_at.is_(None)
            )
        return list((await self.session.scalars(statement.order_by(CommunityCaseStance.id))).all())

    async def active_stance(self, case_id: int, participant_id: int) -> CommunityCaseStance | None:
        return (
            await self.session.scalars(
                select(CommunityCaseStance).where(
                    CommunityCaseStance.case_id == case_id,
                    CommunityCaseStance.participant_id == participant_id,
                    CommunityCaseStance.withdrawn_at.is_(None),
                    CommunityCaseStance.invalidated_at.is_(None),
                )
            )
        ).one_or_none()

    async def photos(self, case_id: int) -> list[CommunityEvidencePhoto]:
        return list(
            (
                await self.session.scalars(
                    select(CommunityEvidencePhoto)
                    .where(CommunityEvidencePhoto.case_id == case_id)
                    .order_by(CommunityEvidencePhoto.id)
                )
            ).all()
        )

    async def photo(self, photo_id: int, *, for_update: bool = False) -> CommunityEvidencePhoto | None:
        statement = select(CommunityEvidencePhoto).where(CommunityEvidencePhoto.id == photo_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.scalars(statement)).one_or_none()

    async def events(self, case_id: int) -> list[CommunityCaseEvent]:
        return list(
            (
                await self.session.scalars(
                    select(CommunityCaseEvent)
                    .where(CommunityCaseEvent.case_id == case_id)
                    .order_by(CommunityCaseEvent.id)
                )
            ).all()
        )

    async def latest_precheck(self, case_id: int) -> tuple[CommunityPrecheck, list[CommunityPrecheckResult]] | None:
        run = (
            await self.session.scalars(
                select(CommunityPrecheck)
                .where(CommunityPrecheck.case_id == case_id)
                .order_by(CommunityPrecheck.id.desc())
                .limit(1)
            )
        ).one_or_none()
        if run is None:
            return None
        results = await self.session.scalars(
            select(CommunityPrecheckResult)
            .where(CommunityPrecheckResult.precheck_id == run.id)
            .order_by(CommunityPrecheckResult.id)
        )
        return run, list(results.all())

    async def cases_by_author(self, participant_id: int, limit: int, before_id: int | None) -> list[CommunityCase]:
        statement = select(CommunityCase).where(CommunityCase.author_participant_id == participant_id)
        if before_id is not None:
            statement = statement.where(CommunityCase.id < before_id)
        return list((await self.session.scalars(statement.order_by(CommunityCase.id.desc()).limit(limit + 1))).all())

    async def participant_case_ids(self, participant_id: int) -> set[int]:
        """Cases the participant authored, took a stance on or uploaded evidence to."""
        rows = await self.session.execute(
            select(CommunityCase.id)
            .where(CommunityCase.author_participant_id == participant_id)
            .union(
                select(CommunityCaseStance.case_id).where(CommunityCaseStance.participant_id == participant_id),
                select(CommunityEvidencePhoto.case_id).where(CommunityEvidencePhoto.participant_id == participant_id),
            )
        )
        return set(rows.scalars().all())

    async def is_participant(self, case_id: int, participant_id: int) -> bool:
        return case_id in await self.participant_case_ids(participant_id)

    async def cases_to_recount_for(self, participant_id: int) -> list[int]:
        """Open or live cases the participant authored or holds an active stance on.

        Closed (rejected, superseded or withdrawn) cases are skipped. IDs are returned
        ascending so concurrent recounts lock cases in the same order.
        """
        stance_cases = select(CommunityCaseStance.case_id).where(
            CommunityCaseStance.participant_id == participant_id,
            CommunityCaseStance.withdrawn_at.is_(None),
            CommunityCaseStance.invalidated_at.is_(None),
        )
        rows = await self.session.scalars(
            select(CommunityCase.id)
            .where(
                or_(CommunityCase.author_participant_id == participant_id, CommunityCase.id.in_(stance_cases)),
                CommunityCase.review_status.not_in(("REJECTED", "SUPERSEDED")),
                CommunityCase.publication_state != CasePublicationState.WITHDRAWN,
            )
            .order_by(CommunityCase.id)
        )
        return list(rows.all())

    async def published_for_parking(self, parking_id: int, now: datetime) -> list[CommunityCase]:
        return list(
            (
                await self.session.scalars(
                    select(CommunityCase)
                    .where(
                        CommunityCase.parking_id == parking_id,
                        CommunityCase.publication_state == CasePublicationState.PUBLISHED,
                        or_(CommunityCase.published_until.is_(None), CommunityCase.published_until > now),
                    )
                    .order_by(CommunityCase.first_published_at.desc(), CommunityCase.id.desc())
                )
            ).all()
        )

    async def queue(
        self,
        review_statuses: list[str],
        fact_type: str | None,
        city: str | None,
        parking_id: int | None,
        limit: int,
        offset: int,
    ) -> list[CommunityCase]:
        """Published disputes first, then permission/access, then others; oldest waiting first."""
        statement = select(CommunityCase).join(ParkingLot, ParkingLot.id == CommunityCase.parking_id)
        statement = statement.where(CommunityCase.review_status.in_(review_statuses))
        if fact_type is not None:
            statement = statement.where(CommunityCase.fact_type == fact_type)
        if city is not None:
            statement = statement.where(ParkingLot.city == city)
        if parking_id is not None:
            statement = statement.where(CommunityCase.parking_id == parking_id)
        priority = case(
            (CommunityCase.publication_state == CasePublicationState.SUSPENDED, 0),
            (CommunityCase.fact_type.in_(("PARKING_PERMISSION", "ENTRANCE_ACCESS")), 1),
            else_=2,
        )
        statement = statement.order_by(priority, CommunityCase.updated_at, CommunityCase.id)
        return list((await self.session.scalars(statement.offset(offset).limit(limit + 1))).all())

    # --- ledger ---------------------------------------------------------------------------

    async def ledger_for_case(self, case_id: int) -> list[ContributionLedgerEntry]:
        return list(
            (
                await self.session.scalars(
                    select(ContributionLedgerEntry)
                    .where(ContributionLedgerEntry.case_id == case_id)
                    .order_by(ContributionLedgerEntry.id)
                )
            ).all()
        )

    async def ledger_for_participant(self, participant_id: int) -> list[ContributionLedgerEntry]:
        return list(
            (
                await self.session.scalars(
                    select(ContributionLedgerEntry)
                    .where(ContributionLedgerEntry.participant_id == participant_id)
                    .order_by(ContributionLedgerEntry.id)
                )
            ).all()
        )

    # --- source verification --------------------------------------------------------------

    async def source_verification(self, verification_id: int, *, for_update: bool = False):
        statement = select(SourceVerification).where(SourceVerification.id == verification_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.scalars(statement)).one_or_none()

    # --- idempotency ----------------------------------------------------------------------

    async def idempotency_record(self, user_id: int, operation: str, key: str) -> IdempotencyRecord | None:
        return (
            await self.session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.user_id == user_id,
                    IdempotencyRecord.operation == operation,
                    IdempotencyRecord.idempotency_key == key,
                )
            )
        ).one_or_none()

    async def photo_counts_by_owner(self, case_id: int) -> dict[tuple[str, int], int]:
        rows = await self.session.execute(
            select(CommunityEvidencePhoto.revision_id, CommunityEvidencePhoto.stance_id, func.count())
            .where(CommunityEvidencePhoto.case_id == case_id, CommunityEvidencePhoto.deleted_at.is_(None))
            .group_by(CommunityEvidencePhoto.revision_id, CommunityEvidencePhoto.stance_id)
        )
        counts = {}
        for revision_id, stance_id, count in rows.all():
            counts[("revision", revision_id) if revision_id is not None else ("stance", stance_id)] = count
        return counts
