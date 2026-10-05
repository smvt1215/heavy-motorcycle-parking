"""Explicit source/authority policy; provenance is independent per feed."""

from dataclasses import dataclass

DATASET_URL = "https://data.taipei/dataset/detail?id=d5c0656b-5250-4179-a491-c94daa56ef2c"
LICENSE_URL = "https://data.taipei/rule"
ATTRIBUTION = "臺北市政府交通局停車管理工程處 2026 臺北市停車場資訊 V2（政府資料開放授權條款第1版）"
AUTHORITY_PRIORITY = 100
TAIPEI_INGESTION_LOCK = 7400401


@dataclass(frozen=True)
class FeedPolicy:
    kind: str
    code: str
    name: str
    url: str
    freshness_seconds: int
    freshness_uses_source_timestamp: bool = False


STATIC = FeedPolicy(
    "static",
    "TAIPEI_STATIC_V2",
    "臺北市停車場資訊 V2",
    "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.json",
    86400,
)
REALTIME = FeedPolicy(
    "realtime",
    "TAIPEI_REALTIME_V2",
    "臺北市剩餘停車位數 V2",
    "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.json",
    120,
    True,
)
FEEDS = {policy.kind: policy for policy in (STATIC, REALTIME)}
