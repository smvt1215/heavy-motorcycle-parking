import 'package:flutter/material.dart';

import 'tokens.dart';

/// Brand seed used when the platform offers no dynamic color.
const Color brandSeed = parkingSignBlue;

/// Minimum interactive size: 48dp satisfies Android (48dp) and iOS (44pt).
const double minTouchTarget = 48;

/// Builds the shared Material 3 theme for one color scheme. Parking state is
/// always shown with icon/shape/text, so these colors only reinforce meaning.
ThemeData buildTheme(ColorScheme scheme, {bool highContrast = false}) {
  final outline = highContrast ? scheme.onSurface : scheme.outline;
  return ThemeData(
    colorScheme: scheme,
    useMaterial3: true,
    materialTapTargetSize: MaterialTapTargetSize.padded,
    visualDensity: VisualDensity.standard,
    scaffoldBackgroundColor: scheme.surface,
    filledButtonTheme: FilledButtonThemeData(
      style:
          FilledButton.styleFrom(minimumSize: const Size(64, minTouchTarget)),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(64, minTouchTarget),
        side: BorderSide(color: outline, width: highContrast ? 2 : 1),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(minimumSize: const Size(64, minTouchTarget)),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(
        minimumSize: const Size(minTouchTarget, minTouchTarget),
      ),
    ),
    chipTheme: ChipThemeData(
      side: highContrast ? BorderSide(color: outline, width: 2) : null,
    ),
    dividerTheme: DividerThemeData(color: highContrast ? outline : null),
    bottomSheetTheme: const BottomSheetThemeData(showDragHandle: true),
  );
}

/// Schemes for the four system appearance combinations. Increase Contrast
/// (iOS) / high-contrast text (Android) selects the maximum-contrast variant.
class AppSchemes {
  const AppSchemes({
    required this.light,
    required this.dark,
    required this.highContrastLight,
    required this.highContrastDark,
  });

  factory AppSchemes.fromSeed(Color seed) => AppSchemes(
        light: ColorScheme.fromSeed(seedColor: seed),
        dark: ColorScheme.fromSeed(
          seedColor: seed,
          brightness: Brightness.dark,
        ),
        highContrastLight: ColorScheme.fromSeed(
          seedColor: seed,
          contrastLevel: 1,
        ),
        highContrastDark: ColorScheme.fromSeed(
          seedColor: seed,
          brightness: Brightness.dark,
          contrastLevel: 1,
        ),
      );

  /// Android 12+ wallpaper colors. Contrast variants keep the dynamic primary
  /// as their seed so Increase Contrast still matches the wallpaper hue.
  factory AppSchemes.fromDynamic(ColorScheme light, ColorScheme dark) =>
      AppSchemes(
        light: light,
        dark: dark,
        highContrastLight: ColorScheme.fromSeed(
          seedColor: light.primary,
          contrastLevel: 1,
        ),
        highContrastDark: ColorScheme.fromSeed(
          seedColor: dark.primary,
          brightness: Brightness.dark,
          contrastLevel: 1,
        ),
      );

  final ColorScheme light;
  final ColorScheme dark;
  final ColorScheme highContrastLight;
  final ColorScheme highContrastDark;
}

/// True when the rider asked the OS to reduce motion.
bool reduceMotion(BuildContext context) =>
    MediaQuery.maybeDisableAnimationsOf(context) ?? false;
