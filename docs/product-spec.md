# 重機停車通 Product Specification v1.2

## Goal
讓普通重型及大型重型機車使用者快速確認：附近哪裡可合法停、停哪種格、現在有沒有位、多少錢、入口在哪裡。

## MVP region
台北市、新北市。

## Core flow
定位或搜尋目的地 → 查詢附近停車 → 依車種合法性過濾 → 顯示距離/空位/費率/來源 → 查看停車區與入口 → Apple Maps / Google Maps 外部導航。

## Parking space types
- `HEAVY_ONLY`: 大重專用；普重依明確規則判斷。
- `MOTO_SHARED`: 普重／大重共用；以分類級許可為準。
- `CAR_SHARED`: 汽車共享區；確認大重許可時納入，普重依明確規則判斷。
- `LIGHT_MOTO_ONLY`: 一般機車區；確認普重適用時納入，大重正常搜尋排除。

Four-type classification is descriptive, not the only authority. Explicit per-vehicle tri-state permissions remain authoritative when present.

## Vehicle types
`NORMAL_HEAVY`（普重／普通重型機車）、`LARGE_HEAVY`（大重／大型重型機車，包含黃牌及紅牌）。未設定偏好預設大重；每次查詢明確傳入 vehicle。舊公開值全部回傳 422。

## Compatibility states
Vehicle compatibility is tri-state and must remain explicit end-to-end:
- `ALLOWED`: confirmed allowed for the selected vehicle.
- `NOT_ALLOWED`: confirmed not allowed.
- `UNKNOWN`: permission cannot currently be verified.

MVP behavior for both rider classes:
- Normal map/search results include `ALLOWED` parking only.
- `NOT_ALLOWED` parking is excluded from normal results.
- `LIGHT_MOTO_ONLY` is available to NORMAL_HEAVY only when applicable permission is confirmed; it is excluded from LARGE_HEAVY search.
- `UNKNOWN` parking is excluded by default, but may be displayed when the user enables an explicit `顯示未確認停車位置` option.
- When shown, `UNKNOWN` locations must be labeled `尚未確認` and must never use the same visual or wording as confirmed legal parking.
- `UNKNOWN` must never be silently converted to `NOT_ALLOWED` or `ALLOWED`.

The unverified-location option exists to support discovery and community correction without misleading riders about legality.

A future feature for intentionally browsing known prohibited/non-parking locations, if ever needed, must be specified as a separate discovery mode and must not be mixed into legal parking search/ranking.

## Required parking information
- 合法性與 reason
- Parking zone / space type
- 距離
- realtime available / total / freshness
- 停車費率與每日上限
- 停車場入口
- 資料來源
- 最後更新時間

## Rate model
Support `FREE`, `HOURLY`, `PER_ENTRY`, `TIME_BLOCK`, `PROGRESSIVE`, `FLAT`, `DAILY`, `MONTHLY`, `CUSTOM`.
Must handle free minutes, progressive rules, weekday/weekend/holiday periods, day/night periods, daily caps, and heavy motorcycles charged by car rate. Preserve raw rate text when parsing is incomplete.

## Ranking policy
Legality/applicability is a hard filter before ranking. A cheaper, closer, or apparently available option must never outrank a confirmed legal option by using facts from a `NOT_ALLOWED` or compatibility-`UNKNOWN` zone.

For confirmed `ALLOWED` lots, v1 weighted ranking components are:
- Distance: 35%
- Availability: 25%
- Price: 20%
- Confidence/provenance quality: 15%
- Entrance quality: 5%

The backend normalizes these components deterministically and versions scoring semantics. Exact ordering/cursor mechanics are defined in `docs/api.md`.

When `include_unknown=true`:
- confirmed `ALLOWED` lots remain the first result group;
- an `UNKNOWN` child zone cannot improve or worsen an ALLOWED lot's score;
- unknown-only lots form a separate unverified group after confirmed results and are ordered by distance plus a stable ID tie-breaker;
- unknown-only results do not receive confirmed availability/price/confidence boosts.

No AI/LLM ranking is used in MVP.

## Community verification (planned)
Riders can report and corroborate structured observations with photos; see the [plan](community-verification-plan.md) and [contract](community-verification-contract.md). Three independent corroborators can publish only low-risk observations (lighting, rain cover, charging, entrance location) for 90 days, labelled separately from official data. Votes never confirm parking permission, rates, realtime availability or entrance access; those require a verified source or a manual decision and keep the precedence rules above. Community observations do not affect ranking, `available_only`, rate filters or default navigation. Photos stay private and there is no public leaderboard or social feed.

## MVP non-goals
No in-app turn-by-turn navigation, payment, monthly-rental marketplace, social feed/chat, AI ranking, CarPlay, Android Auto, or prohibited-location discovery mode.

## Definition of Done
User selects LARGE_HEAVY, searches `台北101`, sees confirmed applicable heavy-motorcycle parking options, compares distance/availability/rates, opens details with source and entrance, then launches Apple Maps or Google Maps to the entrance. If the user explicitly enables unverified locations, any returned `UNKNOWN` result is clearly labeled and cannot be mistaken for confirmed legal parking.
