import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/source_link_service.dart';
import 'package:heavy_parking/design_system/vehicle_selector.dart';
import 'package:heavy_parking/domain/parking.dart';
import 'package:heavy_parking/features/map/map_controller.dart';
import 'package:heavy_parking/features/map/map_screen.dart';
import 'package:heavy_parking/features/map/parking_panel.dart';
import 'package:heavy_parking/features/map/parking_presentation.dart';

import 'fixtures/parking.dart';
import 'map_screen_test.dart' show WidgetRepository;

// Synthetic source URLs: the recorder never performs network or platform calls.
const links = {
  '停車規則來源': 'https://pma.gov.taipei/policy',
  '費率來源': 'https://data.taipei/rates',
  '空位來源': 'https://data.taipei/availability',
  '入口來源': 'https://data.taipei/entrances',
};

Map<String, dynamic> source(String title, int id) => {
      ...sourceJson(sourceId: id),
      'source_name': '$title公告',
      'source_url': links[title],
    };

class SourceRepository extends WidgetRepository {
  @override
  Future<ParkingDetail> detail(
    int id,
    VehicleType vehicle, {
    DateTime? at,
  }) async {
    final zone = zoneJson(
      vehicle: vehicle.wireValue,
      rateSummary: rateSummaryJson(),
      availability: availabilityJson(),
    );
    final rule = zone['compatibility'] as Map<String, dynamic>;
    rule['provenance'] = source('停車規則來源', 4);
    rule['rule_evidence'] = [
      {'rule_id': 501, 'provenance': source('停車規則來源', 4)},
    ];
    (zone['rate_summary'] as Map<String, dynamic>)['provenance'] =
        source('費率來源', 7);
    (zone['availability'] as Map<String, dynamic>)['provenance'] =
        source('空位來源', 9);
    final entrance = entranceJson();
    entrance['provenance'] = source('入口來源', 11);
    return ParkingDetail.fromJson(
      detailFixture(
        id: id,
        vehicle: vehicle.wireValue,
        zones: [zone],
        entrances: [entrance],
      ),
    );
  }
}

class SourceRecorder implements SourceLinkService {
  final opened = <Uri>[];
  bool succeeds = true;
  @override
  Future<bool> open(Uri uri) async {
    opened.add(uri);
    return succeeds;
  }
}

void main() {
  test('source names and URLs retain independent provenance in the mobile DTO',
      () {
    final parsed = ParkingSource.fromJson(source('停車規則來源', 4));
    expect(parsed.sourceName, '停車規則來源公告');
    expect(sourceUri(parsed).toString(), links['停車規則來源']);
    expect(sourceLabel(parsed), contains('停車規則來源公告'));
    final absent = ParkingSource.fromJson(sourceJson());
    expect(absent.sourceName, isNull);
    expect(sourceUri(absent), isNull);
    expect(sourceLabel(absent), contains('來源名稱未提供'));
    expect(sourceLabel(absent), isNot(contains('#4')));
  });

  for (final url in [
    'javascript:alert(1)',
    'file:///tmp/source',
    'https://',
    'https://user:password@example.com',
    'http://example.com',
    '/relative',
  ]) {
    test('unsupported source link is not offered: $url', () {
      expect(
        sourceUri(
          ParkingSource.fromJson({...sourceJson(), 'source_url': url}),
        ),
        isNull,
      );
    });
  }

  test(
      'permission explanations use readable labels and preserve unknown reasons',
      () {
    expect(compatibilityReasonLabel('conflicting_permissions'), contains('衝突'));
    expect(compatibilityReasonLabel('schedule_unknown'), contains('時段尚未確認'));
    expect(compatibilityReasonLabel('unknown_permission'), contains('尚未確認'));
    expect(compatibilityReasonLabel('unsupported_source_reason'), isNull);
  });

  test('detail zones retain allowed and opted-in unknown facts', () {
    ParkingDetail detail(List<String> statuses) => ParkingDetail.fromJson(
          detailFixture(
            zones: [for (final status in statuses) zoneJson(status: status)],
          ),
        );
    expect(
      displayDetailZones(
        detail(['NOT_ALLOWED', 'ALLOWED']),
        includeUnknown: true,
      ).single.compatibility.status,
      CompatibilityStatus.allowed,
    );
    expect(displayDetailZones(detail([]), includeUnknown: true), isEmpty);
    expect(
      displayDetailZones(
        detail(['NOT_ALLOWED', 'UNKNOWN']),
        includeUnknown: false,
      ),
      isEmpty,
    );
    expect(
      displayDetailZones(
        detail(['NOT_ALLOWED', 'UNKNOWN']),
        includeUnknown: true,
      ).single.compatibility.status,
      CompatibilityStatus.unknown,
    );
  });

  test(
      'normal detail includes a confirmed conventional zone while large excludes it',
      () {
    final normal = ParkingDetail.fromJson(
      detailFixture(
        vehicle: 'NORMAL_HEAVY',
        zones: [
          zoneJson(vehicle: 'NORMAL_HEAVY', spaceType: 'LIGHT_MOTO_ONLY'),
        ],
      ),
    );
    expect(displayDetailZones(normal, includeUnknown: false), hasLength(1));
    final large = ParkingDetail.fromJson(
      detailFixture(
        zones: [
          zoneJson(spaceType: 'LIGHT_MOTO_ONLY'),
        ],
      ),
    );
    expect(displayDetailZones(large, includeUnknown: true), isEmpty);
  });

  const platforms =
      TargetPlatformVariant({TargetPlatform.android, TargetPlatform.iOS});
  testWidgets('detail keeps the query badge and excludes other space types',
      (tester) async {
    final unknown = zoneJson(status: 'UNKNOWN', name: '搜尋範圍未確認區');
    final lot = ParkingLot.fromJson(
      lotJson(
        status: 'UNKNOWN',
        zones: [unknown],
        rankingGroup: 1,
        rankingScoreBp: null,
        availabilitySummary: unknownSummaryJson(),
      ),
    );
    final detail = ParkingDetail.fromJson(
      detailFixture(
        zones: [
          unknown,
          zoneJson(zoneId: 21, spaceType: 'CAR_SHARED', name: '範圍外可停區'),
        ],
        entrances: [],
      ),
    );
    await tester.binding.setSurfaceSize(const Size(800, 1400));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          home: Scaffold(
            body: ParkingPanel(
              state: MapState(
                query: const ParkingQuery(
                  center: defaultMapCenter,
                  includeUnknown: true,
                  spaceType: SpaceType.heavyOnly,
                ),
                selectedLot: lot,
                detail: detail,
              ),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('尚未確認'), findsWidgets);
    expect(find.text('可停放'), findsNothing);
    await tester.scrollUntilVisible(
      find.textContaining('搜尋範圍未確認區'),
      300,
      scrollable: find.byType(Scrollable).last,
    );
    expect(find.textContaining('範圍外可停區'), findsNothing);
    expect(tester.takeException(), isNull);
  });
  testWidgets(
    'both classes expose formal names to accessibility semantics',
    (tester) async {
      final semantics = tester.ensureSemantics();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: VehicleSelector(
              selected: VehicleType.largeHeavy,
              onChanged: (_) {},
            ),
          ),
        ),
      );
      expect(find.text('普重'), findsOneWidget);
      expect(find.text('大重'), findsOneWidget);
      expect(find.bySemanticsLabel(RegExp('普通重型機車')), findsWidgets);
      expect(find.bySemanticsLabel(RegExp('大型重型機車')), findsWidgets);
      semantics.dispose();
      expect(tester.takeException(), isNull);
    },
    variant: platforms,
  );

  Future<SourceRecorder> pumpDetail(WidgetTester tester) async {
    await tester.binding.setSurfaceSize(const Size(800, 1400));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final repository = SourceRepository();
    final controller = MapController(repository);
    final recorder = SourceRecorder();
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          mapControllerProvider.overrideWith((ref) => controller),
          sourceLinkServiceProvider.overrideWithValue(recorder),
        ],
        child: MaterialApp(
          home: MapScreen(mapBuilder: (_, __) => const SizedBox.expand()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(repository.queries.last.vehicle, VehicleType.largeHeavy);
    await controller.selectLot(controller.state.items.first);
    await tester.pumpAndSettle();
    expect(find.text('explicit_vehicle_permission'), findsNothing);
    return recorder;
  }

  testWidgets(
    'detail opens each fact source without substituting another source',
    (tester) async {
      final recorder = await pumpDetail(tester);
      for (final title in links.keys) {
        await tester.scrollUntilVisible(
          find.text('查看$title'),
          300,
          scrollable: find.byType(Scrollable).last,
        );
        await tester.tap(find.text('查看$title'));
        await tester.pumpAndSettle();
        expect(recorder.opened.last.toString(), links[title]);
        expect(tester.takeException(), isNull);
      }
    },
    variant: platforms,
  );

  testWidgets('source launch failure leaves the detail usable', (tester) async {
    final recorder = await pumpDetail(tester);
    recorder.succeeds = false;
    await tester.scrollUntilVisible(
      find.text('查看停車規則來源'),
      300,
      scrollable: find.byType(Scrollable).last,
    );
    await tester.tap(find.text('查看停車規則來源'));
    await tester.pumpAndSettle();
    expect(find.text('無法開啟來源連結。'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
