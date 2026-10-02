# 重機停車通 Product Specification v1.2

## Goal
讓黃牌／紅牌大型重型機車使用者快速確認：附近哪裡可合法停、停哪種格、現在有沒有位、多少錢、入口在哪裡。

## MVP region
台北市、新北市。

## Core flow
定位或搜尋目的地 → 查詢附近停車 → 依車種合法性過濾 → 顯示距離/空位/費率/來源 → 查看停車區與入口 → Apple Maps / Google Maps 外部導航。

## Parking space types
- `HEAVY_ONLY`: 黃/紅牌可停；綠/白牌、汽車不可停。
- `MOTO_SHARED`: 黃/紅/綠/白牌可停；汽車不可停。
- `CAR_SHARED`: 黃/紅牌與汽車可停；綠/白牌不可停。
- `LIGHT_MOTO_ONLY`: 僅綠/白牌；對黃/紅牌屬已確認 `NOT_ALLOWED`，不屬於正常停車搜尋結果。

Four-type classification is descriptive, not the only authority. Explicit per-vehicle tri-state permissions remain authoritative when present.

## Vehicle types
`GREEN`, `WHITE`, `YELLOW`, `RED`, `CAR`.

## Compatibility states
Vehicle compatibility is tri-state and must remain explicit end-to-end:
- `ALLOWED`: confirmed allowed for the selected vehicle.
- `NOT_ALLOWED`: confirmed not allowed.
- `UNKNOWN`: permission cannot currently be verified.

MVP behavior for yellow/red users:
- Normal map/search results include `ALLOWED` parking only.
- `NOT_ALLOWED` parking is excluded from normal results.
- `LIGHT_MOTO_ONLY` is therefore not offered as a normal YELLOW/RED parking-search filter.
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

## MVP non-goals
No in-app turn-by-turn navigation, payment, monthly-rental marketplace, social feed/chat, AI ranking, CarPlay, Android Auto, or prohibited-location discovery mode.

## Definition of Done
User selects RED, searches `台北101`, sees confirmed applicable heavy-motorcycle parking options, compares distance/availability/rates, opens details with source and entrance, then launches Apple Maps or Google Maps to the entrance. If the user explicitly enables unverified locations, any returned `UNKNOWN` result is clearly labeled and cannot be mistaken for confirmed legal parking.
