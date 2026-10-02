# Design System

## Shared principles
Platform-adaptive UI with shared business logic and content. Do not require pixel-identical iOS/Android screens.
Parking state must use icon/shape/text plus semantic color; never color alone.
Support System / Light / Dark from MVP.

## Map markers
Represent `HEAVY_ONLY`, `MOTO_SHARED`, `CAR_SHARED`, `LIGHT_MOTO_ONLY` distinctly. Hide `LIGHT_MOTO_ONLY` by default for YELLOW/RED users. Use clustering.

## Map interaction
Do not call backend on every camera frame. On camera idle, show `搜尋此區域` or use a controlled debounce.

## iOS
Follow current Apple HIG. Use Liquid Glass selectively for navigation/search/tab/floating/transient controls, not every content card. Support Dynamic Type, VoiceOver, Reduce Motion, Increase Contrast; minimum target 44pt. Native Swift/UIKit bridge is allowed only when required for system-native behavior Flutter cannot provide cleanly.

## Android
Follow Android 16 / Material 3 Expressive direction: Dynamic Color, edge-to-edge, SearchBar, filter chips, bottom sheets, navigation patterns, TalkBack, font scaling, reduced animation; minimum target 48dp.

## Core map filters
Vehicle YELLOW/RED; availability; rate; more. Advanced filters include space types, show unavailable/light-moto-only, hourly price bands, daily cap requirement, 500m/1km/3km/5km radius.

## Parking summary
Show name, distance, parking type, selected-vehicle compatibility, available/total, main rate, daily max, source/freshness, and navigation/detail CTA.
