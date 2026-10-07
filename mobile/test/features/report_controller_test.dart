import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/photo_picker.dart';
import 'package:heavy_parking/data/user_repository.dart';
import 'package:heavy_parking/domain/user.dart';
import 'package:heavy_parking/features/account/account_controller.dart';
import 'package:heavy_parking/features/reports/report_controller.dart';

import '../fixtures/user.dart';

void main() {
  late FakeUserRepository repo;
  late ProviderContainer c;

  Future<void> signedIn() async {
    c.read(authControllerProvider);
    await Future<void>.delayed(Duration.zero);
  }

  setUp(() {
    repo = FakeUserRepository();
    c = ProviderContainer(
      overrides: [
        userRepositoryProvider.overrideWithValue(repo),
        tokenStoreProvider.overrideWithValue(MemoryTokenStore(validToken)),
      ],
    );
    addTearDown(c.dispose);
  });

  final photo = PickedPhoto(Uint8List.fromList([1, 2, 3]), 'p.jpg');

  test('submits a community report and its photo', () async {
    await signedIn();
    final sub = c.listen(reportControllerProvider, (_, __) {});
    await c.read(reportControllerProvider.notifier).submit(
          parkingId: 12,
          type: ReportType.wrongEntrance,
          zoneId: 20,
          description: '入口在後巷',
          photo: photo,
        );
    final state = sub.read();
    expect(state.status, ReportSubmitStatus.submitted);
    expect(state.photoFailed, isFalse);
    expect(repo.reports.single.type, ReportType.wrongEntrance);
    expect(repo.reports.single.zoneId, 20);
    expect(repo.photos, [(1, 'p.jpg')]);
  });

  test('a failed photo upload keeps the saved report', () async {
    await signedIn();
    repo.photoError = const UserApiException(
      code: 'STORAGE_UNAVAILABLE',
      message: 'down',
      statusCode: 503,
    );
    final sub = c.listen(reportControllerProvider, (_, __) {});
    await c.read(reportControllerProvider.notifier).submit(
          parkingId: 12,
          type: ReportType.closed,
          photo: photo,
        );
    final state = sub.read();
    expect(state.status, ReportSubmitStatus.submitted);
    expect(state.photoFailed, isTrue);
    expect(userErrorMessage(state.error!), contains('回報內容已保存'));
    expect(repo.reports, hasLength(1));
  });

  test('guests cannot submit reports', () async {
    c = ProviderContainer(
      overrides: [
        userRepositoryProvider.overrideWithValue(repo),
        tokenStoreProvider.overrideWithValue(MemoryTokenStore()),
      ],
    );
    addTearDown(c.dispose);
    await signedIn();
    final sub = c.listen(reportControllerProvider, (_, __) {});
    await c.read(reportControllerProvider.notifier).submit(
          parkingId: 12,
          type: ReportType.other,
        );
    expect(sub.read().status, ReportSubmitStatus.failed);
    expect(sub.read().error?.code, UserApiException.unauthenticated);
    expect(repo.reports, isEmpty);
  });
}
