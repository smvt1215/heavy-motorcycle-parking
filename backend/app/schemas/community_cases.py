"""Wire contracts for community verification (docs/community-verification-contract.md §9)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.models.enums import (
    CaseEventType,
    CaseReviewStatus,
    CaseStance,
    CommunityFactType,
    ContributionEntryType,
    ContributionReason,
    PhotoTimeStatus,
    PrecheckCode,
    PrecheckOutcome,
    PublicationBasis,
    PublicationStatus,
    RuleKind,
    SourceFactKind,
)
from app.schemas.parking import PublicVehicle, WireModel

HTTPS_URL = r"^https://\S+$"
REASON_CODE = r"^[A-Z0-9_]{2,64}$"


def _blank_is_none(value):
    return value.strip() or None if isinstance(value, str) else value


class CaseCreate(WireModel):
    parking_id: int = Field(gt=0)
    zone_id: int | None = Field(default=None, gt=0)
    fact_type: CommunityFactType
    # Explicit rider class for permission, rate and entrance access; null for facilities/entrance location.
    vehicle: PublicVehicle | None = None
    proposed_value: dict[str, Any]
    description: str | None = Field(default=None, max_length=1000)
    source_url: str | None = Field(default=None, max_length=2000, pattern=HTTPS_URL)

    _description = field_validator("description")(classmethod(lambda cls, value: _blank_is_none(value)))


class CaseRevisionCreate(WireModel):
    proposed_value: dict[str, Any]
    description: str | None = Field(default=None, max_length=1000)
    source_url: str | None = Field(default=None, max_length=2000, pattern=HTTPS_URL)

    _description = field_validator("description")(classmethod(lambda cls, value: _blank_is_none(value)))


class StanceUpdate(WireModel):
    stance: CaseStance
    observed_value: dict[str, Any] | None = None


class ModerationDecision(WireModel):
    decision: Literal["ACCEPT", "REJECT", "REQUEST_EVIDENCE", "SUPERSEDE"]
    reason_code: str = Field(pattern=REASON_CODE)
    reason_text: str = Field(min_length=1, max_length=1000)
    expected_version: int = Field(ge=1)
    superseded_by_case_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def successor_only_for_supersede(self):
        if (self.decision == "SUPERSEDE") != (self.superseded_by_case_id is not None):
            raise ValueError("superseded_by_case_id is required for SUPERSEDE and only for SUPERSEDE")
        if not self.reason_text.strip():
            raise ValueError("reason_text must not be blank")
        return self


class SourceVerificationCreate(WireModel):
    source_id: int = Field(gt=0)
    fact_kind: SourceFactKind
    parking_id: int | None = Field(default=None, gt=0)
    zone_id: int | None = Field(default=None, gt=0)
    parser_code: str | None = Field(default=None, min_length=1, max_length=64)
    parser_config_version: str | None = Field(default=None, min_length=1, max_length=32)
    rule_kind: RuleKind | None = None
    authority_priority: int | None = None
    evidence_url: str = Field(max_length=2000, pattern=HTTPS_URL)
    evidence_note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def mirror_database_rules(self):
        tier = self.rule_kind is not None and self.authority_priority is not None
        no_tier = self.rule_kind is None and self.authority_priority is None
        if (self.fact_kind == SourceFactKind.PERMISSION and not tier) or (
            self.fact_kind != SourceFactKind.PERMISSION and not no_tier
        ):
            raise ValueError("rule_kind and authority_priority are required for PERMISSION and only for PERMISSION")
        if (self.parser_code is None) != (self.parser_config_version is None):
            raise ValueError("parser_code and parser_config_version must be given together")
        if self.zone_id is not None and self.parking_id is None:
            raise ValueError("zone_id requires parking_id")
        return self


class SuspensionCreate(WireModel):
    reason_code: str = Field(pattern=REASON_CODE)


class CaseListQuery(WireModel):
    limit: int = Field(default=20, ge=1, le=100)
    before: int | None = Field(default=None, gt=0)


class QueueQuery(WireModel):
    review_status: CaseReviewStatus = CaseReviewStatus.MANUAL_REVIEW
    fact_type: CommunityFactType | None = None
    city: str | None = Field(default=None, max_length=64)
    parking_id: int | None = Field(default=None, gt=0)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)


# --- responses ------------------------------------------------------------------------------


class CaseSummary(WireModel):
    id: int
    parking_id: int
    zone_id: int | None
    fact_type: CommunityFactType
    vehicle: PublicVehicle | None
    review_status: CaseReviewStatus
    publication_status: PublicationStatus
    publication_basis: PublicationBasis | None
    first_published_at: datetime | None
    published_until: datetime | None
    current_revision: int
    version: int
    supporters: int
    proposed_value: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MetadataTime(WireModel):
    """Backend-extracted photo metadata time; editable EXIF, never a verified capture time."""

    status: PhotoTimeStatus
    captured_at: datetime | None
    datetime_original: str | None
    offset_time_original: str | None


class PhotoUploadResponse(WireModel):
    photo_id: int
    metadata_time: MetadataTime
    counts_toward_corroboration: bool
    message_code: Literal[
        "ELIGIBLE",
        "MANUAL_REVIEW_REQUIRED",
        "DUPLICATE_EVIDENCE",
        "OUTSIDE_PUBLICATION_WINDOW",
        "NOT_COUNTED",
        "DELETED",
    ]


class OwnPhoto(PhotoUploadResponse):
    owner: Literal["revision", "stance"]
    received_at: datetime
    deleted: bool


class StanceResponse(WireModel):
    id: int
    case_id: int
    stance: CaseStance
    observed_value: dict[str, Any] | None
    created_at: datetime


class RevisionItem(WireModel):
    revision: int
    proposed_value: dict[str, Any]
    description: str | None
    source_url: str | None
    created_at: datetime


class TimelineEvent(WireModel):
    id: int
    event_type: CaseEventType
    actor: Literal["SYSTEM", "YOU", "MODERATOR", "PARTICIPANT"]
    revision: int | None
    reason_code: str | None
    reason_text: str | None
    created_at: datetime


class CaseDetailResponse(WireModel):
    case: CaseSummary
    revisions: list[RevisionItem]
    my_stance: StanceResponse | None
    my_photos: list[OwnPhoto]
    timeline: list[TimelineEvent]


class CasePage(WireModel):
    next_before: int | None
    has_more: bool


class MyCasesResponse(WireModel):
    items: list[CaseSummary]
    page: CasePage


class ContributionEntry(WireModel):
    case_id: int
    entry_type: ContributionEntryType
    reason: ContributionReason
    points: int
    created_at: datetime


class ContributionsResponse(WireModel):
    points: int
    level: Literal["NEWCOMER", "L1", "L2", "L3", "L4"]
    entries: list[ContributionEntry]


class ObservationProvenance(WireModel):
    source_type: Literal["COMMUNITY"] = "COMMUNITY"


class CommunityObservation(WireModel):
    observation_id: int
    parking_id: int
    zone_id: int | None
    fact_type: CommunityFactType
    vehicle: PublicVehicle | None
    value: dict[str, Any]
    publication_basis: PublicationBasis
    publication_status: PublicationStatus
    corroborator_count: int | None
    first_published_at: datetime
    published_until: datetime | None
    label: str | None
    provenance: ObservationProvenance = ObservationProvenance()


class CommunityObservationsResponse(WireModel):
    items: list[CommunityObservation]


class QueueItem(WireModel):
    case: CaseSummary
    recused: bool


class QueueResponse(WireModel):
    items: list[QueueItem]
    has_more: bool
    next_offset: int | None


class ModerationStance(WireModel):
    id: int
    participant_id: int
    stance: CaseStance
    observed_value: dict[str, Any] | None
    created_at: datetime
    withdrawn_at: datetime | None
    invalidated_at: datetime | None
    invalidated_reason: str | None


class ModerationPhoto(OwnPhoto):
    participant_id: int
    normalized_sha256: str


class PrecheckResultItem(WireModel):
    check_code: PrecheckCode
    outcome: PrecheckOutcome
    reason_code: str | None
    detail: dict[str, Any] | None


class PrecheckSummary(WireModel):
    rule_version: str
    outcome: PrecheckOutcome
    evaluated_at: datetime
    results: list[PrecheckResultItem]


class ModerationEvent(WireModel):
    id: int
    event_type: CaseEventType
    actor_participant_id: int | None
    case_version: int
    revision: int | None
    reason_code: str | None
    reason_text: str | None
    payload: dict[str, Any] | None
    created_at: datetime


class DecisionPreview(WireModel):
    accept_basis: PublicationBasis
    accept_published_until: datetime | None
    award_original_report: list[int]
    award_corroboration: list[int]
    award_upheld_objection: list[int]
    precheck_decision: str


class ModerationCaseResponse(WireModel):
    case: CaseSummary
    author_participant_id: int
    recused: bool
    revisions: list[RevisionItem]
    stances: list[ModerationStance]
    photos: list[ModerationPhoto]
    precheck: PrecheckSummary | None
    timeline: list[ModerationEvent]
    preview: DecisionPreview


class SourceVerificationResponse(WireModel):
    id: int
    source_id: int
    fact_kind: SourceFactKind
    parking_id: int | None
    zone_id: int | None
    parser_code: str | None
    parser_config_version: str | None
    rule_kind: RuleKind | None
    authority_priority: int | None
    evidence_url: str
    evidence_note: str | None
    verified_at: datetime
    revoked_at: datetime | None


class ParticipantResponse(WireModel):
    id: int
    suspended_at: datetime | None
    suspended_reason: str | None
