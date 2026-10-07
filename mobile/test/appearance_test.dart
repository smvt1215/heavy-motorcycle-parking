import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:heavy_parking/app/app.dart';
import 'package:heavy_parking/design_system/floating_surface.dart';
import 'package:heavy_parking/design_system/theme.dart';
import 'package:heavy_parking/features/account/account_controller.dart';
import 'package:heavy_parking/features/account/account_sheet.dart';
import 'package:heavy_parking/features/map/map_controller.dart';
import 'package:heavy_parking/features/map/map_screen.dart';
import 'package:heavy_parking/features/map/map_styles.dart';
import 'package:heavy_parking/features/settings/appearance_controller.dart';

import 'fixtures/user.dart';
import 'map_screen_test.dart' show WidgetRepository;

class MemoryAppearanceStore implements AppearanceStore {
  MemoryAppearanceStore([this.mode]);
  ThemeMode? mode;

  @override
  Future<ThemeMode?> load() async => mode;

  @override
  Future<void> save(ThemeMode mode) async => this.mode = mode;
}

void main() {
  late MemoryAppearanceStore store;
  MapCanvasConfiguration? config;

  Future<void> pumpApp(WidgetTester tester) async {
    final router = GoRouter(
      routes: [
        GoRoute(
          path: '/',
          builder: (_, __) => MapScreen(
            mapBuilder: (_, value) {
              config = value;
              return const SizedBox.expand();
            },
          ),
        ),
      ],
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          appearanceStoreProvider.overrideWithValue(store),
          mapControllerProvider
              .overrideWith((ref) => MapController(WidgetRepository())),
          userRepositoryProvider.overrideWithValue(FakeUserRepository()),
          tokenStoreProvider.overrideWithValue(MemoryTokenStore()),
        ],
        child: HeavyParkingApp(router: router),
      ),
    );
    await tester.pumpAndSettle();
  }

  setUp(() {
    store = MemoryAppearanceStore();
    config = null;
  });

  testWidgets('follows the system appearance by default, map included',
      (tester) async {
    tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;
    addTearDown(tester.platformDispatcher.clearAllTestValues);
    await pumpApp(tester);
    expect(
      Theme.of(tester.element(find.byType(MapScreen))).brightness,
      Brightness.dark,
    );
    expect(config!.style, darkMapStyle);
  });

  testWidgets('a saved Light/Dark choice overrides the system and persists',
      (tester) async {
    store.mode = ThemeMode.light;
    tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;
    addTearDown(tester.platformDispatcher.clearAllTestValues);
    await pumpApp(tester);
    expect(config!.style, lightMapStyle);

    await tester.tap(find.byTooltip('我的帳號'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('深色'),
      100,
      scrollable: find
          .descendant(
            of: find.byType(AccountSheet),
            matching: find.byType(Scrollable),
          )
          .first,
    );
    await tester.tap(find.text('深色'));
    await tester.pumpAndSettle();
    expect(store.mode, ThemeMode.dark);
    expect(config!.style, darkMapStyle);
    expect(
      Theme.of(tester.element(find.byType(MapScreen))).brightness,
      Brightness.dark,
    );
  });

  testWidgets('Increase Contrast selects the high-contrast scheme',
      (tester) async {
    tester.platformDispatcher.accessibilityFeaturesTestValue =
        const FakeAccessibilityFeatures(highContrast: true);
    addTearDown(tester.platformDispatcher.clearAllTestValues);
    await pumpApp(tester);
    final scheme = Theme.of(tester.element(find.byType(MapScreen))).colorScheme;
    expect(scheme, AppSchemes.fromSeed(brandSeed).highContrastLight);
  });

  testWidgets('touch-target minimums apply to every button type',
      (tester) async {
    final theme = buildTheme(AppSchemes.fromSeed(brandSeed).light);
    expect(theme.materialTapTargetSize, MaterialTapTargetSize.padded);
    for (final style in [
      theme.filledButtonTheme.style,
      theme.outlinedButtonTheme.style,
      theme.textButtonTheme.style,
      theme.iconButtonTheme.style,
    ]) {
      expect(style!.minimumSize!.resolve({})!.height, minTouchTarget);
    }
  });

  group('floating controls', () {
    Future<bool> blurred(
      WidgetTester tester, {
      TargetPlatform platform = TargetPlatform.iOS,
      bool highContrast = false,
      bool disableAnimations = false,
    }) async {
      debugDefaultTargetPlatformOverride = platform;
      addTearDown(() => debugDefaultTargetPlatformOverride = null);
      await tester.pumpWidget(
        MediaQuery(
          data: MediaQueryData(
            highContrast: highContrast,
            disableAnimations: disableAnimations,
          ),
          child: MaterialApp(
            home: Scaffold(
              body: Builder(
                builder: (context) {
                  // Reduce Motion is also exposed to the camera logic.
                  expect(reduceMotion(context), disableAnimations);
                  return const FloatingSurface(child: Text('搜尋目的地'));
                },
              ),
            ),
          ),
        ),
      );
      final result = find.byType(BackdropFilter).evaluate().isNotEmpty;
      debugDefaultTargetPlatformOverride = null;
      return result;
    }

    testWidgets('iOS uses translucent glass for floating controls',
        (tester) async {
      expect(await blurred(tester), isTrue);
    });

    testWidgets('Increase Contrast and Reduce Motion make them opaque',
        (tester) async {
      expect(await blurred(tester, highContrast: true), isFalse);
      expect(await blurred(tester, disableAnimations: true), isFalse);
    });

    testWidgets('Android uses an opaque Material surface', (tester) async {
      expect(await blurred(tester, platform: TargetPlatform.android), isFalse);
    });
  });
}
