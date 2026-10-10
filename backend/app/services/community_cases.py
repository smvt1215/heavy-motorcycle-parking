"""Community verification cases: submission, corroboration, publication and review.

Contract: docs/community-verification-contract.md. Cases are community evidence
only; nothing here writes parking rules, rates, realtime, entrances or facilities.
Every write locks the case row, and vote counting, publication, version and
points change in the caller's single transaction (the router commits).
"""

import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import AuthenticatedUser
from app.domain.community import (
    CORROBORATION_THRESHOLD,
    PRECHECK_RULE_VERSION,
    TIME_PARSER_VERSION,
    CaseInput,
    Decision,
    EvidencePhoto,
    LedgerEntry,
    PrecheckReport,
    StanceInput,
    classify_photo_time,
    contribution_level,
    counts_at_publication,
    derive_publication_status,
    effective_points,
    evaluate_case,
    normalize_value,
    observation_label,
    publication_term_end,
)
from app.domain.errors import DiscoveryError, forbidden, not_found
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
    DataSource,
    ParkingEntrance,
    SourceVerification,
    StorageDeletion,
)
from app.models.enums import (
    LOW_RISK_FACT_TYPES,
    CaseEventType,
    CasePublicationState,
    CaseReviewStatus,
    CaseStance,
    CommunityFactType,
    ContributionEntryType,
    ContributionReason,
    PhotoTimeStatus,
    PublicationBasis,
    PublicationStatus,
)
from app.repositories.community import ReportRepository, UserRepository
from app.repositories.community_cases import CommunityCaseRepository
from app.services.photos import process_photo_async
from app.services.storage import ObjectStorage, storage_unavailable

MAX_PHOTOS_PER_OWNER = 3
OPEN_REVIEW = (
    CaseReviewStatus.PRECHECK_PENDING,
    CaseReviewStatus.AWAITING_CORROBORATION,
    CaseReviewStatus.MANUAL_REVIEW,
    CaseReviewStatus.NEEDS_EVIDENCE,
)
CLOSED_REVIEW = (CaseReviewStatus.REJECTED, CaseReviewStatus.SUPERSEDED)


def case_not_found() -> DiscoveryError:
    return not_found("CASE_NOT_FOUND", "The case was not found.")


def conflict(code: str, message: str) -> DiscoveryError:
    return DiscoveryError(code, message, 409)


def invalid(code: str, message: str) -> DiscoveryError:
    return DiscoveryError(code, message, 422)


@dataclass(frozen=True)
class CaseView:
    case: CommunityCase
    revision: CommunityCaseRevision
    publication_status: PublicationStatus
    supporters: int


@dataclass(frozen=True)
class PhotoView:
    photo: CommunityEvidencePhoto
    counts_toward_corroboration: bool
    message_code: str


@dataclass(frozen=True)
class CaseDetail:
    view: CaseView
    revisions: list[CommunityCaseRevision]
    photos: list[PhotoView]
    events: list[CommunityCaseEvent]
    my_stance: CommunityCaseStance | None
    stances: list[CommunityCaseStance] = field(default_factory=list)
    precheck: tuple[CommunityPrecheck, list[CommunityPrecheckResult]] | None = None
    viewer_participant_id: int | None = None
    recused: bool = False
    preview: dict | None = None


@dataclass(frozen=True)
class Observation:
    case: CommunityCase
    revision: CommunityCaseRevision
    corroborators: int | None
    label: str | None


@dataclass(frozen=True)
class Contributions:
    points: int
    level: str
    entries: list[ContributionLedgerEntry]


class CommunityCaseService:
    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorage | None,
        max_photo_bytes: int,
        auto_publish_enabled: bool,
    ):
        self.session = session
        self.repo = CommunityCaseRepository(session)
        self.users = UserRepository(session)
        self.reports = ReportRepository(session)
        self.storage = storage
        self.max_photo_bytes = max_photo_bytes
        self.auto_publish_enabled = auto_publish_enabled

    # --- identity -------------------------------------------------------------------------

    async def _participant(self, principal: AuthenticatedUser) -> CommunityParticipant:
        return await self.repo.participant_for_user(principal.id)

    async def _active_participant(self, principal: AuthenticatedUser) -> CommunityParticipant:
        participant = await self._participant(principal)
        if participant.suspended_at is not None:
            raise DiscoveryError("PARTICIPANT_SUSPENDED", "Your community participation is suspended.", 403)
        return participant

    async def _locked_case(self, case_id: int) -> CommunityCase:
        found = await self.repo.case(case_id, for_update=True)
        if found is None:
            raise case_not_found()
        return found

    # --- submission -------------------------------------------------------------------------

    async def create(
        self,
        principal: AuthenticatedUser,
        now: datetime,
        *,
        parking_id: int,
        zone_id: int | None,
        fact_type: CommunityFactType,
        vehicle: str | None,
        proposed_value: dict,
        description: str | None,
        source_url: str | None,
    ) -> CaseView:
        participant = await self._active_participant(principal)
        if not await self.users.lot_exists(parking_id):
            raise not_found("PARKING_NOT_FOUND", "The parking lot was not found.")
        if zone_id is not None and not await self.reports.zone_in_lot(zone_id, parking_id):
            raise invalid("ZONE_NOT_IN_PARKING", "The zone does not belong to this parking lot.")
        low_risk = fact_type in LOW_RISK_FACT_TYPES
        if low_risk and vehicle is not None:
            raise invalid("SCOPE_INVALID", "Facility and entrance-location observations are vehicle-independent.")
        if not low_risk and (vehicle is None or zone_id is None):
            raise invalid("SCOPE_INVALID", "Permission, rate and entrance-access reports need a vehicle and zone.")
        value = await self._value(fact_type, proposed_value, parking_id)
        row = await self.repo.add(
            CommunityCase(
                parking_id=parking_id,
                zone_id=zone_id,
                fact_type=fact_type,
                vehicle=vehicle,
                author_participant_id=participant.id,
                review_status=CaseReviewStatus.PRECHECK_PENDING,
                publication_state=CasePublicationState.UNPUBLISHED,
                current_revision=1,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        revision = await self.repo.add(
            CommunityCaseRevision(
                case_id=row.id,
                revision=1,
                proposed_value=value,
                description=description,
                source_url=source_url,
                created_by_participant_id=participant.id,
                created_at=now,
            )
        )
        await self._event(row, now, CaseEventType.SUBMITTED, participant.id, revision=1)
        report = await self._precheck(row, now)
        return self._view(row, revision, now, report)

    async def revise(
        self,
        principal: AuthenticatedUser,
        now: datetime,
        case_id: int,
        *,
        proposed_value: dict,
        description: str | None,
        source_url: str | None,
    ) -> CaseView:
        participant = await self._active_participant(principal)
        row = await self._locked_case(case_id)
        if row.author_participant_id != participant.id:
            raise forbidden()
        if row.review_status != CaseReviewStatus.NEEDS_EVIDENCE:
            raise conflict("CASE_NOT_AWAITING_EVIDENCE", "Only cases awaiting evidence can be revised.")
        value = await self._value(row.fact_type, proposed_value, row.parking_id)
        row.current_revision += 1
        revision = await self.repo.add(
            CommunityCaseRevision(
                case_id=row.id,
                revision=row.current_revision,
                proposed_value=value,
                description=description,
                source_url=source_url,
                created_by_participant_id=participant.id,
                created_at=now,
            )
        )
        row.review_status = CaseReviewStatus.PRECHECK_PENDING
        await self._event(row, now, CaseEventType.REVISION_SUBMITTED, participant.id, revision=row.current_revision)
        report = await self._precheck(row, now)
        return self._view(row, revision, now, report)

    async def _value(self, fact_type: str, value, parking_id: int) -> dict:
        try:
            normalized = normalize_value(fact_type, value)
        except ValueError as exc:
            raise invalid("VALUE_INVALID", str(exc)) from exc
        entrance_id = normalized.get("entrance_id")
        if entrance_id is not None:
            entrance = await self.session.get(ParkingEntrance, entrance_id)
            if entrance is None or entrance.parking_id != parking_id:
                raise invalid("VALUE_INVALID", "entrance_id does not belong to this parking lot")
        return normalized

    # --- evidence ---------------------------------------------------------------------------

    async def add_photo(
        self, principal: AuthenticatedUser, now: datetime, case_id: int, owner: str, data: bytes
    ) -> PhotoView:
        participant = await self._active_participant(principal)
        row = await self._locked_case(case_id)
        self._require_open(row, now)
        if owner == "revision":
            if row.author_participant_id != participant.id:
                raise forbidden()
            revision = await self.repo.revision(row.id, row.current_revision)
            owner_key, values = ("revision", revision.id), {"revision_id": revision.id}
        else:
            stance = await self.repo.active_stance(row.id, participant.id)
            if stance is None:
                raise conflict("STANCE_REQUIRED", "Record your stance before adding evidence to it.")
            if stance.stance == CaseStance.CANNOT_CONFIRM:
                raise conflict("STANCE_HAS_NO_EVIDENCE", "CANNOT_CONFIRM stances do not take evidence.")
            owner_key, values = ("stance", stance.id), {"stance_id": stance.id}
        counts = await self.repo.photo_counts_by_owner(row.id)
        if counts.get(owner_key, 0) >= MAX_PHOTOS_PER_OWNER:
            raise conflict("PHOTO_LIMIT_REACHED", f"At most {MAX_PHOTOS_PER_OWNER} photos per submission.")
        if self.storage is None:
            raise storage_unavailable()
        processed = await process_photo_async(data, self.max_photo_bytes)
        exif = processed.exif_time
        # Classify the full raw values; only storage applies the column length limits.
        time = classify_photo_time(
            exif.raw_datetime_original, exif.raw_offset_time_original, exif.raw_subsec_time_original, now
        )
        duplicate = any(p.normalized_sha256 == processed.sha256 for p in await self.repo.photos(row.id))
        key = f"community/{row.id}/{uuid.uuid4().hex}.jpg"
        await self.storage.put(key, processed.body, processed.content_type)
        try:
            photo = await self.repo.add(
                CommunityEvidencePhoto(
                    case_id=row.id,
                    participant_id=participant.id,
                    storage_key=key,
                    content_type=processed.content_type,
                    byte_size=len(processed.body),
                    width=processed.width,
                    height=processed.height,
                    normalized_sha256=processed.sha256,
                    received_at=now,
                    exif_datetime_original=exif.datetime_original,
                    exif_offset_time_original=exif.offset_time_original,
                    exif_subsec_time_original=exif.subsec_time_original,
                    captured_at=time.captured_at,
                    time_status=time.status,
                    time_parser_version=TIME_PARSER_VERSION,
                    created_at=now,
                    **values,
                )
            )
            await self._precheck(row, now)
        except Exception:
            with contextlib.suppress(Exception):
                await self.storage.delete(key)
            raise
        eligible = time.status == PhotoTimeStatus.VALID and not duplicate
        message = "ELIGIBLE" if eligible else "DUPLICATE_EVIDENCE" if duplicate else "MANUAL_REVIEW_REQUIRED"
        return PhotoView(photo, eligible, message)

    async def set_stance(
        self, principal: AuthenticatedUser, now: datetime, case_id: int, stance: CaseStance, observed_value
    ) -> CommunityCaseStance:
        participant = await self._active_participant(principal)
        row = await self._locked_case(case_id)
        if row.author_participant_id == participant.id:
            raise forbidden()
        self._require_open(row, now)
        value = None
        if stance != CaseStance.CANNOT_CONFIRM:
            if observed_value is None:
                raise invalid("VALUE_INVALID", "SUPPORT and OPPOSE need an observed value.")
            value = await self._value(row.fact_type, observed_value, row.parking_id)
            proposed = (await self.repo.revision(row.id, row.current_revision)).proposed_value
            if stance == CaseStance.SUPPORT and value != proposed:
                # Support for a different value is an objection, not corroboration.
                raise invalid("SUPPORT_VALUE_MISMATCH", "SUPPORT must report the proposed value; use OPPOSE.")
        current = await self.repo.active_stance(row.id, participant.id)
        if current is not None:
            current.withdrawn_at = now
            await self.session.flush()
            await self._event(row, now, CaseEventType.STANCE_WITHDRAWN, participant.id)
        created = await self.repo.add(
            CommunityCaseStance(
                case_id=row.id, participant_id=participant.id, stance=stance, observed_value=value, created_at=now
            )
        )
        await self._event(row, now, CaseEventType.STANCE_RECORDED, participant.id, payload={"stance": stance})
        await self._precheck(row, now)
        return created

    async def withdraw_stance(self, principal: AuthenticatedUser, now: datetime, case_id: int) -> None:
        participant = await self._participant(principal)
        row = await self._locked_case(case_id)
        current = await self.repo.active_stance(row.id, participant.id)
        if current is None:
            raise not_found("STANCE_NOT_FOUND", "You have no active stance on this case.")
        current.withdrawn_at = now
        await self.session.flush()
        await self._event(row, now, CaseEventType.STANCE_WITHDRAWN, participant.id)
        await self._precheck(row, now)

    def _require_open(self, row: CommunityCase, now: datetime) -> None:
        if row.review_status in CLOSED_REVIEW or row.publication_state == CasePublicationState.WITHDRAWN:
            raise conflict("CASE_CLOSED", "This case is closed.")
        if derive_publication_status(row.publication_state, row.published_until, now) == PublicationStatus.EXPIRED:
            raise conflict("CASE_EXPIRED", "This observation expired; start a new case for a new round.")

    # --- reading ------------------------------------------------------------------------------

    async def my_cases(self, principal: AuthenticatedUser, now: datetime, limit: int, before_id: int | None):
        participant = await self.repo.existing_participant(principal.id)
        if participant is None:
            return [], None
        rows = await self.repo.cases_by_author(participant.id, limit, before_id)
        page = rows[:limit]
        views = [await self._stored_view(row, now) for row in page]
        return views, (page[-1].id if len(rows) > limit else None)

    async def case_for_participant(self, principal: AuthenticatedUser, now: datetime, case_id: int) -> CaseDetail:
        participant = await self.repo.existing_participant(principal.id)
        row = await self.repo.case(case_id)
        if row is None:
            raise case_not_found()
        if participant is None or not await self.repo.is_participant(row.id, participant.id):
            raise forbidden()
        photos = [photo for photo in await self.repo.photos(row.id) if photo.participant_id == participant.id]
        return CaseDetail(
            view=await self._stored_view(row, now),
            revisions=await self.repo.revisions(row.id),
            photos=[self._photo_view(photo, now, row) for photo in photos],
            events=await self.repo.events(row.id),
            my_stance=await self.repo.active_stance(row.id, participant.id),
            viewer_participant_id=participant.id,
        )

    async def contributions(self, principal: AuthenticatedUser) -> Contributions:
        participant = await self.repo.existing_participant(principal.id)
        if participant is None:
            return Contributions(0, contribution_level(0), [])
        entries = await self.repo.ledger_for_participant(participant.id)
        points = effective_points(LedgerEntry(e.case_id, e.entry_type, e.points) for e in entries)
        return Contributions(points, contribution_level(points), entries)

    async def public_observations(self, parking_id: int, now: datetime) -> list[Observation]:
        if not await self.users.lot_exists(parking_id):
            raise not_found("PARKING_NOT_FOUND", "The parking lot was not found.")
        observations = []
        for row in await self.repo.published_for_parking(parking_id, now):
            revision = await self.repo.revision(row.id, row.current_revision)
            corroborators = None
            if row.publication_basis == PublicationBasis.COMMUNITY_CORROBORATED:
                corroborators = await self._supporters_from_latest(row.id)
            observations.append(
                Observation(row, revision, corroborators, observation_label(row.fact_type, row.publication_basis))
            )
        return observations

    async def photo_content(self, principal: AuthenticatedUser, photo_id: int) -> tuple[bytes, str]:
        photo = await self.repo.photo(photo_id)
        if photo is None or photo.deleted_at is not None or photo.storage_key is None:
            raise not_found("PHOTO_NOT_FOUND", "The photo was not found.")
        if not principal.is_moderator:
            participant = await self.repo.existing_participant(principal.id)
            if participant is None or participant.id != photo.participant_id:
                raise forbidden()
        if self.storage is None:
            raise storage_unavailable()
        return await self.storage.get(photo.storage_key), photo.content_type or "image/jpeg"

    # --- precheck, publication and points ---------------------------------------------------

    async def _evaluate(self, row: CommunityCase, now: datetime) -> PrecheckReport:
        revision = await self.repo.revision(row.id, row.current_revision)
        photos = await self.repo.photos(row.id)
        stances = await self.repo.stances(row.id, active_only=True)
        suspended = await self.repo.suspended([row.author_participant_id, *(s.participant_id for s in stances)])

        def evidence(items):
            return tuple(
                EvidencePhoto(p.id, p.normalized_sha256, p.time_status, p.captured_at, p.deleted_at is not None)
                for p in items
            )

        return evaluate_case(
            CaseInput(
                low_risk=row.fact_type in LOW_RISK_FACT_TYPES,
                author_participant_id=row.author_participant_id,
                author_suspended=row.author_participant_id in suspended,
                proposed_value=revision.proposed_value,
                original_photos=evidence(p for p in photos if p.revision_id == revision.id),
                stances=tuple(
                    StanceInput(
                        s.id,
                        s.participant_id,
                        s.stance,
                        s.observed_value,
                        evidence(p for p in photos if p.stance_id == s.id),
                        s.participant_id in suspended,
                    )
                    for s in stances
                ),
                publication_state=row.publication_state,
                publication_basis=row.publication_basis,
                published_until=row.published_until,
                auto_publish_enabled=self.auto_publish_enabled,
            ),
            now,
        )

    async def _precheck(self, row: CommunityCase, now: datetime) -> PrecheckReport:
        report = await self._evaluate(row, now)
        run = await self.repo.add(
            CommunityPrecheck(
                case_id=row.id,
                revision=row.current_revision,
                rule_version=PRECHECK_RULE_VERSION,
                outcome=report.outcome,
                evaluated_at=now,
            )
        )
        for result in report.results:
            self.session.add(
                CommunityPrecheckResult(
                    precheck_id=run.id,
                    check_code=result.code,
                    outcome=result.outcome,
                    reason_code=result.reason_code,
                    detail=result.detail,
                )
            )
        await self.session.flush()
        await self._event(row, now, CaseEventType.PRECHECK_COMPLETED, None, payload={"outcome": report.outcome})
        if row.review_status in OPEN_REVIEW and row.review_status != CaseReviewStatus.NEEDS_EVIDENCE:
            if report.decision == Decision.PUBLISH:
                await self._publish(row, now, PublicationBasis.COMMUNITY_CORROBORATED, report)
            elif report.decision == Decision.MANUAL:
                row.review_status = CaseReviewStatus.MANUAL_REVIEW
            elif report.decision == Decision.AWAIT:
                row.review_status = CaseReviewStatus.AWAITING_CORROBORATION
        if report.decision == Decision.SUSPEND:
            row.publication_state = CasePublicationState.SUSPENDED
            row.review_status = CaseReviewStatus.MANUAL_REVIEW
            await self._freeze(row, now)
            await self._event(row, now, CaseEventType.SUSPENDED, None, payload={"supporters": len(report.supporters)})
        elif (
            report.decision == Decision.KEEP
            and derive_publication_status(row.publication_state, row.published_until, now)
            == PublicationStatus.PUBLISHED
        ):
            # Corroboration added while the observation is live earns points, still at most once.
            for participant_id in report.supporters:
                await self._award(row, participant_id, ContributionReason.CORROBORATION, now)
        row.version += 1
        row.updated_at = now
        await self.session.flush()
        return report

    async def _publish(self, row: CommunityCase, now: datetime, basis: PublicationBasis, report: PrecheckReport):
        row.review_status = CaseReviewStatus.ACCEPTED
        row.publication_state = CasePublicationState.PUBLISHED
        row.publication_basis = basis
        if row.first_published_at is None:
            row.first_published_at = now
            row.published_until = publication_term_end(now)
        await self._event(
            row, now, CaseEventType.PUBLISHED, None, payload={"basis": basis, "supporters": len(report.supporters)}
        )
        await self._award(row, row.author_participant_id, ContributionReason.ORIGINAL_REPORT, now)
        for participant_id in report.supporters:
            await self._award(row, participant_id, ContributionReason.CORROBORATION, now)

    async def _award(self, row: CommunityCase, participant_id: int, reason: ContributionReason, now: datetime):
        if any(
            e.participant_id == participant_id and e.entry_type == ContributionEntryType.AWARD
            for e in await self.repo.ledger_for_case(row.id)
        ):
            return
        self.session.add(
            ContributionLedgerEntry(
                participant_id=participant_id,
                case_id=row.id,
                entry_type=ContributionEntryType.AWARD,
                reason=reason,
                points=1,
                created_at=now,
            )
        )
        await self.session.flush()

    async def _award_markers(self, row: CommunityCase):
        """(award, frozen, revoked) per award on the case, in ledger order."""
        entries = await self.repo.ledger_for_case(row.id)
        state = {}
        for entry in entries:
            if entry.entry_type == ContributionEntryType.AWARD:
                state[entry.id] = [entry, False, False]
            elif entry.reverses_entry_id in state:
                if entry.entry_type == ContributionEntryType.FREEZE:
                    state[entry.reverses_entry_id][1] = True
                elif entry.entry_type == ContributionEntryType.UNFREEZE:
                    state[entry.reverses_entry_id][1] = False
                elif entry.entry_type == ContributionEntryType.REVOKE:
                    state[entry.reverses_entry_id][2] = True
        return list(state.values())

    async def _mark(self, award: ContributionLedgerEntry, entry_type: ContributionEntryType, now: datetime):
        self.session.add(
            ContributionLedgerEntry(
                participant_id=award.participant_id,
                case_id=award.case_id,
                entry_type=entry_type,
                reason=award.reason,
                points=-1 if entry_type == ContributionEntryType.REVOKE else 0,
                reverses_entry_id=award.id,
                created_at=now,
            )
        )
        await self.session.flush()

    async def _freeze(self, row: CommunityCase, now: datetime):
        for award, frozen, revoked in await self._award_markers(row):
            if not frozen and not revoked:
                await self._mark(award, ContributionEntryType.FREEZE, now)

    async def _unfreeze(self, row: CommunityCase, now: datetime):
        for award, frozen, revoked in await self._award_markers(row):
            if frozen and not revoked:
                await self._mark(award, ContributionEntryType.UNFREEZE, now)

    async def _revoke(self, row: CommunityCase, now: datetime, keep: set[ContributionReason] = frozenset()):
        for award, _frozen, revoked in await self._award_markers(row):
            if not revoked and award.reason not in keep:
                await self._mark(award, ContributionEntryType.REVOKE, now)

    async def _event(
        self,
        row: CommunityCase,
        now: datetime,
        event_type: CaseEventType,
        actor_participant_id: int | None,
        *,
        revision: int | None = None,
        reason_code: str | None = None,
        reason_text: str | None = None,
        payload: dict | None = None,
    ):
        self.session.add(
            CommunityCaseEvent(
                case_id=row.id,
                event_type=event_type,
                case_version=row.version,
                revision=revision,
                actor_participant_id=actor_participant_id,
                reason_code=reason_code,
                reason_text=reason_text,
                payload=payload,
                created_at=now,
            )
        )
        await self.session.flush()

    # --- views ----------------------------------------------------------------------------------

    def _view(self, row: CommunityCase, revision, now: datetime, report: PrecheckReport) -> CaseView:
        return CaseView(
            row,
            revision,
            derive_publication_status(row.publication_state, row.published_until, now),
            len(report.supporters),
        )

    async def _stored_view(self, row: CommunityCase, now: datetime) -> CaseView:
        return CaseView(
            row,
            await self.repo.revision(row.id, row.current_revision),
            derive_publication_status(row.publication_state, row.published_until, now),
            await self._supporters_from_latest(row.id) or 0,
        )

    async def _supporters_from_latest(self, case_id: int) -> int | None:
        latest = await self.repo.latest_precheck(case_id)
        if latest is None:
            return None
        for result in latest[1]:
            if result.check_code == "CORROBORATION" and result.detail is not None:
                return int(result.detail.get("supporters", 0))
        return None

    def _photo_view(self, photo: CommunityEvidencePhoto, now: datetime, row: CommunityCase) -> PhotoView:
        eligible = photo.deleted_at is None and photo.time_status == PhotoTimeStatus.VALID
        if photo.deleted_at is not None:
            return PhotoView(photo, False, "DELETED")
        if not eligible:
            return PhotoView(photo, False, "MANUAL_REVIEW_REQUIRED")
        if row.publication_state != CasePublicationState.UNPUBLISHED:
            # Mirrors precheck: once published, natural ageing never revokes eligibility.
            return PhotoView(photo, True, "ELIGIBLE")
        within = counts_at_publication(photo.time_status, photo.captured_at, now)
        return PhotoView(photo, within, "ELIGIBLE" if within else "OUTSIDE_PUBLICATION_WINDOW")


class ModerationService(CommunityCaseService):
    """MODERATOR-only review. A moderator never reviews a case they took part in."""

    async def _moderator(self, principal: AuthenticatedUser) -> CommunityParticipant:
        if not principal.is_moderator:
            raise forbidden()
        return await self._participant(principal)

    async def queue(
        self,
        principal: AuthenticatedUser,
        now: datetime,
        *,
        review_statuses: list[str],
        fact_type: str | None,
        city: str | None,
        parking_id: int | None,
        limit: int,
        offset: int,
    ):
        moderator = await self._moderator(principal)
        rows = await self.repo.queue(review_statuses, fact_type, city, parking_id, limit, offset)
        involved = await self.repo.participant_case_ids(moderator.id)
        page = rows[:limit]
        return [(await self._stored_view(row, now), row.id in involved) for row in page], len(rows) > limit

    async def detail(self, principal: AuthenticatedUser, now: datetime, case_id: int) -> CaseDetail:
        moderator = await self._moderator(principal)
        row = await self.repo.case(case_id)
        if row is None:
            raise case_not_found()
        recused = await self.repo.is_participant(row.id, moderator.id)
        report = await self._evaluate(row, now)
        return CaseDetail(
            view=await self._stored_view(row, now),
            revisions=await self.repo.revisions(row.id),
            photos=[self._photo_view(photo, now, row) for photo in await self.repo.photos(row.id)],
            events=await self.repo.events(row.id),
            my_stance=None,
            stances=await self.repo.stances(row.id),
            precheck=await self.repo.latest_precheck(row.id),
            viewer_participant_id=moderator.id,
            recused=recused,
            preview={
                "accept_basis": (
                    row.publication_basis or PublicationBasis.MANUAL_REVIEW
                    if row.publication_state != CasePublicationState.UNPUBLISHED
                    else PublicationBasis.MANUAL_REVIEW
                ),
                "accept_published_until": (
                    row.published_until if row.first_published_at is not None else publication_term_end(now)
                ),
                "award_original_report": [row.author_participant_id],
                "award_corroboration": list(report.supporters),
                "award_upheld_objection": list(report.objectors),
                "precheck_decision": report.decision,
            },
        )

    async def decide(
        self,
        principal: AuthenticatedUser,
        now: datetime,
        case_id: int,
        *,
        decision: str,
        reason_code: str,
        reason_text: str,
        expected_version: int,
        superseded_by_case_id: int | None,
    ) -> CaseView:
        moderator = await self._moderator(principal)
        row = await self._locked_case(case_id)
        if await self.repo.is_participant(row.id, moderator.id):
            raise DiscoveryError("CONFLICT_OF_INTEREST", "You took part in this case and cannot review it.", 403)
        if row.version != expected_version:
            raise conflict("VERSION_CONFLICT", "The case changed; reload and review the latest version.")
        if row.review_status in CLOSED_REVIEW or row.publication_state == CasePublicationState.WITHDRAWN:
            raise conflict("INVALID_DECISION", "This case is already closed.")
        reason = {"reason_code": reason_code, "reason_text": reason_text}
        report = await self._evaluate(row, now)
        if decision == "ACCEPT":
            if (
                row.review_status == CaseReviewStatus.ACCEPTED
                and row.publication_state == CasePublicationState.PUBLISHED
            ):
                raise conflict("INVALID_DECISION", "This case is already accepted and published.")
            await self._event(row, now, CaseEventType.MANUAL_ACCEPTED, moderator.id, **reason)
            # Accepting rules on the objections in front of the moderator, published or not,
            # so the next precheck does not immediately suspend the accepted observation.
            for stance in await self.repo.stances(row.id, active_only=True):
                if stance.stance == CaseStance.OPPOSE:
                    stance.invalidated_at = now
                    stance.invalidated_reason = "OVERRULED"
                    await self._event(row, now, CaseEventType.STANCE_INVALIDATED, moderator.id)
            await self.session.flush()
            if row.publication_state == CasePublicationState.SUSPENDED:
                row.publication_state = CasePublicationState.PUBLISHED
                row.review_status = CaseReviewStatus.ACCEPTED
                await self._unfreeze(row, now)
                await self._event(row, now, CaseEventType.RESUMED, moderator.id)
            else:
                await self._publish(row, now, PublicationBasis.MANUAL_REVIEW, report)
        elif decision == "REJECT":
            was_published = row.publication_state != CasePublicationState.UNPUBLISHED
            # Withdraw before changing the review status: PUBLISHED always requires ACCEPTED.
            if was_published:
                row.publication_state = CasePublicationState.WITHDRAWN
                row.withdrawn_at = now
            row.review_status = CaseReviewStatus.REJECTED
            await self._event(row, now, CaseEventType.MANUAL_REJECTED, moderator.id, **reason)
            if was_published:
                await self._revoke(row, now)
                await self._event(row, now, CaseEventType.WITHDRAWN, moderator.id)
                # An objection that exposed a published error is upheld and earns a point.
                for participant_id in report.objectors:
                    await self._award(row, participant_id, ContributionReason.UPHELD_OBJECTION, now)
        elif decision == "REQUEST_EVIDENCE":
            if row.publication_state != CasePublicationState.UNPUBLISHED:
                raise conflict("INVALID_DECISION", "Published observations are accepted or rejected, not revised.")
            row.review_status = CaseReviewStatus.NEEDS_EVIDENCE
            await self._event(row, now, CaseEventType.EVIDENCE_REQUESTED, moderator.id, **reason)
        elif decision == "SUPERSEDE":
            successor = await self.repo.case(superseded_by_case_id) if superseded_by_case_id else None
            if successor is None or successor.id == row.id or successor.parking_id != row.parking_id:
                raise invalid("SUCCESSOR_INVALID", "Supersede needs another existing case for the same parking lot.")
            was_published = row.publication_state != CasePublicationState.UNPUBLISHED
            if was_published:
                row.publication_state = CasePublicationState.WITHDRAWN
                row.withdrawn_at = now
            row.review_status = CaseReviewStatus.SUPERSEDED
            row.superseded_by_case_id = successor.id
            await self._event(
                row, now, CaseEventType.SUPERSEDED, moderator.id, payload={"successor": successor.id}, **reason
            )
            if was_published:
                await self._event(row, now, CaseEventType.WITHDRAWN, moderator.id)
        else:  # pragma: no cover - the schema restricts decisions
            raise invalid("INVALID_DECISION", "Unknown decision.")
        row.version += 1
        row.updated_at = now
        await self.session.flush()
        return CaseView(
            row,
            await self.repo.revision(row.id, row.current_revision),
            derive_publication_status(row.publication_state, row.published_until, now),
            len(report.supporters),
        )

    async def delete_photo(self, principal: AuthenticatedUser, now: datetime, photo_id: int) -> int | None:
        """Clear the photo and record its object for durable removal; returns the outbox id."""
        moderator = await self._moderator(principal)
        photo = await self.repo.photo(photo_id, for_update=True)
        if photo is None or photo.deleted_at is not None:
            raise not_found("PHOTO_NOT_FOUND", "The photo was not found.")
        row = await self._locked_case(photo.case_id)
        if await self.repo.is_participant(row.id, moderator.id):
            raise DiscoveryError("CONFLICT_OF_INTEREST", "You took part in this case and cannot moderate it.", 403)
        key = photo.storage_key
        photo.deleted_at = now
        photo.storage_key = None
        photo.exif_datetime_original = None
        photo.exif_offset_time_original = None
        photo.exif_subsec_time_original = None
        photo.captured_at = None
        await self.session.flush()
        await self._event(row, now, CaseEventType.PHOTO_DELETED, moderator.id, payload={"photo_id": photo.id})
        await self._precheck(row, now)
        if key is None:
            return None
        outbox = await self.repo.add(StorageDeletion(storage_key=key, requested_at=now, created_at=now))
        return outbox.id

    async def add_source_verification(self, principal: AuthenticatedUser, now: datetime, **fields):
        moderator = await self._moderator(principal)
        if await self.session.get(DataSource, fields["source_id"]) is None:
            raise not_found("SOURCE_NOT_FOUND", "The data source was not found.")
        if fields.get("parking_id") is not None and not await self.users.lot_exists(fields["parking_id"]):
            raise not_found("PARKING_NOT_FOUND", "The parking lot was not found.")
        if fields.get("zone_id") is not None and not await self.reports.zone_in_lot(
            fields["zone_id"], fields["parking_id"]
        ):
            raise invalid("ZONE_NOT_IN_PARKING", "The zone does not belong to this parking lot.")
        return await self.repo.add(
            SourceVerification(**fields, verified_by_participant_id=moderator.id, verified_at=now, created_at=now)
        )

    async def revoke_source_verification(self, principal: AuthenticatedUser, now: datetime, verification_id: int):
        await self._moderator(principal)
        verification = await self.repo.source_verification(verification_id, for_update=True)
        if verification is None:
            raise not_found("SOURCE_VERIFICATION_NOT_FOUND", "The source verification was not found.")
        if verification.revoked_at is not None:
            raise conflict("ALREADY_REVOKED", "This source verification is already revoked.")
        verification.revoked_at = max(now, verification.verified_at)
        await self.session.flush()
        return verification

    async def set_suspension(
        self, principal: AuthenticatedUser, now: datetime, participant_id: int, reason_code: str | None
    ) -> CommunityParticipant:
        moderator = await self._moderator(principal)
        target = await self.repo.participant(participant_id, for_update=True)
        if target is None:
            raise not_found("PARTICIPANT_NOT_FOUND", "The participant was not found.")
        if target.id == moderator.id:
            raise invalid("SELF_SUSPENSION", "Moderators cannot change their own suspension.")
        target.suspended_at = now if reason_code else None
        target.suspended_reason = reason_code
        await self.session.flush()
        # Their stances stop (or resume) counting; recount every case they are active on.
        for case_id in await self.repo.cases_with_active_stance(target.id):
            await self._precheck(await self._locked_case(case_id), now)
        return target


__all__ = [
    "CORROBORATION_THRESHOLD",
    "CaseDetail",
    "CaseView",
    "CommunityCaseService",
    "Contributions",
    "ModerationService",
    "Observation",
    "PhotoView",
]
