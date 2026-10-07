import 'package:dynamic_color/dynamic_color.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../design_system/theme.dart';
import '../features/settings/appearance_controller.dart';
import 'router.dart';

class HeavyParkingApp extends ConsumerWidget {
  const HeavyParkingApp({super.key, this.router});
  final GoRouter? router;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final themeMode = ref.watch(appearanceControllerProvider);
    return DynamicColorBuilder(
      builder: (lightDynamic, darkDynamic) {
        // Material You colors on Android 12+; the brand seed elsewhere.
        final useDynamic = defaultTargetPlatform == TargetPlatform.android &&
            lightDynamic != null &&
            darkDynamic != null;
        final schemes = useDynamic
            ? AppSchemes.fromDynamic(
                lightDynamic.harmonized(),
                darkDynamic.harmonized(),
              )
            : AppSchemes.fromSeed(brandSeed);
        return MaterialApp.router(
          title: '重機停車通',
          themeMode: themeMode,
          theme: buildTheme(schemes.light),
          darkTheme: buildTheme(schemes.dark),
          highContrastTheme:
              buildTheme(schemes.highContrastLight, highContrast: true),
          highContrastDarkTheme:
              buildTheme(schemes.highContrastDark, highContrast: true),
          routerConfig: router ?? appRouter,
          debugShowCheckedModeBanner: false,
        );
      },
    );
  }
}
