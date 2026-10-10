"""Explicit per-city source/authority policy; provenance is independent per feed."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FeedPolicy:
    kind: str
    code: str
    name: str
    url: str
    freshness_seconds: int
    freshness_uses_source_timestamp: bool = False
    # Paged sources: `page`/`size` query parameters until a short page. None = single document.
    page_size: int | None = None
    max_pages: int = 1
    # Static only: skip retiring absent lots when a snapshot has fewer records than this
    # fraction of the last fully reconciled snapshot. None keeps complete-snapshot semantics.
    reconcile_min_ratio: float | None = None


@dataclass(frozen=True)
class CitySource:
    key: str
    city_name: str
    dataset_url: str
    license_url: str
    attribution: str
    lock_key: int
    rule_note: str
    feeds: dict[str, FeedPolicy] = field(default_factory=dict)
    authority_priority: int = 100

    def identity_prefix(self) -> str:
        return self.key


# --- Taipei (M4) -------------------------------------------------------------------------

DATASET_URL = "https://data.taipei/dataset/detail?id=d5c0656b-5250-4179-a491-c94daa56ef2c"
LICENSE_URL = "https://data.taipei/rule"
ATTRIBUTION = "臺北市政府交通局停車管理工程處 2026 臺北市停車場資訊 V2（政府資料開放授權條款第1版）"
AUTHORITY_PRIORITY = 100
TAIPEI_INGESTION_LOCK = 7400401

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

TAIPEI = CitySource(
    key="taipei",
    city_name="臺北市",
    dataset_url=DATASET_URL,
    license_url=LICENSE_URL,
    attribution=ATTRIBUTION,
    lock_key=TAIPEI_INGESTION_LOCK,
    rule_note="Taipei V2 explicit category counts; generic car/motor counts do not confirm heavy permission",
    feeds=FEEDS,
    authority_priority=AUTHORITY_PRIORITY,
)

# --- New Taipei (M7) ---------------------------------------------------------------------

NEW_TAIPEI_STATIC_DATASET = "B1464EF0-9C7C-4A6F-ABF7-6BDF32847E68"
NEW_TAIPEI_REALTIME_DATASET = "E09B35A5-A738-48CC-B0F5-570B67AD9C78"
NEW_TAIPEI_LICENSE_URL = "https://data.gov.tw/license"
NEW_TAIPEI_INGESTION_LOCK = 7400402
NEW_TAIPEI_PAGE_SIZE = 1000

NEW_TAIPEI_STATIC = FeedPolicy(
    "static",
    "NEW_TAIPEI_STATIC",
    "新北市路外公共停車場資訊",
    f"https://data.ntpc.gov.tw/api/datasets/{NEW_TAIPEI_STATIC_DATASET}/json",
    86400,
    page_size=NEW_TAIPEI_PAGE_SIZE,
    max_pages=20,
    reconcile_min_ratio=0.8,
)
NEW_TAIPEI_REALTIME = FeedPolicy(
    "realtime",
    "NEW_TAIPEI_REALTIME",
    "新北市公有路外停車場即時賸餘車位數",
    f"https://data.ntpc.gov.tw/api/datasets/{NEW_TAIPEI_REALTIME_DATASET}/json",
    # The feed has no upstream update time, so freshness can only follow fetch time.
    180,
    page_size=NEW_TAIPEI_PAGE_SIZE,
    max_pages=20,
)

NEW_TAIPEI = CitySource(
    key="new_taipei",
    city_name="新北市",
    dataset_url=f"https://data.ntpc.gov.tw/datasets/{NEW_TAIPEI_STATIC_DATASET}",
    license_url=NEW_TAIPEI_LICENSE_URL,
    attribution="新北市政府 新北市路外公共停車場資訊、新北市公有路外停車場即時賸餘車位數（政府資料開放授權條款第1版）",
    lock_key=NEW_TAIPEI_INGESTION_LOCK,
    rule_note="New Taipei explicit car/motor counts; fees never confirm heavy permission",
    feeds={policy.kind: policy for policy in (NEW_TAIPEI_STATIC, NEW_TAIPEI_REALTIME)},
)

NEW_TAIPEI_ROADSIDE_DATASET = "54a507c4-c038-41b5-bf60-bbecb9d052c6"
NEW_TAIPEI_ROADSIDE_FEEDS = {
    kind: FeedPolicy(
        kind,
        f"NEW_TAIPEI_ROADSIDE_{kind.upper()}",
        f"新北市路邊停車位資訊（{kind}）",
        f"https://data.ntpc.gov.tw/api/datasets/{NEW_TAIPEI_ROADSIDE_DATASET}/json",
        180 if kind == "realtime" else 86400,
        page_size=1000,
        max_pages=100,
        reconcile_min_ratio=0.8 if kind == "static" else None,
    )
    for kind in ("static", "realtime")
}
NEW_TAIPEI_ROADSIDE_SOURCE = CitySource(
    key="new_taipei_roadside",
    city_name="新北市",
    dataset_url="https://data.gov.tw/dataset/122901",
    license_url=NEW_TAIPEI_LICENSE_URL,
    attribution="新北市政府交通局 新北市路邊停車位資訊（政府資料開放授權條款第1版）",
    lock_key=7400403,
    rule_note="Exact cell category only; undocumented status codes remain UNKNOWN",
    feeds=NEW_TAIPEI_ROADSIDE_FEEDS,
)

CITY_SOURCES = {source.key: source for source in (TAIPEI, NEW_TAIPEI, NEW_TAIPEI_ROADSIDE_SOURCE)}
