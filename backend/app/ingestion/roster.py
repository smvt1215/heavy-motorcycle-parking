"""Rerunnable reconciliation of the New Taipei managed-facility roster against the offstreet feed.

The report explains every roster row. Only an exact district + full-name match
(allowing the same district prefix) whose captured ID/name/district/address
tuple still equals the live record is MATCHED; that is the same rule the policy
writer enforces at runtime. Near names are listed as leads for manual source
verification and never matched automatically.

Usage:
    python -m app.ingestion.roster [--static-file PATH] [--format markdown|json]
"""

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from app.ingestion.downloader import ParkingDownloader
from app.ingestion.new_taipei import NewTaipeiParkingAdapter
from app.ingestion.policies import MANAGED_FACILITIES
from app.ingestion.sources import NEW_TAIPEI

_SUFFIX = re.compile(r"(?:(?:平面|立體|地下|臨時|機車|公有)*停車場)$")


class RosterStatus(StrEnum):
    MATCHED = "MATCHED"
    CAPTURED_TUPLE_CHANGED = "CAPTURED_TUPLE_CHANGED"
    CAPTURED_ID_MISSING = "CAPTURED_ID_MISSING"
    EXACT_MATCH_NOT_CAPTURED = "EXACT_MATCH_NOT_CAPTURED"
    AMBIGUOUS_EXACT_NAME = "AMBIGUOUS_EXACT_NAME"
    NAME_IN_OTHER_DISTRICT = "NAME_IN_OTHER_DISTRICT"
    NAME_VARIANT_ONLY = "NAME_VARIANT_ONLY"
    NO_CANDIDATE = "NO_CANDIDATE"


@dataclass(frozen=True)
class Candidate:
    external_id: str | None
    name: str | None
    district: str | None
    address: str | None
    type_code: str | None

    @classmethod
    def of(cls, record: dict[str, Any]) -> "Candidate":
        return cls(record.get("ID"), record.get("NAME"), record.get("AREA"), record.get("ADDRESS"), record.get("TYPE"))


@dataclass(frozen=True)
class RosterFinding:
    district: str
    roster_name: str
    status: RosterStatus
    external_id: str | None = None
    candidates: tuple[Candidate, ...] = field(default=())


def _exact_names(district: str, name: str) -> set[str]:
    return {name, f"{district}{name}", f"{district.removesuffix('區')}{name}"}


def _core(district: str, name: str) -> str:
    for prefix in (district, district.removesuffix("區")):
        name = name.removeprefix(prefix)
    return _SUFFIX.sub("", name)


def reconcile(
    entries: Iterable[dict[str, Any]], facilities: Iterable[dict[str, Any]], records: Iterable[dict[str, Any]]
) -> list[RosterFinding]:
    records = [record for record in records if isinstance(record, dict)]
    by_id: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_id.setdefault(record.get("ID"), []).append(record)
    captured = {(item["district"], item["roster_name"]): item for item in facilities}
    findings = []
    for entry in entries:
        district, name = entry["district"], entry["roster_name"]
        names = _exact_names(district, name)
        exact = [r for r in records if r.get("AREA") == district and r.get("NAME") in names]
        capture = captured.get((district, name))
        if capture is not None:
            live = by_id.get(capture["external_id"], [])
            if not live:
                findings.append(RosterFinding(district, name, RosterStatus.CAPTURED_ID_MISSING, capture["external_id"]))
                continue
            expected = (capture["external_id"], capture["name"], capture["district"], capture["address"])
            if (
                len(live) == 1
                and (live[0].get("ID"), live[0].get("NAME"), live[0].get("AREA"), live[0].get("ADDRESS")) == expected
            ):
                findings.append(RosterFinding(district, name, RosterStatus.MATCHED, capture["external_id"]))
            else:
                findings.append(
                    RosterFinding(
                        district,
                        name,
                        RosterStatus.CAPTURED_TUPLE_CHANGED,
                        capture["external_id"],
                        tuple(Candidate.of(r) for r in live),
                    )
                )
            continue
        if len(exact) == 1:
            findings.append(
                RosterFinding(district, name, RosterStatus.EXACT_MATCH_NOT_CAPTURED, None, (Candidate.of(exact[0]),))
            )
            continue
        if len(exact) > 1:
            findings.append(
                RosterFinding(district, name, RosterStatus.AMBIGUOUS_EXACT_NAME, None, tuple(map(Candidate.of, exact)))
            )
            continue
        elsewhere = [r for r in records if r.get("NAME") in names]
        if elsewhere:
            findings.append(
                RosterFinding(
                    district, name, RosterStatus.NAME_IN_OTHER_DISTRICT, None, tuple(map(Candidate.of, elsewhere))
                )
            )
            continue
        core = _core(district, name)
        # A roster name that already starts with the short district name may appear with the full district.
        short = district.removesuffix("區")
        expanded = f"{district}{name.removeprefix(short)}" if name.startswith(short) else None
        variants = [
            r
            for r in records
            if r.get("AREA") == district
            and ((core and core in (r.get("NAME") or "")) or (expanded is not None and r.get("NAME") == expanded))
        ]
        status = RosterStatus.NAME_VARIANT_ONLY if variants else RosterStatus.NO_CANDIDATE
        findings.append(RosterFinding(district, name, status, None, tuple(map(Candidate.of, variants))))
    return findings


def markdown(findings: list[RosterFinding]) -> str:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.status] = counts.get(finding.status, 0) + 1
    lines = [
        "| 狀態 | 筆數 |",
        "| --- | --- |",
        *(f"| `{status}` | {count} |" for status, count in sorted(counts.items())),
        "",
        "| 行政區 | 名冊名稱 | 狀態 | 候選（ID 名稱／TYPE；僅供人工查證） |",
        "| --- | --- | --- | --- |",
    ]
    for finding in findings:
        if finding.status == RosterStatus.MATCHED:
            continue
        leads = "；".join(f"{c.external_id} {c.name}／{c.type_code}" for c in finding.candidates) or "—"
        lines.append(f"| {finding.district} | {finding.roster_name} | `{finding.status}` | {leads} |")
    return "\n".join(lines) + "\n"


async def _live_records() -> list[dict[str, Any]]:
    async with httpx.AsyncClient() as client:
        download = await ParkingDownloader(client).fetch(NEW_TAIPEI.feeds["static"])
    return NewTaipeiParkingAdapter().records(download.payload)


def main(argv: list[str] | None = None) -> None:
    options = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    options.add_argument("--static-file", type=Path, help="Replay a captured offstreet snapshot instead of fetching")
    options.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = options.parse_args(argv)
    if args.static_file:
        records = NewTaipeiParkingAdapter().records(json.loads(args.static_file.read_text(encoding="utf-8")))
    else:
        records = asyncio.run(_live_records())
    findings = reconcile(MANAGED_FACILITIES["roster_entries"], MANAGED_FACILITIES["facilities"], records)
    if args.format == "json":
        json.dump([asdict(finding) for finding in findings], sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(markdown(findings))


if __name__ == "__main__":
    main()
