"""Decide whether the Claude review workflow run actually produced a review.

Inputs are JSON files so the decision is testable (backend/tests/test_ci_verify_claude_review.py):
    verify_claude_review.py EXECUTION_FILE ISSUE_COMMENTS_JSON REVIEW_COMMENTS_JSON STARTED_AT

The run passes only when, without any denied tool call, Claude posted a review in this
run: a "## Code review" summary comment or inline review comments. An explicit
"Claude review skipped:" comment, or nothing at all, fails the check, because no review
happened. Output stays minimal for public logs: denied tool names, command prefixes and
the first line of a skip comment.
"""

import json
import sys

CLAUDE = {"claude[bot]", "claude"}
SKIP_PREFIX = "Claude review skipped:"
REVIEW_HEADING = "## Code review"


def _new_by_claude(comments: list[dict], started_at: str) -> list[dict]:
    return [
        c
        for c in comments
        if (c.get("user") or {}).get("login") in CLAUDE
        and max(c.get("created_at") or "", c.get("updated_at") or "") >= started_at
    ]


def verify(execution: list, issue_comments: list[dict], review_comments: list[dict], started_at: str):
    """Return (ok, messages). Messages use GitHub workflow-command syntax."""
    denials = [
        denial
        for message in execution
        if isinstance(message, dict) and message.get("type") == "result"
        for denial in message.get("permission_denials") or []
    ]
    if denials:
        lines = [f"::error::Claude review was denied {len(denials)} tool call(s); the review is incomplete."]
        for denial in denials:
            command = ((denial.get("tool_input") or {}).get("command") or "")[:80]
            lines.append(f"- {denial.get('tool_name')}" + (f": {command}" if command else ""))
        return False, lines
    issue = _new_by_claude(issue_comments, started_at)
    skips = [c for c in issue if (c.get("body") or "").startswith(SKIP_PREFIX)]
    if skips:
        reason = (skips[0].get("body") or "").splitlines()[0][:300]
        return False, [f"::error::No review happened: {reason}"]
    summaries = [c for c in issue if REVIEW_HEADING in (c.get("body") or "")]
    inline = _new_by_claude(review_comments, started_at)
    if not summaries and not inline:
        return False, [
            "::error::Claude posted no review in this run (no summary, inline findings or skip reason). "
            "Comment '@claude review this PR' to request one."
        ]
    return True, [f"Claude review posted {len(summaries)} summary and {len(inline)} inline comment(s) in this run."]


def main(argv: list[str]) -> int:
    execution_path, issue_path, review_path, started_at = argv[1:5]
    with open(execution_path, encoding="utf-8") as handle:
        execution = json.load(handle)
    with open(issue_path, encoding="utf-8") as handle:
        issue_comments = json.load(handle)
    with open(review_path, encoding="utf-8") as handle:
        review_comments = json.load(handle)
    ok, messages = verify(execution, issue_comments, review_comments, started_at)
    print("\n".join(messages))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
