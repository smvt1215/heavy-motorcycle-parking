# Design System

## Shared principles
Platform-adaptive UI with shared business logic and content. Do not require pixel-identical iOS/Android screens.
Parking state must use icon/shape/text plus semantic color; never color alone.
Support System / Light / Dark from MVP.

## Implemented tokens (M9)
- Brand: Taiwan parking-sign blue `#0B5CAD` (Material seed and the "P" mark).
  Android 12+ uses Dynamic Color instead; the P mark stays sign blue.
- Plate selector: yellow `#F2B705` / red `#C8102E` swatches, always beside 黃牌 / 紅牌.
- Status badge: filled + check = 可停放, outlined + ? = 尚未確認, outlined + block =
  不可停. Result tiles: filled = confirmed, outlined = unverified.
- System fonts per platform; distances, counts and prices use tabular figures.
- Search-first header: P mark, destination field, account. iOS uses a translucent
  floating field and `CupertinoSlidingSegmentedControl`; Android uses the M3 search
  bar shape, `SegmentedButton` and a FAB.
- Minimum target 48dp; high-contrast themes; Reduce Motion disables camera
  animation and translucency. See [accessibility audit](accessibility-audit.md).

## Map markers
Represent `HEAVY_ONLY`, `MOTO_SHARED`, `CAR_SHARED`, `LIGHT_MOTO_ONLY` distinctly when those types are surfaced in an appropriate context. For LARGE_HEAVY parking search, `LIGHT_MOTO_ONLY` is `NOT_ALLOWED` and therefore remains excluded from normal parking results. Use clustering.

## Map interaction
Do not call backend on every camera frame. On camera idle, show `搜尋此區域` or use a controlled debounce.

## iOS
Follow current Apple HIG. Use Liquid Glass selectively for navigation/search/tab/floating/transient controls, not every content card. Support Dynamic Type, VoiceOver, Reduce Motion, Increase Contrast; minimum target 44pt. Native Swift/UIKit bridge is allowed only when required for system-native behavior Flutter cannot provide cleanly.

## Android
Follow Android 16 / Material 3 Expressive direction: Dynamic Color, edge-to-edge, SearchBar, filter chips, bottom sheets, navigation patterns, TalkBack, font scaling, reduced animation; minimum target 48dp.

## Core map filters
Vehicle NORMAL_HEAVY/LARGE_HEAVY; confirmed availability; confirmed hourly rate; more.

Advanced parking-search filters include:
- compatible space types (`HEAVY_ONLY`, `MOTO_SHARED`, `CAR_SHARED`)
- `顯示未確認停車位置` mapped to `include_unknown=true`
- confirmed hourly price bands
- confirmed daily-cap requirement
- 500m/1km/3km/5km radius

Do **not** offer `LIGHT_MOTO_ONLY` or a generic `顯示不可停位置` control as a normal LARGE_HEAVY parking-search filter. Known `NOT_ALLOWED` locations remain excluded by the v1 parking API. A future feature for intentionally browsing prohibited/non-parking locations, if needed, must be a separately specified discovery mode and must not be mixed with legal parking results.

## Parking summary
Show name, distance, parking type, selected-vehicle compatibility, available/total, main rate, daily max, source/freshness, and navigation/detail CTA.

When compatibility is `UNKNOWN`, label it as unverified and never style it as confirmed legal parking.

Vehicle controls display 普重 / 大重 with the formal names 普通重型機車 / 大型重型機車（黃牌／紅牌） via explanation, tooltip and semantics. Default is 大重. NORMAL_HEAVY offers confirmed conventional motorcycle zones, including LIGHT_MOTO_ONLY.
