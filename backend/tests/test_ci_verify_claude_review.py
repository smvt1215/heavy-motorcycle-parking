"""The Claude review workflow passes only when this run actually posted a review."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/verify_claude_review.py"
spec = importlib.util.spec_from_file_location("verify_claude_review", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)

STARTED = "2026-10-10T12:00:00Z"
CLEAN = [{"type": "system"}, {"type": "result", "permission_denials": []}]


def comment(body, login="claude[bot]", created="2026-10-10T12:05:00Z", updated=None):
    return {"user": {"login": login}, "body": body, "created_at": created, "updated_at": updated or created}


def run(execution=CLEAN, issue=(), review=()):
    return verifier.verify(list(execution), list(issue), list(review), STARTED)


def test_new_summary_review_passes():
    ok, messages = run(issue=[comment("## Code review\n\nNo issues found.")])
    assert ok and "1 summary" in messages[0]


def test_new_inline_findings_pass():
    ok, messages = run(review=[comment("**P1** something", created="2026-10-10T12:01:00Z")])
    assert ok and "1 inline" in messages[0]


def test_updated_old_review_counts_for_this_run():
    ok, _ = run(issue=[comment("## Code review", created="2026-10-10T10:00:00Z", updated="2026-10-10T12:02:00Z")])
    assert ok


@pytest.mark.parametrize(
    "issue, review",
    [
        ([], []),
        ([comment("## Code review", created="2026-10-10T11:59:59Z")], []),  # earlier run
        ([comment("## Code review", login="smvt1215")], []),  # not Claude
        ([comment("Claude finished @smvt1215's task in 47s")], []),  # @claude task reply
        ([], [comment("finding", login="chatgpt-codex-connector[bot]")]),
    ],
)
def test_no_review_from_this_run_fails(issue, review):
    ok, messages = run(issue=issue, review=review)
    assert not ok and "posted no review" in messages[0]


def test_explicit_skip_fails_with_its_reason():
    ok, messages = run(issue=[comment("Claude review skipped: the PR is closed\n\ndetails")])
    assert not ok and messages == ["::error::No review happened: Claude review skipped: the PR is closed"]


def test_skip_from_other_accounts_or_runs_is_ignored():
    ok, _ = run(
        issue=[
            comment("Claude review skipped: spoof", login="someone"),
            comment("Claude review skipped: old", created="2026-10-10T11:00:00Z"),
            comment("## Code review\n\nNo issues found."),
        ]
    )
    assert ok


def test_denied_tools_fail_and_print_only_names_and_command_prefixes():
    execution = [
        {
            "type": "result",
            "permission_denials": [
                {"tool_name": "Bash", "tool_input": {"command": "gh pr list " + "x" * 200}},
                {"tool_name": "Task", "tool_input": {"prompt": "private prompt"}},
            ],
        }
    ]
    ok, messages = run(execution=execution, issue=[comment("## Code review")])
    assert not ok and "denied 2 tool call(s)" in messages[0]
    assert messages[1] == "- Bash: " + ("gh pr list " + "x" * 200)[:80]
    assert messages[2] == "- Task" and "private prompt" not in "\n".join(messages)


def test_command_line_entry_point(tmp_path):
    paths = []
    for name, data in (("e", CLEAN), ("i", [comment("## Code review")]), ("r", [])):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data))
        paths.append(str(path))
    assert verifier.main(["verify", *paths, STARTED]) == 0
    (tmp_path / "i.json").write_text("[]")
    assert verifier.main(["verify", *paths, STARTED]) == 1
