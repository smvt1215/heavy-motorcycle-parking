"""Community verification endpoints. Business rules live in app.services.community_cases."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Path, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.config import settings
from app.db import get_session
from app.domain.errors import DiscoveryError
from app.models.enums import CaseEventType
from app.schemas.community_cases import (
    CaseCreate,
    CaseDetailResponse,
    CaseListQuery,
    CasePage,
    CaseRevisionCreate,
    CaseSummary,
    CommunityObservation,
    CommunityObservationsResponse,
    ContributionEntry,
    ContributionsResponse,
    DecisionPreview,
    MetadataTime,
    ModerationCaseResponse,
    ModerationDecision,
    ModerationEvent,
    ModerationPhoto,
    ModerationStance,
    MyCasesResponse,
    OwnPhoto,
    ParticipantResponse,
    PhotoUploadResponse,
    PrecheckResultItem,
    PrecheckSummary,
    QueueItem,
    QueueQuery,
    QueueResponse,
    RevisionItem,
    SourceVerificationCreate,
    SourceVerificationResponse,
    StanceResponse,
    StanceUpdate,
    SuspensionCreate,
    TimelineEvent,
)
from app.schemas.error import ErrorResponse
from app.services.community_cases import CaseDetail, CaseView, CommunityCaseService, ModerationService, PhotoView
from app.services.idempotency import IdempotencyService, unit_of_work
from app.services.storage_outbox import drain

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_session)]
CaseID = Annotated[int, Path(gt=0)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)]
ERRORS = {code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 428, 429)}
MANUAL_EVENTS = {
    CaseEventType.MANUAL_ACCEPTED,
    CaseEventType.MANUAL_REJECTED,
    CaseEventType.EVIDENCE_REQUESTED,
    CaseEventType.SUPERSEDED,
    CaseEventType.RESUMED,
    CaseEventType.WITHDRAWN,
    CaseEventType.PHOTO_DELETED,
}
NO_STORE = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}


def _service(request: Request, session: Session) -> CommunityCaseService:
    return CommunityCaseService(
        session,
        request.app.state.object_storage,
        settings.report_photo_max_bytes,
        request.app.state.community_auto_publish_enabled,
    )


def _moderation(request: Request, session: Session) -> ModerationService:
    return ModerationService(
        session,
        request.app.state.object_storage,
        settings.report_photo_max_bytes,
        request.app.state.community_auto_publish_enabled,
    )


Cases = Annotated[CommunityCaseService, Depends(_service)]
Moderation = Annotated[ModerationService, Depends(_moderation)]


async def write_limit(request: Request, user: CurrentUser) -> None:
    await _limit(request, user.id)


async def _limit(request: Request, user_id: int) -> None:
    if not await request.app.state.community_rate_limiter.allow(f"user:{user_id}"):
        raise DiscoveryError("RATE_LIMITED", "Too many community submissions; try again shortly.", 429)


# Only for non-idempotent writes; idempotent routes rate-limit after the replay check.
RateLimited = Depends(write_limit)


# --- mapping --------------------------------------------------------------------------------


def _summary(view: CaseView) -> CaseSummary:
    case = view.case
    return CaseSummary(
        id=case.id,
        parking_id=case.parking_id,
        zone_id=case.zone_id,
        fact_type=case.fact_type,
        vehicle=case.vehicle,
        review_status=case.review_status,
        publication_status=view.publication_status,
        publication_basis=case.publication_basis,
        first_published_at=case.first_published_at,
        published_until=case.published_until,
        current_revision=case.current_revision,
        version=case.version,
        supporters=view.supporters,
        proposed_value=view.revision.proposed_value,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def _metadata(photo) -> MetadataTime:
    return MetadataTime(
        status=photo.time_status,
        captured_at=photo.captured_at,
        datetime_original=photo.exif_datetime_original,
        offset_time_original=photo.exif_offset_time_original,
    )


def _own_photo(view: PhotoView) -> OwnPhoto:
    photo = view.photo
    return OwnPhoto(
        photo_id=photo.id,
        metadata_time=_metadata(photo),
        counts_toward_corroboration=view.counts_toward_corroboration,
        message_code=view.message_code,
        owner="revision" if photo.revision_id is not None else "stance",
        received_at=photo.received_at,
        deleted=photo.deleted_at is not None,
    )


def _stance(stance) -> StanceResponse:
    return StanceResponse(
        id=stance.id,
        case_id=stance.case_id,
        stance=stance.stance,
        observed_value=stance.observed_value,
        created_at=stance.created_at,
    )


def _revisions(detail: CaseDetail, *, show_text: bool = True) -> list[RevisionItem]:
    # Unmoderated free text is private to the author (and moderators), never other participants.
    return [
        RevisionItem(
            revision=r.revision,
            proposed_value=r.proposed_value,
            description=r.description if show_text or r.description_approved_at is not None else None,
            source_url=r.source_url,
            created_at=r.created_at,
        )
        for r in detail.revisions
    ]


def _actor(event, viewer: int | None) -> Literal["SYSTEM", "YOU", "MODERATOR", "PARTICIPANT"]:
    if event.actor_participant_id is None:
        return "SYSTEM"
    if event.actor_participant_id == viewer:
        return "YOU"
    return "MODERATOR" if event.event_type in MANUAL_EVENTS else "PARTICIPANT"


def _json(status_code: int, model) -> tuple[int, dict]:
    return status_code, model.model_dump(mode="json")


# --- submission -----------------------------------------------------------------------------


@router.post(
    "/community/cases",
    response_model=CaseSummary,
    status_code=status.HTTP_201_CREATED,
    tags=["community"],
    responses=ERRORS,
)
async def create_case(
    request: Request, body: CaseCreate, user: CurrentUser, cases: Cases, session: Session, key: IdempotencyKey = None
):
    now = request.state.received_at

    async def handler():
        view = await cases.create(user, now, **body.model_dump())
        return _json(201, _summary(view))

    code, payload = await IdempotencyService(session).run(
        user.id, "create_case", key, body.model_dump(mode="json"), now, handler, lambda: _limit(request, user.id)
    )
    return JSONResponse(payload, status_code=code)


@router.post(
    "/community/cases/{case_id}/revisions",
    response_model=CaseSummary,
    status_code=status.HTTP_201_CREATED,
    tags=["community"],
    responses=ERRORS,
)
async def revise_case(
    request: Request,
    case_id: CaseID,
    body: CaseRevisionCreate,
    user: CurrentUser,
    cases: Cases,
    session: Session,
    key: IdempotencyKey = None,
):
    now = request.state.received_at

    async def handler():
        return _json(201, _summary(await cases.revise(user, now, case_id, **body.model_dump())))

    payload = {"case_id": case_id, **body.model_dump(mode="json")}
    code, response = await IdempotencyService(session).run(
        user.id, "revise_case", key, payload, now, handler, lambda: _limit(request, user.id)
    )
    return JSONResponse(response, status_code=code)


@router.post(
    "/community/cases/{case_id}/photos",
    response_model=PhotoUploadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["community"],
    responses={**ERRORS, 413: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    dependencies=[RateLimited],
)
async def upload_case_photo(
    request: Request,
    case_id: CaseID,
    user: CurrentUser,
    cases: Cases,
    session: Session,
    file: Annotated[UploadFile, File()],
    owner: Annotated[Literal["revision", "stance"], Form()],
):
    data = await file.read(settings.report_photo_max_bytes + 1)
    now = request.state.received_at
    view = await unit_of_work(session, lambda: cases.add_photo(user, now, case_id, owner, data))
    photo = view.photo
    return PhotoUploadResponse(
        photo_id=photo.id,
        metadata_time=_metadata(photo),
        counts_toward_corroboration=view.counts_toward_corroboration,
        message_code=view.message_code,
    )


@router.put(
    "/community/cases/{case_id}/stance",
    response_model=StanceResponse,
    tags=["community"],
    responses=ERRORS,
)
async def set_stance(
    request: Request,
    case_id: CaseID,
    body: StanceUpdate,
    user: CurrentUser,
    cases: Cases,
    session: Session,
    key: IdempotencyKey = None,
):
    now = request.state.received_at

    async def handler():
        return _json(200, _stance(await cases.set_stance(user, now, case_id, body.stance, body.observed_value)))

    payload = {"case_id": case_id, **body.model_dump(mode="json")}
    code, response = await IdempotencyService(session).run(
        user.id, "set_stance", key, payload, now, handler, lambda: _limit(request, user.id)
    )
    return JSONResponse(response, status_code=code)


@router.delete(
    "/community/cases/{case_id}/stance",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["community"],
    responses=ERRORS,
)
async def withdraw_stance(
    request: Request, case_id: CaseID, user: CurrentUser, cases: Cases, session: Session, key: IdempotencyKey = None
):
    now = request.state.received_at

    async def handler():
        await cases.withdraw_stance(user, now, case_id)
        return 204, {}

    # A lost 204 retried with the same key replays 204 instead of 404 STANCE_NOT_FOUND.
    await IdempotencyService(session).run(user.id, "withdraw_stance", key, {"case_id": case_id}, now, handler)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- reading --------------------------------------------------------------------------------


@router.get("/me/community/cases", response_model=MyCasesResponse, tags=["community"], responses=ERRORS)
async def my_cases(request: Request, user: CurrentUser, cases: Cases, query: Annotated[CaseListQuery, Query()]):
    views, next_before = await cases.my_cases(user, request.state.received_at, query.limit, query.before)
    return MyCasesResponse(
        items=[_summary(view) for view in views],
        page=CasePage(next_before=next_before, has_more=next_before is not None),
    )


@router.get("/me/community/cases/{case_id}", response_model=CaseDetailResponse, tags=["community"], responses=ERRORS)
async def my_case(request: Request, case_id: CaseID, user: CurrentUser, cases: Cases):
    detail = await cases.case_for_participant(user, request.state.received_at, case_id)
    return CaseDetailResponse(
        case=_summary(detail.view),
        revisions=_revisions(detail, show_text=detail.view.case.author_participant_id == detail.viewer_participant_id),
        my_stance=_stance(detail.my_stance) if detail.my_stance is not None else None,
        my_photos=[_own_photo(photo) for photo in detail.photos],
        timeline=[
            TimelineEvent(
                id=e.id,
                event_type=e.event_type,
                actor=_actor(e, detail.viewer_participant_id),
                revision=e.revision,
                reason_code=e.reason_code,
                reason_text=e.reason_text,
                created_at=e.created_at,
            )
            for e in detail.events
        ],
    )


@router.get("/me/contributions", response_model=ContributionsResponse, tags=["community"], responses=ERRORS)
async def my_contributions(user: CurrentUser, cases: Cases):
    result = await cases.contributions(user)
    return ContributionsResponse(
        points=result.points,
        level=result.level,
        entries=[
            ContributionEntry(
                case_id=e.case_id, entry_type=e.entry_type, reason=e.reason, points=e.points, created_at=e.created_at
            )
            for e in result.entries
        ],
    )


@router.get(
    "/community/photos/{photo_id}",
    tags=["community"],
    responses={**ERRORS, 200: {"content": {"image/jpeg": {}}}, 503: {"model": ErrorResponse}},
    response_class=Response,
)
async def photo(photo_id: Annotated[int, Path(gt=0)], user: CurrentUser, cases: Cases):
    body, content_type = await cases.photo_content(user, photo_id)
    return Response(content=body, media_type=content_type, headers=NO_STORE)


@router.get(
    "/parking/{parking_id}/community-observations",
    response_model=CommunityObservationsResponse,
    tags=["community"],
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def community_observations(request: Request, parking_id: Annotated[int, Path(gt=0)], cases: Cases):
    items = []
    for observation in await cases.public_observations(parking_id, request.state.received_at):
        case = observation.case
        items.append(
            CommunityObservation(
                observation_id=case.id,
                parking_id=case.parking_id,
                zone_id=case.zone_id,
                fact_type=case.fact_type,
                vehicle=case.vehicle,
                value=observation.revision.proposed_value,
                publication_basis=case.publication_basis,
                publication_status="PUBLISHED",
                corroborator_count=observation.corroborators,
                first_published_at=case.first_published_at,
                published_until=case.published_until,
                label=observation.label,
            )
        )
    return CommunityObservationsResponse(items=items)


# --- moderation -----------------------------------------------------------------------------


@router.get("/moderation/cases", response_model=QueueResponse, tags=["moderation"], responses=ERRORS)
async def moderation_queue(
    request: Request, user: CurrentUser, moderation: Moderation, query: Annotated[QueueQuery, Query()]
):
    rows, has_more = await moderation.queue(
        user,
        request.state.received_at,
        review_statuses=[query.review_status],
        fact_type=query.fact_type,
        city=query.city,
        parking_id=query.parking_id,
        limit=query.limit,
        offset=query.offset,
    )
    return QueueResponse(
        items=[QueueItem(case=_summary(view), recused=recused) for view, recused in rows],
        has_more=has_more,
        next_offset=query.offset + query.limit if has_more else None,
    )


@router.get(
    "/moderation/cases/{case_id}",
    response_model=ModerationCaseResponse,
    tags=["moderation"],
    responses=ERRORS,
)
async def moderation_case(request: Request, case_id: CaseID, user: CurrentUser, moderation: Moderation):
    detail = await moderation.detail(user, request.state.received_at, case_id)
    precheck = None
    if detail.precheck is not None:
        run, results = detail.precheck
        precheck = PrecheckSummary(
            rule_version=run.rule_version,
            outcome=run.outcome,
            evaluated_at=run.evaluated_at,
            results=[
                PrecheckResultItem(
                    check_code=r.check_code, outcome=r.outcome, reason_code=r.reason_code, detail=r.detail
                )
                for r in results
            ],
        )
    return ModerationCaseResponse(
        case=_summary(detail.view),
        author_participant_id=detail.view.case.author_participant_id,
        recused=detail.recused,
        revisions=_revisions(detail),
        stances=[
            ModerationStance(
                id=s.id,
                participant_id=s.participant_id,
                stance=s.stance,
                observed_value=s.observed_value,
                created_at=s.created_at,
                withdrawn_at=s.withdrawn_at,
                invalidated_at=s.invalidated_at,
                invalidated_reason=s.invalidated_reason,
            )
            for s in detail.stances
        ],
        photos=[
            ModerationPhoto(
                **_own_photo(view).model_dump(),
                participant_id=view.photo.participant_id,
                normalized_sha256=view.photo.normalized_sha256,
            )
            for view in detail.photos
        ],
        precheck=precheck,
        timeline=[
            ModerationEvent(
                id=e.id,
                event_type=e.event_type,
                actor_participant_id=e.actor_participant_id,
                case_version=e.case_version,
                revision=e.revision,
                reason_code=e.reason_code,
                reason_text=e.reason_text,
                payload=e.payload,
                created_at=e.created_at,
            )
            for e in detail.events
        ],
        preview=DecisionPreview(**detail.preview),
    )


@router.post(
    "/moderation/cases/{case_id}/decisions",
    response_model=CaseSummary,
    tags=["moderation"],
    responses=ERRORS,
)
async def moderation_decision(
    request: Request,
    case_id: CaseID,
    body: ModerationDecision,
    user: CurrentUser,
    moderation: Moderation,
    session: Session,
    key: IdempotencyKey = None,
):
    now = request.state.received_at

    async def handler():
        return _json(200, _summary(await moderation.decide(user, now, case_id, **body.model_dump())))

    payload = {"case_id": case_id, **body.model_dump(mode="json")}
    code, response = await IdempotencyService(session).run(user.id, "moderation_decision", key, payload, now, handler)
    return JSONResponse(response, status_code=code)


@router.delete(
    "/moderation/photos/{photo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["moderation"],
    responses=ERRORS,
)
async def delete_photo(
    request: Request, photo_id: Annotated[int, Path(gt=0)], user: CurrentUser, moderation: Moderation, session: Session
):
    now = request.state.received_at
    outbox_id = await unit_of_work(session, lambda: moderation.delete_photo(user, now, photo_id))
    # The cleared row and its outbox entry are committed first; the object is removed next.
    # A failure leaves the entry pending for `python -m app.services.storage_outbox`.
    if outbox_id is not None:
        await drain(session, request.app.state.object_storage, now, [outbox_id])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _verification(row) -> SourceVerificationResponse:
    return SourceVerificationResponse(
        id=row.id,
        source_id=row.source_id,
        fact_kind=row.fact_kind,
        parking_id=row.parking_id,
        zone_id=row.zone_id,
        parser_code=row.parser_code,
        parser_config_version=row.parser_config_version,
        rule_kind=row.rule_kind,
        authority_priority=row.authority_priority,
        evidence_url=row.evidence_url,
        evidence_note=row.evidence_note,
        verified_at=row.verified_at,
        revoked_at=row.revoked_at,
    )


@router.post(
    "/moderation/source-verifications",
    response_model=SourceVerificationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["moderation"],
    responses=ERRORS,
)
async def add_source_verification(
    request: Request, body: SourceVerificationCreate, user: CurrentUser, moderation: Moderation, session: Session
):
    row = await unit_of_work(
        session, lambda: moderation.add_source_verification(user, request.state.received_at, **body.model_dump())
    )
    return _verification(row)


@router.post(
    "/moderation/source-verifications/{verification_id}/revoke",
    response_model=SourceVerificationResponse,
    tags=["moderation"],
    responses=ERRORS,
)
async def revoke_source_verification(
    request: Request,
    verification_id: Annotated[int, Path(gt=0)],
    user: CurrentUser,
    moderation: Moderation,
    session: Session,
):
    row = await unit_of_work(
        session, lambda: moderation.revoke_source_verification(user, request.state.received_at, verification_id)
    )
    return _verification(row)


def _participant(row) -> ParticipantResponse:
    return ParticipantResponse(id=row.id, suspended_at=row.suspended_at, suspended_reason=row.suspended_reason)


@router.put(
    "/moderation/participants/{participant_id}/suspension",
    response_model=ParticipantResponse,
    tags=["moderation"],
    responses=ERRORS,
)
async def suspend_participant(
    request: Request,
    participant_id: Annotated[int, Path(gt=0)],
    body: SuspensionCreate,
    user: CurrentUser,
    moderation: Moderation,
    session: Session,
):
    row = await unit_of_work(
        session,
        lambda: moderation.set_suspension(user, request.state.received_at, participant_id, body.reason_code),
    )
    return _participant(row)


@router.delete(
    "/moderation/participants/{participant_id}/suspension",
    response_model=ParticipantResponse,
    tags=["moderation"],
    responses=ERRORS,
)
async def unsuspend_participant(
    request: Request,
    participant_id: Annotated[int, Path(gt=0)],
    user: CurrentUser,
    moderation: Moderation,
    session: Session,
):
    row = await unit_of_work(
        session, lambda: moderation.set_suspension(user, request.state.received_at, participant_id, None)
    )
    return _participant(row)
