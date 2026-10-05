import pytest

from app.ingestion.cli import parser, run


@pytest.mark.parametrize(
    "args",
    [
        ["--feed", "static", "--static-file", "x.json"],
        ["--static-file", "x.json", "--fetched-at", "2026-10-05T16:02:01Z"],
        ["--feed", "static", "--static-file", "x.json", "--fetched-at", "2026-10-05T16:02:01"],
        ["--feed", "realtime", "--static-file", "x.json", "--fetched-at", "2026-10-05T16:02:01Z"],
    ],
)
async def test_replay_requires_absolute_time_and_all_selected_files(args):
    with pytest.raises(ValueError):
        await run(parser().parse_args(args))
