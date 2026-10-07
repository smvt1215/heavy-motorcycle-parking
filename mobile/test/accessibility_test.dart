import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/app/app.dart';
import 'package:heavy_parking/features/account/account_controller.dart';
import 'package:heavy_parking/features/map/map_controller.dart';
import 'package:heavy_parking/design_system/status_badge.dart';
import 'package:heavy_parking/features/map/map_screen.dart';
import 'package:heavy_parking/features/search/destination_search_controller.dart';
import 'package:go_router/go_router.dart';

import 'fixtures/places.dart';
import 'fixtures/user.dart';
import 'map_screen_test.dart' show WidgetRepository;

const sizes = {
  'small Android 360x640': Size(360, 640),
  'iPhone SE 375x667': Size(375, 667),
  'large iPhone 430x932': Size(430, 932),
};

Future<MapController> pumpApp(
  WidgetTester tester, {
  required Size size,
  double textScale = 1,
  Brightness brightness = Brightness.light,
  bool signedIn = false,
}) async {
  tester.view.physicalSize = size * 3;
  tester.view.devicePixelRatio = 3;
  tester.platformDispatcher.textScaleFactorTestValue = textScale;
  tester.platformDispatcher.platformBrightnessTestValue = brightness;
  addTearDown(tester.view.reset);
  addTearDown(tester.platformDispatcher.clearAllTestValues);
  final controller = MapController(WidgetRepository());
  final router = GoRouter(
    routes: [
      GoRoute(
        path: '/',
        builder: (_, __) =>
            MapScreen(mapBuilder: (_, __) => const SizedBox.expand()),
      ),
    ],
  );
  addTearDown(router.dispose);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        mapControllerProvider.overrideWith((ref) => controller),
        userRepositoryProvider.overrideWithValue(FakeUserRepository()),
        tokenStoreProvider.overrideWithValue(
          MemoryTokenStore(signedIn ? validToken : null),
        ),
        placesRepositoryProvider.overrideWithValue(FakePlacesRepository()),
        recentSearchStoreProvider.overrideWithValue(MemoryRecentSearchStore()),
      ],
      child: HeavyParkingApp(router: router),
    ),
  );
  await tester.pumpAndSettle();
  return controller;
}

void main() {
  for (final MapEntry(key: name, value: size) in sizes.entries) {
    for (final scale in [1.0, 2.0]) {
      for (final brightness in Brightness.values) {
        testWidgets('map renders: $name, text x$scale, ${brightness.name}',
            (tester) async {
          final controller = await pumpApp(
            tester,
            size: size,
            textScale: scale,
            brightness: brightness,
          );
          expect(tester.takeException(), isNull);
          final context = tester.element(find.byType(MapScreen));
          expect(MediaQuery.textScalerOf(context).scale(10), 10 * scale);
          await controller.selectLot(controller.state.items.first);
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
        });
      }
    }
  }

  Future<void> audit(WidgetTester tester) async {
    await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
    await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
    await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
    await expectLater(tester, meetsGuideline(textContrastGuideline));
  }

  for (final brightness in Brightness.values) {
    testWidgets('core flow meets accessibility guidelines (${brightness.name})',
        (tester) async {
      final handle = tester.ensureSemantics();
      final controller = await pumpApp(
        tester,
        size: const Size(390, 844),
        brightness: brightness,
      );
      await audit(tester); // map + nearby list

      await controller.selectLot(controller.state.items.first);
      await tester.pumpAndSettle();
      await audit(tester); // selected parking detail

      await tester.tap(find.byTooltip('我的帳號'));
      await tester.pumpAndSettle();
      await audit(tester); // account sheet
      Navigator.of(tester.element(find.text('我的帳號'))).pop();
      await tester.pumpAndSettle();

      await tester.tap(find.text('搜尋目的地'));
      await tester.pumpAndSettle();
      await audit(tester); // destination search
      handle.dispose();
    });
  }

  testWidgets('high-contrast dark theme still meets the guidelines',
      (tester) async {
    final handle = tester.ensureSemantics();
    tester.platformDispatcher.accessibilityFeaturesTestValue =
        const FakeAccessibilityFeatures(highContrast: true);
    final controller = await pumpApp(
      tester,
      size: const Size(390, 844),
      brightness: Brightness.dark,
    );
    await audit(tester);
    await controller.selectLot(controller.state.items.first);
    await tester.pumpAndSettle();
    await audit(tester);
    handle.dispose();
  });

  testWidgets('report sheet meets the guidelines', (tester) async {
    final handle = tester.ensureSemantics();
    final controller = await pumpApp(
      tester,
      size: const Size(390, 844),
      signedIn: true,
    );
    await controller.selectLot(controller.state.items.first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('回報問題'));
    await tester.pumpAndSettle();
    await audit(tester);
    handle.dispose();
  });

  testWidgets('parking state is never color alone', (tester) async {
    final controller = await pumpApp(tester, size: const Size(390, 844));
    // Every listed lot carries a text status and a shape/icon, not only color.
    expect(find.text('可停放'), findsWidgets);
    // The badge pairs the text with an icon; the result tile has its own shape.
    expect(find.byType(StatusBadge), findsWidgets);
    expect(
      find.descendant(
        of: find.byType(StatusBadge).first,
        matching: find.byIcon(Icons.verified),
      ),
      findsOneWidget,
    );
    await controller.selectLot(controller.state.items.first);
    await tester.pumpAndSettle();
    expect(find.textContaining('停車場整體'), findsOneWidget);
  });
}
