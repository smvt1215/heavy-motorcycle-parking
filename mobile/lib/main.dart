import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app/app.dart';
import 'features/settings/appearance_controller.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Draw behind transparent system bars; screens pad with SafeArea/viewPadding.
  // Bar icon brightness follows the app theme (see HeavyParkingApp).
  SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
  final store = SharedPreferencesAppearanceStore();
  final themeMode = await loadInitialThemeMode(store);
  runApp(
    ProviderScope(
      overrides: [
        appearanceStoreProvider.overrideWithValue(store),
        initialThemeModeProvider.overrideWithValue(themeMode),
      ],
      child: const HeavyParkingApp(),
    ),
  );
}
