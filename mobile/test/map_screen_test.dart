import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/location_service.dart';
import 'package:heavy_parking/data/map_configuration.dart';
import 'package:heavy_parking/data/navigation_service.dart';
import 'package:heavy_parking/data/parking_repository.dart';
import 'package:heavy_parking/domain/parking.dart';
import 'package:heavy_parking/features/map/filters_sheet.dart';
import 'package:heavy_parking/features/map/map_controller.dart';
import 'package:heavy_parking/features/map/map_screen.dart';
import 'package:heavy_parking/features/map/parking_panel.dart';
import 'package:heavy_parking/features/map/parking_presentation.dart';
import 'package:heavy_parking/data/places_repository.dart';
import 'package:heavy_parking/domain/place.dart';
import 'package:heavy_parking/features/search/destination_search_controller.dart';
import 'package:heavy_parking/features/search/destination_search_screen.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';

import 'fixtures/parking.dart';
import 'fixtures/places.dart';

class WidgetRepository implements ParkingRepository {
  final queries = <ParkingQuery>[];
  bool fail = false;
  List<Map<String, dynamic>>? items;

  @override
  Future<NearbyParkingPage> nearby(ParkingQuery query, {String? cursor}) async {
    queries.add(query);
    if (fail) {
      throw const ParkingApiException(code: 'NETWORK_ERROR', message: 'test');
    }
    return NearbyParkingPage.fromJson(
      nearbyFixture(vehicle: query.vehicle.wireValue, items: items),
    );
  }

  @override
  Future<ParkingDetail> detail(
    int id,
    VehicleType vehicle, {
    DateTime? at,
  }) async =>
      ParkingDetail.fromJson(detailFixture(id: id, vehicle: vehicle.wireValue));

  @override
  Future<ParkingRates> rates(
    int id,
    VehicleType vehicle, {
    DateTime? at,
  }) async =>
      throw UnimplementedError('Unused in map widget tests');

  @override
  Future<ParkingRealtime> realtime(
    int id,
    VehicleType vehicle, {
    DateTime? at,
  }) async =>
      throw UnimplementedError('Unused in map widget tests');
}

class FakeLocation implements LocationService {
  @override
  Future<GeoPoint> locate() async => throw const LocationFailure('測試拒絕定位');
}

void main() {
  testWidgets('missing Maps configuration keeps parking results usable',
      (tester) async {
    final repository = WidgetRepository();
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          parkingRepositoryProvider.overrideWithValue(repository),
          mapConfigurationProvider.overrideWith((ref) async => false),
        ],
        child: const MaterialApp(home: MapScreen()),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byType(GoogleMap), findsNothing);
    expect(find.text('地圖尚未就緒\n仍可查看附近停車資訊'), findsOneWidget);
    expect(find.text('普重'), findsOneWidget);
    expect(repository.queries, hasLength(1));
  });

  testWidgets(
      'camera callbacks query only on search-this-area; vehicle and availability controls persist',
      (tester) async {
    final repository = WidgetRepository();
    final controller = MapController(repository);
    MapCanvasConfiguration? config;
    await tester.pumpWidget(
      ProviderScope(
        overrides: [mapControllerProvider.overrideWith((ref) => controller)],
        child: MaterialApp(
          home: MapScreen(
            mapBuilder: (_, value) {
              config = value;
              return const ColoredBox(color: Colors.grey);
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(repository.queries, hasLength(1));
    for (var i = 0; i < 50; i++) {
      config!.onCameraMove(GeoPoint(25.04 + i / 100000, 121.57));
    }
    config!.onCameraIdle();
    await tester.pump();
    expect(repository.queries, hasLength(1));
    await tester.tap(find.text('搜尋此區域'));
    await tester.pumpAndSettle();
    expect(repository.queries, hasLength(2));
    expect(repository.queries.last.center, const GeoPoint(25.04049, 121.57));
    await tester.tap(find.text('普重'));
    await tester.pumpAndSettle();
    expect(repository.queries.last.vehicle, VehicleType.normalHeavy);
    await tester.tap(find.text('目前有空位'));
    await tester.pumpAndSettle();
    expect(repository.queries.last.availableOnly, isTrue);
    expect(repository.queries.last.vehicle, VehicleType.normalHeavy);
  });

  testWidgets('hourly filter uses backend threshold and can be cleared',
      (tester) async {
    final repository = WidgetRepository();
    final controller = MapController(repository);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [mapControllerProvider.overrideWith((ref) => controller)],
        child: MaterialApp(
          home: MapScreen(mapBuilder: (_, value) => const SizedBox.expand()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('每小時費率'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('每小時 ≤ NT\$50'));
    await tester.pumpAndSettle();
    expect(repository.queries.last.hourlyRateMaxTwd, 50);
    await tester.tap(find.text('≤ NT\$50/時'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('不限已確認費率'));
    await tester.pumpAndSettle();
    expect(repository.queries.last.hourlyRateMaxTwd, isNull);
  });

  testWidgets(
      'filters expose only compatible spaces and explicit unknown opt-in',
      (tester) async {
    ParkingQuery? result;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () async {
                result = await showModalBottomSheet<ParkingQuery>(
                  context: context,
                  isScrollControlled: true,
                  builder: (_) => const FiltersSheet(
                    query: ParkingQuery(center: defaultMapCenter),
                  ),
                );
              },
              child: const Text('開啟'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('開啟'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('所有相容車格'));
    await tester.pumpAndSettle();
    expect(find.text('重機專用'), findsOneWidget);
    expect(find.text('機車共用'), findsOneWidget);
    expect(find.text('汽車格共用'), findsOneWidget);
    expect(find.text('輕型機車專用'), findsNothing);
    await tester.tap(find.text('汽車格共用'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('顯示未確認停車位置'));
    await tester.tap(find.text('套用條件'));
    await tester.pumpAndSettle();
    expect(result!.spaceType, SpaceType.carShared);
    expect(result!.includeUnknown, isTrue);
    expect(result!.toQueryParameters()['include_unknown'], isTrue);
  });

  testWidgets(
      'loading, empty, network error and denied location retain map controls',
      (tester) async {
    final repository = WidgetRepository()..items = [];
    final controller = MapController(repository);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          mapControllerProvider.overrideWith((ref) => controller),
          locationServiceProvider.overrideWithValue(FakeLocation()),
        ],
        child: MaterialApp(
          home: MapScreen(mapBuilder: (_, value) => const SizedBox.expand()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('這個區域沒有符合條件的停車場。'), findsOneWidget);
    repository.fail = true;
    await controller.search();
    await tester.pumpAndSettle();
    expect(find.text('無法取得停車資料，請檢查網路後重試。'), findsOneWidget);
    await tester.tap(find.byTooltip('定位我的位置'));
    await tester.pumpAndSettle();
    expect(find.text('測試拒絕定位'), findsOneWidget);
    expect(find.text('普重'), findsOneWidget);
  });

  testWidgets('partial summary does not synthesize child totals',
      (tester) async {
    final repository = WidgetRepository();
    final controller = MapController(repository);
    final lot = ParkingLot.fromJson(
      lotJson(
        availabilitySummary: unknownSummaryJson(coverage: 'PARTIAL'),
        zones: [
          zoneJson(availability: availabilityJson(available: 9, total: 10)),
        ],
      ),
    );
    final state = MapState(
      query: const ParkingQuery(center: defaultMapCenter),
      selectedLot: lot,
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [mapControllerProvider.overrideWith((ref) => controller)],
        child: MaterialApp(home: Scaffold(body: ParkingPanel(state: state))),
      ),
    );
    expect(find.text('停車場整體：空位不明 · 部分涵蓋，總數不明'), findsOneWidget);
    expect(find.textContaining('停車場整體：9/10'), findsNothing);
  });

  for (final scenario in [
    (
      name: 'UNKNOWN freshness',
      observation: availabilityJson(freshness: 'UNKNOWN'),
      freshness: '更新時間不明',
      label: '上次觀測：有空位（目前未確認） · 8/20 格',
    ),
    (
      // Malformed response defense, not a valid FRESH fixture.
      name: 'FRESH observation missing fetched_at',
      observation: availabilityJson(fetchedAt: null),
      freshness: '更新時間未確認',
      label: '上次觀測：有空位（目前未確認） · 8/20 格',
    ),
    (
      name: 'STALE observation',
      observation: availabilityJson(freshness: 'STALE'),
      freshness: '可能已過時',
      label: '上次觀測：有空位 · 8/20 格',
    ),
  ]) {
    testWidgets('selected panel preserves unconfirmed ${scenario.name}',
        (tester) async {
      final lot = ParkingLot.fromJson(
        lotJson(
          zones: [zoneJson(availability: scenario.observation)],
          availabilitySummary: unknownSummaryJson(),
        ),
      );
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            parkingRepositoryProvider.overrideWithValue(WidgetRepository()),
          ],
          child: MaterialApp(
            home: Scaffold(
              body: ParkingPanel(
                state: MapState(
                  query: const ParkingQuery(center: defaultMapCenter),
                  selectedLot: lot,
                ),
              ),
            ),
          ),
        ),
      );
      await tester.scrollUntilVisible(
        find.text(scenario.label),
        100,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      expect(find.text(scenario.label), findsOneWidget);
      expect(find.text(scenario.freshness), findsWidgets);
      expect(find.textContaining('目前有空位'), findsNothing);
      expect(
        lot.zones.single.availability!.status,
        AvailabilityStatus.available,
      );
      expect(lot.zones.single.hasConfirmedAvailability, isFalse);
    });
  }

  testWidgets('opted-in unknown zone is visibly unverified in the panel',
      (tester) async {
    final lot = ParkingLot.fromJson(
      lotJson(
        status: 'UNKNOWN',
        zones: [
          zoneJson(
            status: 'UNKNOWN',
            availability: availabilityJson(),
          ),
        ],
        availabilitySummary: unknownSummaryJson(eligibleZoneCount: 0),
      ),
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          parkingRepositoryProvider.overrideWithValue(WidgetRepository()),
        ],
        child: MaterialApp(
          home: Scaffold(
            body: ParkingPanel(
              state: MapState(
                query: const ParkingQuery(
                  center: defaultMapCenter,
                  includeUnknown: true,
                ),
                selectedLot: lot,
              ),
            ),
          ),
        ),
      ),
    );
    const label = '上次觀測：有空位（目前未確認） · 8/20 格';
    await tester.scrollUntilVisible(
      find.text(label),
      100,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();
    expect(find.text('尚未確認'), findsWidgets);
    expect(find.text(label), findsOneWidget);
    expect(find.text('可停放'), findsNothing);
    expect(find.textContaining('目前有空位'), findsNothing);
  });

  test('presentation preserves stale status, unknown counts and server status',
      () {
    final zone = ParkingZone.fromJson(
      zoneJson(availability: availabilityJson(freshness: 'STALE')),
    );
    expect(zoneAvailabilityLabel(zone), '上次觀測：有空位 · 8/20 格');
    expect(zoneFreshnessLabel(zone), '可能已過時');
    expect(zone.availability!.status, AvailabilityStatus.available);
    expect(zone.hasConfirmedAvailability, isFalse);
    final unknown = ParkingZone.fromJson(
      zoneJson(availability: availabilityJson(status: 'UNKNOWN')),
    );
    expect(zoneAvailabilityLabel(unknown), '空位資料不明');
    final summary = AvailabilitySummary.fromJson(
      summaryJson(status: 'CLOSED', available: 0),
    );
    expect(summaryLabel(summary), '未開放 · 0/20 格');
  });

  for (final observation in [
    availabilityJson(freshness: 'UNKNOWN'),
    availabilityJson(fetchedAt: null),
    availabilityJson(available: -1),
    availabilityJson(available: 2.5),
  ]) {
    test(
        'unconfirmed AVAILABLE observation is never labeled current: $observation',
        () {
      final zone = ParkingZone.fromJson(zoneJson(availability: observation));
      expect(zoneAvailabilityLabel(zone), contains('目前未確認'));
      expect(zoneAvailabilityLabel(zone), isNot(contains('目前有空位')));
      expect(zone.availability!.status, AvailabilityStatus.available);
    });
  }

  test('navigation URLs use ALLOWED entrance, then explicit center fallback',
      () {
    final detail = ParkingDetail.fromJson(detailFixture());
    final target = detail.navigationTarget;
    expect(target.entrance!.id, 301);
    expect(
      navigationUri(target, NavigationApp.googleMaps)
          .queryParameters['destination'],
      '25.0333,121.5625',
    );
    final unknownDetail = ParkingDetail.fromJson(
      detailFixture(entrances: [entranceJson(access: 'UNKNOWN')]),
    );
    expect(unknownDetail.navigationTarget.isConfirmedEntrance, isFalse);
    expect(unknownDetail.navigationTarget.location, unknownDetail.location);
    expect(
      navigationUri(unknownDetail.navigationTarget, NavigationApp.appleMaps)
          .queryParameters['daddr'],
      '25.0331,121.5628',
    );
  });

  group('destination search', () {
    late WidgetRepository repository;
    late FakePlacesRepository places;
    late MemoryRecentSearchStore recents;
    late MapController controller;
    MapCanvasConfiguration? config;

    Future<void> pumpMap(WidgetTester tester) async {
      repository = WidgetRepository();
      controller = MapController(repository);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            mapControllerProvider.overrideWith((ref) => controller),
            placesRepositoryProvider.overrideWithValue(places),
            recentSearchStoreProvider.overrideWithValue(recents),
          ],
          child: MaterialApp(
            home: MapScreen(
              mapBuilder: (_, value) {
                config = value;
                return const SizedBox.expand();
              },
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
    }

    setUp(() {
      places = FakePlacesRepository();
      recents = MemoryRecentSearchStore();
      config = null;
    });

    testWidgets('台北101 selects a destination and queries our nearby API',
        (tester) async {
      await pumpMap(tester);
      expect(repository.queries, hasLength(1));

      await tester.tap(find.text('搜尋目的地'));
      await tester.pumpAndSettle();
      expect(find.text('輸入目的地，查詢附近可停重機的停車位置。'), findsOneWidget);
      expect(find.text('地點搜尋由 Google 提供'), findsOneWidget);

      await tester.enterText(find.byType(TextField), '台北101');
      await tester.pump(destinationSearchDebounce);
      await tester.pumpAndSettle();
      expect(places.autocompleteCalls, hasLength(1));
      expect(places.autocompleteCalls.single.bias, defaultMapCenter);

      await tester.tap(find.widgetWithText(ListTile, '台北101'));
      await tester.pumpAndSettle();

      expect(find.byType(DestinationSearchScreen), findsNothing);
      expect(
        places.detailsCalls.single.sessionToken,
        places.autocompleteCalls.single.sessionToken,
      );
      expect(repository.queries, hasLength(2));
      expect(repository.queries.last.center, taipei101.location);
      expect(repository.queries.last.vehicle, VehicleType.largeHeavy);
      expect(config!.destination?.placeId, taipei101Id);
      expect(find.text('台北101'), findsOneWidget);
      expect(find.text('搜尋此區域'), findsNothing);
      expect(recents.items.single.placeId, taipei101Id);

      await tester.tap(find.byTooltip('清除目的地'));
      await tester.pumpAndSettle();
      expect(config!.destination, isNull);
      expect(find.text('搜尋目的地'), findsOneWidget);
      expect(repository.queries, hasLength(2));
    });

    testWidgets('recent search reopens without calling Places', (tester) async {
      recents.items = [
        RecentDestination.fromDestination(taipei101, DateTime.now()),
      ];
      await pumpMap(tester);
      await tester.tap(find.text('搜尋目的地'));
      await tester.pumpAndSettle();
      expect(find.text('最近搜尋'), findsOneWidget);
      await tester.tap(find.text('台北101'));
      await tester.pumpAndSettle();
      expect(places.autocompleteCalls, isEmpty);
      expect(places.detailsCalls, isEmpty);
      expect(repository.queries.last.center, taipei101.location);
    });

    testWidgets('no result, API error with retry, and cancel keep the map',
        (tester) async {
      await pumpMap(tester);
      await tester.tap(find.text('搜尋目的地'));
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(TextField), '查無此地zz');
      await tester.pump(destinationSearchDebounce);
      await tester.pumpAndSettle();
      expect(find.textContaining('找不到「查無此地zz」'), findsOneWidget);

      places.autocompleteError = const PlacesException(
        code: PlacesException.unavailable,
        message: 'off',
      );
      await tester.enterText(find.byType(TextField), '台北101');
      await tester.pump(destinationSearchDebounce);
      await tester.pumpAndSettle();
      expect(find.textContaining('目的地搜尋暫時無法使用'), findsOneWidget);

      places.autocompleteError = null;
      await tester.tap(find.text('重試'));
      await tester.pumpAndSettle();
      expect(find.widgetWithText(ListTile, '台北101'), findsOneWidget);

      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      expect(find.byType(DestinationSearchScreen), findsNothing);
      expect(repository.queries, hasLength(1));
      expect(config!.destination, isNull);
      expect(places.detailsCalls, isEmpty);
    });

    testWidgets('closing during debounce sends no Places request',
        (tester) async {
      await pumpMap(tester);
      await tester.tap(find.text('搜尋目的地'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), '台北101');
      await tester.pump(const Duration(milliseconds: 100));
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      await tester.pump(const Duration(seconds: 1));
      expect(places.autocompleteCalls, isEmpty);
    });
  });
}
