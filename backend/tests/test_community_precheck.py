"""Deterministic precheck: thresholds, evidence independence, objections and the flag."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.community import CaseInput, Decision, EvidencePhoto, StanceInput, evaluate_case
from app.models.enums import CasePublicationState as State
from app.models.enums import CaseStance as Stance
from app.models.enums import PhotoTimeStatus as Time
from app.models.enums import PrecheckCode as Code
from app.models.enums import PrecheckOutcome as Outcome
from app.models.enums import PublicationBasis as Basis

NOW = datetime(2026, 10, 10, 4, tzinfo=UTC)
RECENT = NOW - timedelta(days=1)
VALUE = {"present": True}


def photo(n, *, status=Time.VALID, captured=RECENT, deleted=False, copy=0):
    """Image `n`; `copy` > 0 is another stored row (new ID) of the same image bytes."""
    photo_id = n + 1000 * copy
    return EvidencePhoto(photo_id, f"{n:064x}", status, captured if status != Time.MISSING else None, deleted)


def support(participant, *photos, stance=Stance.SUPPORT, value=VALUE, suspended=False):
    return StanceInput(participant, participant, stance, value, photos or (photo(100 + participant),), suspended)


ORIGINAL = (photo(1),)


def case(*stances, original=ORIGINAL, state=State.UNPUBLISHED, basis=None, until=None, enabled=True, low=True):
    return CaseInput(
        low_risk=low,
        author_participant_id=1,
        author_suspended=False,
        proposed_value=VALUE,
        original_photos=original,
        stances=stances,
        publication_state=state,
        publication_basis=basis,
        published_until=until,
        auto_publish_enabled=enabled,
    )


def outcome(report, code):
    return next(result for result in report.results if result.code == code)


def test_three_independent_supporters_publish_low_risk_observation():
    report = evaluate_case(case(support(2), support(3), support(4)), NOW)
    assert report.decision == Decision.PUBLISH and report.outcome == Outcome.PASS
    assert report.supporters == (2, 3, 4)
    assert [result.code for result in report.results] == list(Code)


def test_fewer_than_three_supporters_wait_for_corroboration():
    report = evaluate_case(case(support(2), support(3)), NOW)
    assert report.decision == Decision.AWAIT
    assert outcome(report, Code.CORROBORATION).reason_code == "INSUFFICIENT_CORROBORATION"


@pytest.mark.parametrize(
    "stances",
    [
        # The author's own support never counts.
        (support(1), support(2), support(3)),
        # Duplicate image hashes count once per case.
        (support(2, photo(7)), support(3, photo(7, copy=1)), support(4)),
        # Re-using the original's image is not independent evidence.
        (support(2, photo(1, copy=1)), support(3), support(4)),
        # Suspended participants do not count.
        (support(2, suspended=True), support(3), support(4)),
        # Stale, missing, timezone-unknown or deleted evidence does not count.
        (support(2, photo(8, captured=NOW - timedelta(days=31))), support(3), support(4)),
        (support(2, photo(8, status=Time.MISSING)), support(3), support(4)),
        (support(2, photo(8, status=Time.TIMEZONE_UNKNOWN, captured=None)), support(3), support(4)),
        (support(2, photo(8, deleted=True)), support(3), support(4)),
        # CANNOT_CONFIRM is a stance, not a vote.
        (support(2, stance=Stance.CANNOT_CONFIRM, value=None), support(3), support(4)),
    ],
)
def test_ineligible_support_does_not_reach_the_threshold(stances):
    assert evaluate_case(case(*stances), NOW).decision == Decision.AWAIT


def test_photo_window_is_rechecked_at_publication_time():
    edge = case(support(2), support(3), support(4, photo(9, captured=NOW - timedelta(days=30))))
    assert evaluate_case(edge, NOW).decision == Decision.PUBLISH
    assert evaluate_case(edge, NOW + timedelta(seconds=1)).decision == Decision.AWAIT


def test_original_without_eligible_photo_goes_to_manual_review():
    report = evaluate_case(case(support(2), support(3), support(4), original=(photo(1, status=Time.MISSING),)), NOW)
    assert report.decision == Decision.MANUAL
    assert outcome(report, Code.PHOTO_TIME).reason_code == "ORIGINAL_PHOTO_TIME_UNVERIFIED"


def test_extra_invalid_photos_do_not_block_otherwise_sufficient_evidence():
    original = (photo(1), photo(2, status=Time.FUTURE, captured=NOW + timedelta(hours=1)))
    assert evaluate_case(case(support(2), support(3), support(4), original=original), NOW).decision == Decision.PUBLISH


def test_valid_objection_routes_to_manual_and_invalid_objection_does_not_block():
    valid = support(5, stance=Stance.OPPOSE, value={"present": False})
    report = evaluate_case(case(support(2), support(3), support(4), valid), NOW)
    assert report.decision == Decision.MANUAL and report.objectors == (5,)
    without_photo = support(5, photo(9, status=Time.MISSING), stance=Stance.OPPOSE, value={"present": False})
    same_value = support(5, stance=Stance.OPPOSE, value=VALUE)
    for objection in (without_photo, same_value):
        assert evaluate_case(case(support(2), support(3), support(4), objection), NOW).decision == Decision.PUBLISH


def test_disabled_flag_sends_otherwise_eligible_cases_to_manual_review():
    report = evaluate_case(case(support(2), support(3), support(4), enabled=False), NOW)
    assert report.decision == Decision.MANUAL
    assert outcome(report, Code.PUBLICATION_ELIGIBILITY).reason_code == "AUTO_PUBLISH_DISABLED"
    # The flag never turns waiting into manual review by itself.
    assert evaluate_case(case(support(2), enabled=False), NOW).decision == Decision.AWAIT


def test_source_resolved_facts_never_publish_by_votes():
    report = evaluate_case(case(support(2), support(3), support(4), low=False), NOW)
    assert report.decision == Decision.MANUAL
    assert outcome(report, Code.SOURCE_APPLICABILITY).reason_code == "NO_VERIFIED_PARSER"


def published(*stances, basis=Basis.COMMUNITY_CORROBORATED, until=NOW + timedelta(days=60)):
    return case(*stances, state=State.PUBLISHED, basis=basis, until=until)


def test_published_observation_suspends_on_valid_objection_or_lost_support():
    three = (support(2), support(3), support(4))
    assert evaluate_case(published(*three), NOW).decision == Decision.KEEP
    objection = support(5, stance=Stance.OPPOSE, value={"present": False})
    assert evaluate_case(published(*three, objection), NOW).decision == Decision.SUSPEND
    assert evaluate_case(published(*three[:2]), NOW).decision == Decision.SUSPEND
    # Manual adoption is not a 3-person claim; only a valid objection suspends it.
    assert evaluate_case(published(*three[:1], basis=Basis.MANUAL_REVIEW), NOW).decision == Decision.KEEP


def test_natural_photo_ageing_never_revokes_a_publication():
    aged = (photo(1, captured=NOW - timedelta(days=80)),)
    stances = tuple(support(n, photo(100 + n, captured=NOW - timedelta(days=80))) for n in (2, 3, 4))
    assert evaluate_case(replace(published(*stances), original_photos=aged), NOW).decision == Decision.KEEP


def test_expired_and_suspended_publications_are_left_to_manual_resolution():
    objection = support(5, stance=Stance.OPPOSE, value={"present": False})
    assert evaluate_case(published(objection, until=NOW), NOW).decision == Decision.KEEP
    suspended = replace(published(objection), publication_state=State.SUSPENDED)
    assert evaluate_case(suspended, NOW).decision == Decision.KEEP


def test_case_without_any_original_upload_waits_until_corroborated():
    assert evaluate_case(case(support(2), original=()), NOW).decision == Decision.AWAIT
    report = evaluate_case(case(support(2), support(3), support(4), original=()), NOW)
    assert report.decision == Decision.MANUAL
    assert outcome(report, Code.PHOTO_TIME).reason_code == "ORIGINAL_PHOTO_MISSING"
    deleted_only = (photo(1, deleted=True),)
    assert evaluate_case(case(support(2), original=deleted_only), NOW).decision == Decision.AWAIT


def test_deleting_the_original_evidence_suspends_a_community_publication():
    three = (support(2), support(3), support(4))
    gone = replace(published(*three), original_photos=(photo(1, deleted=True),))
    assert evaluate_case(gone, NOW).decision == Decision.SUSPEND


def test_support_counts_only_for_the_proposed_value():
    contradictory = (support(2, value={"present": False}), support(3), support(4))
    assert evaluate_case(case(*contradictory), NOW).decision == Decision.AWAIT


def test_deleted_or_ineligible_hashes_still_reserve_the_image():
    deleted_original = (photo(7, deleted=True), photo(1))
    stances = (support(2, photo(7, copy=1)), support(3), support(4))
    assert evaluate_case(case(*stances, original=deleted_original), NOW).decision == Decision.AWAIT
    ineligible = (photo(1), photo(8, status=Time.MISSING))
    stances = (support(2, photo(8, copy=1)), support(3), support(4))
    assert evaluate_case(case(*stances, original=ineligible), NOW).decision == Decision.AWAIT


def test_observation_labels_never_claim_confirmed_access():
    from app.domain.community import observation_label

    assert observation_label("LIGHTING", Basis.COMMUNITY_CORROBORATED) is None
    assert observation_label("ENTRANCE_LOCATION", Basis.COMMUNITY_CORROBORATED) == "社群核實位置・通行性未確認"
    assert observation_label("ENTRANCE_LOCATION", Basis.MANUAL_REVIEW) == "人工複審採納位置・通行性未確認"


def test_suspended_author_takes_a_live_observation_back_to_review():
    three = (support(2), support(3), support(4))
    suspended = replace(published(*three), author_suspended=True)
    assert evaluate_case(suspended, NOW).decision == Decision.SUSPEND
    manual = replace(published(*three[:1], basis=Basis.MANUAL_REVIEW), author_suspended=True)
    assert evaluate_case(manual, NOW).decision == Decision.SUSPEND


def test_inactive_owner_photos_reserve_hashes_but_never_vote():
    withdrawn_copy = photo(7)
    later = (support(2, EvidencePhoto(20, withdrawn_copy.sha256, Time.VALID, RECENT)), support(3), support(4))
    report = evaluate_case(replace(case(*later), inactive_photos=(withdrawn_copy,)), NOW)
    assert report.decision == Decision.AWAIT and 20 not in report.counted_photo_ids
    # The first stored copy wins regardless of which owner is evaluated first.
    first = (support(2, photo(30)), support(3), support(4))
    stale = EvidencePhoto(40, photo(30).sha256, Time.VALID, RECENT)
    report = evaluate_case(replace(case(*first), inactive_photos=(stale,)), NOW)
    assert report.decision == Decision.PUBLISH and 30 in report.counted_photo_ids


def test_counted_photo_ids_report_actual_evidence():
    report = evaluate_case(case(support(2), support(3, photo(1, copy=1))), NOW)
    assert report.counted_photo_ids == (1, 102)


def test_conflicting_published_observation_and_unchecked_sources_go_to_manual():
    three = (support(2), support(3), support(4))
    conflict = evaluate_case(replace(case(*three), published_values=({"present": False},)), NOW)
    assert conflict.decision == Decision.MANUAL
    assert outcome(conflict, Code.SOURCE_CONFLICT).reason_code == "CONFLICTS_WITH_PUBLISHED_OBSERVATION"
    same = evaluate_case(replace(case(*three), published_values=(VALUE,)), NOW)
    assert same.decision == Decision.PUBLISH
    source = evaluate_case(case(*three, low=False), NOW)
    assert outcome(source, Code.SOURCE_CONFLICT).reason_code == "EXISTING_FACTS_NOT_COMPARED"
