"""Managed-facility roster reconciliation: exact rule only; near names stay leads."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.ingestion import roster
from app.ingestion.policies import MANAGED_FACILITIES
from app.ingestion.roster import RosterStatus, reconcile

FIXTURES = Path(__file__).parent / "fixtures/new_taipei"
REAL = json.loads((FIXTURES / "offstreet_roster_real.json").read_text())
ENTRIES = MANAGED_FACILITIES["roster_entries"]
FACILITIES = MANAGED_FACILITIES["facilities"]


def entry(district, name):
    return next(e for e in ENTRIES if (e["district"], e["roster_name"]) == (district, name))


def status_of(findings, district, name):
    return next(f for f in findings if (f.district, f.roster_name) == (district, name))


def test_roster_evidence_covers_all_71_rows_and_every_capture():
    assert len(ENTRIES) == 71
    assert len({(e["district"], e["roster_name"]) for e in ENTRIES}) == 71
    assert len(FACILITIES) == 55
    keys = {(e["district"], e["roster_name"]) for e in ENTRIES}
    assert all((f["district"], f["roster_name"]) in keys for f in FACILITIES)
    # Free-text space notes are retained verbatim and never coerced into counts.
    texts = {e["large_heavy_space_text"] for e in ENTRIES}
    assert "2格汽車格位優先重機停放" in texts


def test_real_records_reconcile_by_exact_rule_only():
    findings = {(f.district, f.roster_name): f for f in reconcile(ENTRIES, FACILITIES, REAL)}
    assert findings[("板橋區", "四維公園地下停車場")].status == RosterStatus.MATCHED
    assert findings[("板橋區", "四維公園地下停車場")].external_id == "010152"
    assert findings[("板橋區", "市民廣場地下停車場")].status == RosterStatus.MATCHED
    for key, lead in (
        (("泰山區", "楓樹腳地下停車場"), "160004"),
        (("中和區", "大仁段停車場"), "030175"),
        (("瑞芳區", "瑞芳立體停車場"), "120002"),
        (("五股區", "五股工商立體停車場"), "150010"),
        (("板橋區", "華東平面停車場"), "010024"),
    ):
        finding = findings[key]
        assert finding.status == RosterStatus.NAME_VARIANT_ONLY, key
        assert finding.external_id is None
        assert lead in {c.external_id for c in finding.candidates}
    assert {c.external_id for c in findings[("三重區", "忠孝路平面停車場")].candidates} == {"020039", "020180"}
    # Captured facilities whose record is absent from this partial fixture are reported, not silently matched.
    assert findings[("八里區", "渡船頭平面停車場")].status == RosterStatus.CAPTURED_ID_MISSING
    assert findings[("三重區", "碧華國小地下停車場")].status == RosterStatus.NO_CANDIDATE


@pytest.mark.parametrize("field", ["NAME", "AREA", "ADDRESS"])
def test_changed_captured_tuple_is_not_matched(field):
    record = next(r for r in REAL if r["ID"] == "010152")
    changed = [{**record, field: record[field] + "（變更）"}]
    (finding,) = reconcile([entry("板橋區", "四維公園地下停車場")], FACILITIES, changed)
    assert finding.status == RosterStatus.CAPTURED_TUPLE_CHANGED
    assert finding.external_id == "010152"


def test_duplicate_live_id_is_not_matched():
    record = next(r for r in REAL if r["ID"] == "010152")
    (finding,) = reconcile([entry("板橋區", "四維公園地下停車場")], FACILITIES, [record, record])
    assert finding.status == RosterStatus.CAPTURED_TUPLE_CHANGED


def test_uncaptured_exact_ambiguous_and_other_district_names():
    target = {"district": "中和區", "roster_name": "大仁段停車場", "large_heavy_space_text": "8"}
    base = next(r for r in REAL if r["ID"] == "030175")
    exact = {**base, "NAME": "中和大仁段停車場"}
    (finding,) = reconcile([target], [], [exact])
    assert finding.status == RosterStatus.EXACT_MATCH_NOT_CAPTURED
    (finding,) = reconcile([target], [], [exact, {**exact, "ID": "030999"}])
    assert finding.status == RosterStatus.AMBIGUOUS_EXACT_NAME
    (finding,) = reconcile([target], [], [{**exact, "NAME": "大仁段停車場", "AREA": "永和區"}])
    assert finding.status == RosterStatus.NAME_IN_OTHER_DISTRICT


def test_cli_replays_a_captured_snapshot(tmp_path, capsys):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"page_size": 1000, "pages": [REAL]}, ensure_ascii=False), encoding="utf-8")
    roster.main(["--static-file", str(snapshot), "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    assert len(report) == 71
    assert {row["status"] for row in report} >= {"MATCHED", "NAME_VARIANT_ONLY", "NO_CANDIDATE", "CAPTURED_ID_MISSING"}
    roster.main(["--static-file", str(snapshot)])
    assert "| `MATCHED` | 2 |" in capsys.readouterr().out


def test_findings_are_immutable():
    (finding,) = reconcile([entry("板橋區", "四維公園地下停車場")], FACILITIES, REAL)
    with pytest.raises(AttributeError):
        finding.status = RosterStatus.NO_CANDIDATE
    assert replace(finding).status == RosterStatus.MATCHED
