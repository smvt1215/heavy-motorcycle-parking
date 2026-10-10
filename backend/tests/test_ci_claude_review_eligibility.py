"""Exercise the inline-comment query used before Claude decides a PR was reviewed."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/claude-code-review.yml"


def eligibility_query():
    workflow = (
        WORKFLOW.read_text(encoding="utf-8")
        .replace("${{ github.repository }}", "smvt1215/heavy-motorcycle-parking")
        .replace("${{ github.event.pull_request.number }}", "46")
    )
    prompt = workflow.partition("prompt: |")[2].partition("# Agent mode")[0]
    query = re.search(
        r"gh api --method GET (?P<endpoint>repos/\S+/pulls/\d+/comments) --paginate --jq '(?P<filter>[^']+)'",
        prompt,
    )
    assert query is not None, "Eligibility must query all pages of inline comments before deciding to skip"
    return workflow, query


def test_eligibility_inline_query_has_endpoint_scoped_read_permission():
    workflow, query = eligibility_query()
    tools = workflow.partition("claude_args:")[2].partition("# See")[0]
    assert query["endpoint"] == "repos/smvt1215/heavy-motorcycle-parking/pulls/46/comments"
    assert f"Bash(gh api --method GET {query['endpoint']}:*)" in tools
    assert "Bash(gh api:*)" not in tools


def inline_comment(login, comment_id=1):
    return {
        "id": comment_id,
        "user": {"login": login},
        "body": "Existing inline finding",
        "created_at": "2026-10-09T12:00:00Z",
    }


@pytest.mark.parametrize(
    "pages, expected_ids",
    [
        ([[]], []),
        ([[inline_comment("claude[bot]")]], [1]),
        ([[inline_comment("claude")]], [1]),
        ([[inline_comment("smvt1215"), inline_comment("chatgpt-codex-connector[bot]")]], []),
        ([[inline_comment("claude-helper[bot]"), {"id": 2, "user": None}]], []),
        ([[inline_comment("smvt1215")], [inline_comment("claude[bot]", 2)]], [2]),
        ([[inline_comment("claude[bot]")], [inline_comment("claude", 2)]], [1, 2]),
    ],
)
def test_eligibility_reads_prior_claude_inline_findings_across_pages(pages, expected_ids):
    _, query = eligibility_query()
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq is required by the Claude review workflow and installed on the CI runner")
    result = subprocess.run(
        [jq, "--compact-output", query["filter"]],
        input="\n".join(json.dumps(page) for page in pages),
        text=True,
        capture_output=True,
        check=True,
    )
    assert [json.loads(line)["id"] for line in result.stdout.splitlines()] == expected_ids
