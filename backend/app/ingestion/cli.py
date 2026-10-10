"""One-shot city parking worker; a scheduler can invoke it without API/router coupling."""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.cache import get_redis
from app.db import get_db_engine, get_sessionmaker
from app.ingestion.contracts import FeedSnapshot
from app.ingestion.downloader import DownloadError, ParkingDownloader, decode_document
from app.ingestion.new_taipei import NewTaipeiParkingAdapter
from app.ingestion.new_taipei_roadside import NewTaipeiRoadsideAdapter
from app.ingestion.pipeline import ParkingIngestionPipeline
from app.ingestion.taipei import TaipeiParkingAdapter

ADAPTERS = {
    "taipei": TaipeiParkingAdapter,
    "new_taipei": NewTaipeiParkingAdapter,
    "new_taipei_roadside": NewTaipeiRoadsideAdapter,
}


def parser() -> argparse.ArgumentParser:
    arguments = argparse.ArgumentParser(description="Import city parking feeds with retained raw evidence")
    arguments.add_argument("--city", choices=tuple(ADAPTERS), default="taipei")
    arguments.add_argument("--feed", choices=("static", "realtime", "all"), default="all")
    arguments.add_argument("--static-file", type=Path)
    arguments.add_argument("--realtime-file", type=Path)
    arguments.add_argument("--fetched-at", help="Required absolute RFC3339 fetch instant when replaying files")
    arguments.add_argument("--no-cache", action="store_true", help="Skip the Redis import-status cache")
    return arguments


async def run(options: argparse.Namespace) -> int:
    fetched_at = None
    if options.static_file or options.realtime_file:
        if not options.fetched_at:
            raise ValueError("File replay requires --fetched-at; old files must not appear freshly fetched")
        fetched_at = datetime.fromisoformat(options.fetched_at.replace("Z", "+00:00"))
        if fetched_at.utcoffset() is None:
            raise ValueError("--fetched-at requires an explicit timezone")
        fetched_at = fetched_at.astimezone(UTC)
    feeds = ("static", "realtime") if options.feed == "all" else (options.feed,)
    files = {kind for kind in ("static", "realtime") if getattr(options, f"{kind}_file") is not None}
    if files and files != set(feeds):
        raise ValueError(
            "File replay requires a file for every selected feed and must not mix replay with live downloads"
        )
    adapter, cache = ADAPTERS[options.city](), None if options.no_cache else get_redis()
    exit_code = 0
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            downloader = ParkingDownloader(client)
            for kind in feeds:
                async with get_sessionmaker()() as session:
                    pipeline = ParkingIngestionPipeline(session, adapter, cache=cache)
                    path = getattr(options, f"{kind}_file")
                    if path is not None:
                        try:
                            # A .csv replay is decoded exactly like the live CSV document.
                            document_format = "csv" if path.suffix.lower() == ".csv" else "json"
                            payload = decode_document(path.read_bytes(), fetched_at, document_format)
                        except DownloadError as exc:
                            result = await pipeline.record_download_failure(kind, exc)
                        else:
                            result = await pipeline.ingest(
                                FeedSnapshot(kind, payload, fetched_at, adapter.source_updated_at(payload))
                            )
                    else:
                        result = await pipeline.download_and_ingest(kind, downloader)
                    print(json.dumps(asdict(result), ensure_ascii=False))
                    if result.failed or result.status == "FAILED":
                        exit_code = 1
    finally:
        if cache is not None:
            await cache.aclose()
        await get_db_engine().dispose()
    return exit_code


def main() -> None:
    arguments = parser()
    options = arguments.parse_args()
    try:
        code = asyncio.run(run(options))
    except (ValueError, OSError) as exc:
        arguments.exit(2, f"Import error: {exc}\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
