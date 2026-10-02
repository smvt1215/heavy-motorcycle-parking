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
- `LIGHT_MOTO_ONLY`: 僅綠/白牌；黃/紅牌預設搜尋隱藏。

Four-type classification is descriptive, not the only authority. Explicit per-vehicle tri-state permissions remain authoritative when present.

## Vehicle types
`GREEN`, `WHITE`, `YELLOW`, `RED`, `CAR`.

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
No in-app turn-by-turn navigation, payment, monthly-rental marketplace, social feed/chat, AI ranking, CarPlay, or Android Auto.

## Definition of Done
User selects RED, searches `台北101`, sees only applicable heavy-motorcycle parking options, compares distance/availability/rates, opens details with source and entrance, then launches Apple Maps or Google Maps to the entrance.
