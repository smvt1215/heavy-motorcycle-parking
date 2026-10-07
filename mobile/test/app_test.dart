import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:heavy_parking/app/app.dart';
import 'package:heavy_parking/features/map/map_controller.dart';
import 'package:heavy_parking/features/map/map_screen.dart';
import 'map_screen_test.dart' show WidgetRepository;

void main() {
  testWidgets('App renders without crashing', (tester) async {
    final router = GoRouter(
      routes: [
        GoRoute(
          path: '/',
          builder: (_, state) =>
              MapScreen(mapBuilder: (_, config) => const SizedBox.expand()),
        ),
      ],
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          parkingRepositoryProvider.overrideWithValue(WidgetRepository()),
        ],
        child: HeavyParkingApp(router: router),
      ),
    );
    await tester.pumpAndSettle();
    // Search-first header: the destination field leads the map.
    expect(find.text('搜尋目的地'), findsOneWidget);
    expect(find.byTooltip('我的帳號'), findsOneWidget);
  });
}
