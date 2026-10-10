import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/photo_picker.dart';
import 'package:heavy_parking/domain/user.dart';
import 'package:heavy_parking/features/account/account_controller.dart';
import 'package:heavy_parking/features/map/map_controller.dart';
import 'package:heavy_parking/features/map/map_screen.dart';

import 'fixtures/user.dart';
import 'map_screen_test.dart' show WidgetRepository;

class _Picker implements PhotoPicker {
  int calls = 0;
  PhotoPickFailure? failure;
  PickedPhoto? lost;

  @override
  Future<PickedPhoto?> pick({required bool camera}) async {
    calls++;
    if (failure case final failure?) throw failure;
    return PickedPhoto(Uint8List.fromList([1, 2, 3]), 'entrance.jpg');
  }

  @override
  Future<PickedPhoto?> recoverLost() async {
    final photo = lost;
    lost = null;
    return photo;
  }
}

void main() {
  late WidgetRepository parking;
  late FakeUserRepository users;
  late MemoryTokenStore store;
  late MapController controller;
  late _Picker picker;

  Future<void> pumpMap(WidgetTester tester) async {
    await tester.binding.setSurfaceSize(const Size(800, 1400));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    controller = MapController(parking);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          mapControllerProvider.overrideWith((ref) => controller),
          userRepositoryProvider.overrideWithValue(users),
          tokenStoreProvider.overrideWithValue(store),
          photoPickerProvider.overrideWithValue(picker),
        ],
        child: MaterialApp(
          home: MapScreen(mapBuilder: (_, __) => const SizedBox.expand()),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  Future<void> selectFirstLot(WidgetTester tester) async {
    await controller.selectLot(controller.state.items.first);
    await tester.pumpAndSettle();
  }

  setUp(() {
    parking = WidgetRepository();
    users = FakeUserRepository();
    store = MemoryTokenStore();
    picker = _Picker();
  });

  testWidgets('guest favorite prompts sign-in; signed-in rider saves it',
      (tester) async {
    await pumpMap(tester);
    await selectFirstLot(tester);
    final lotId = controller.state.selectedLot!.id;

    await tester.tap(find.text('收藏'));
    await tester.pumpAndSettle();
    expect(find.text('目前以訪客身分使用，仍可搜尋與查看停車資訊。'), findsOneWidget);
    expect(users.calls, isEmpty);

    await tester.tap(find.text('開發者登入'));
    await tester.pumpAndSettle();
    expect(store.token, 'hmp_rider');
    expect(find.text('收藏的停車場'), findsOneWidget);
    Navigator.of(tester.element(find.text('收藏的停車場'))).pop();
    await tester.pumpAndSettle();

    await tester.tap(find.text('收藏'));
    await tester.pumpAndSettle();
    expect(find.text('已收藏'), findsOneWidget);
    expect(users.favoriteIds, [lotId]);

    await tester.tap(find.byTooltip('我的帳號'));
    await tester.pumpAndSettle();
    expect(find.text('Lot $lotId'), findsOneWidget);
    final searches = parking.queries.length;
    await tester.tap(find.text('Lot $lotId'));
    await tester.pumpAndSettle();
    // Opening a favorite searches our backend at its location.
    expect(parking.queries.length, searches + 1);
    expect(parking.queries.last.center.lat, 25.05);
  });

  testWidgets('signed-in rider submits a report with a photo', (tester) async {
    store.token = validToken;
    await pumpMap(tester);
    await selectFirstLot(tester);

    await tester.tap(find.text('回報問題'));
    await tester.pumpAndSettle();
    expect(find.text('回報會由人工審核，不會直接改變官方停車資料。'), findsOneWidget);
    await tester.tap(find.text(ReportType.parkingAllowed.label));
    await tester.pumpAndSettle();
    await tester.tap(find.text(ReportType.wrongEntrance.label).last);
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, '入口在後巷');
    await tester.tap(find.byTooltip('從相簿選擇'));
    await tester.pumpAndSettle();
    expect(find.text('已選擇照片：entrance.jpg'), findsOneWidget);
    await tester.tap(find.text('送出回報'));
    await tester.pumpAndSettle();

    expect(find.text('感謝回報！審核前不會影響官方資料。'), findsOneWidget);
    expect(users.reports.single.type, ReportType.wrongEntrance);
    expect(users.reports.single.description, '入口在後巷');
    expect(users.photos.single.$2, 'entrance.jpg');
  });

  testWidgets('saved vehicle preference seeds the explicit map vehicle',
      (tester) async {
    store.token = validToken;
    users.preferred = null;
    await pumpMap(tester);
    await tester.tap(find.byTooltip('我的帳號'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('普重').last);
    await tester.pumpAndSettle();
    expect(users.preferred?.wireValue, 'NORMAL_HEAVY');
    expect(parking.queries.last.vehicle.wireValue, 'NORMAL_HEAVY');

    await tester.tap(find.text('登出'));
    await tester.pumpAndSettle();
    expect(store.token, isNull);
    expect(find.text('開發者登入'), findsOneWidget);
  });

  testWidgets('picker failures are shown instead of crashing the sheet',
      (tester) async {
    store.token = validToken;
    picker.failure = const PhotoPickFailure('相機權限已關閉，請至系統設定開啟。');
    await pumpMap(tester);
    await selectFirstLot(tester);
    await tester.tap(find.text('回報問題'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('拍照'));
    await tester.pumpAndSettle();
    expect(find.text('相機權限已關閉，請至系統設定開啟。'), findsOneWidget);
    expect(tester.takeException(), isNull);
    expect(find.text('可附一張照片佐證（選填）'), findsOneWidget);
  });

  testWidgets('a photo lost to Android activity recreation is recovered',
      (tester) async {
    store.token = validToken;
    picker.lost = PickedPhoto(Uint8List.fromList([9]), 'recovered.jpg');
    await pumpMap(tester);
    expect(find.textContaining('已找回先前選擇的照片'), findsOneWidget);
    await selectFirstLot(tester);
    await tester.tap(find.text('回報問題'));
    await tester.pumpAndSettle();
    expect(find.text('已選擇照片：recovered.jpg'), findsOneWidget);
    await tester.tap(find.text('送出回報'));
    await tester.pumpAndSettle();
    expect(users.photos.single.$2, 'recovered.jpg');
  });
}
