# Accessibility audit: core MVP flow (M9)

Flow audited: open map → choose YELLOW/RED → filter → search destination
(`台北101`) → open parking detail → favorite / report → external navigation.
"Automated" items run in `mobile/test/accessibility_test.dart` and
`mobile/test/appearance_test.dart` on every CI run. "Device" items need a manual
check on real hardware before release (record date, device and OS version).

## Shared

| Check | How it is met | Verified |
| --- | --- | --- |
| Parking state is never color alone | Status badges pair text with an icon and shape (filled check = 可停放, outlined ? = 尚未確認, outlined block = 不可停); result tiles are filled vs outlined; map markers keep M5 shapes | Automated (badge text + icon), M5 marker tests |
| System / Light / Dark | `外觀` setting (跟隨系統 / 淺色 / 深色), persisted on device | Automated |
| Map style follows theme | Dark/light map style comes from the effective theme, including a forced choice | Automated |
| Tap targets | Theme minimums are 48dp (≥44pt), `MaterialTapTargetSize.padded`; Flutter `androidTapTargetGuideline` + `iOSTapTargetGuideline` | Automated: map, detail, account, search, report sheet, light/dark/high contrast |
| Labeled controls | `labeledTapTargetGuideline`; icon buttons have tooltips; search/plate/status widgets expose semantic labels | Automated |
| Text contrast | `textContrastGuideline` (WCAG AA) in light, dark and high-contrast dark | Automated |
| Font scaling / Dynamic Type | Map and detail render without overflow at 1.0× and 2.0× on 360×640, 375×667 and 430×932 | Automated (12 size × scale × theme combinations) |
| Increase Contrast | `highContrastTheme`/`highContrastDarkTheme` (contrast level 1.0), thicker outlines; floating controls become opaque | Automated |
| Reduce Motion / Remove animations | Camera moves jump instead of animating; iOS translucent controls become opaque | Automated (helper + surface); camera behavior: Device |
| Screen reader | Semantics labels on search field, status badges and result rows (merged) | Automated (labels); VoiceOver/TalkBack reading order: Device |

## iOS

| Check | How it is met | Verified |
| --- | --- | --- |
| HIG layout | Search-first field at top, plate selector uses `CupertinoSlidingSegmentedControl`, locate control is a round floating button (no FAB) | Automated render; Device |
| Liquid Glass only on floating controls | `FloatingSurface` blur on search field, plate selector and locate button only; content sheets stay opaque | Automated |
| VoiceOver | Rotor through search, plate selector, filters, results, detail actions | Device |
| Dynamic Type at accessibility sizes (AX1–AX5) | Above 2.0× rely on scrolling sheets | Device |

## Android

| Check | How it is met | Verified |
| --- | --- | --- |
| Material 3 / Expressive direction | M3 search bar shape (56dp pill), segmented button, filter chips, FAB, bottom sheet | Automated render; Device |
| Dynamic Color | `DynamicColorBuilder` uses wallpaper colors on Android 12+; brand blue elsewhere | Device (Android 12+) |
| Edge-to-edge | `SystemUiMode.edgeToEdge`, transparent bars, sheet padded by system insets | Device (gesture and 3-button navigation) |
| TalkBack | Same flow as VoiceOver | Device |
| Font size / display size at maximum | | Device |

## Representative sizes

Automated rendering covers 360×640 (small Android), 375×667 (iPhone SE),
390×844 and 430×932 (large iPhone) in light and dark. Tablets are not an MVP
target. Screens are not required to be pixel-identical across platforms;
behavior is functionally identical.
