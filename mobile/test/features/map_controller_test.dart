import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/parking_repository.dart';
import 'package:heavy_parking/domain/parking.dart';
import 'package:heavy_parking/features/map/map_controller.dart';

import '../fixtures/parking.dart';
import '../fixtures/places.dart';

class _NearbyCall {
  _NearbyCall(this.query, this.cursor);
  final ParkingQuery query;
  final String? cursor;
  final Completer<NearbyParkingPage> completer = Completer();
}

class _DetailCall {
  _DetailCall(this.id, this.vehicle, this.at);
  final int id;
  final VehicleType vehicle;
  final DateTime? at;
  final Completer<ParkingDetail> completer = Completer();
}

/// Records calls and lets each test resolve them in any order.
class _FakeRepository implements ParkingRepository {
  final List<_NearbyCall> nearbyCalls = [];
  final List<_DetailCall> detailCalls = [];

  @override
  Future<NearbyParkingPage> nearby(ParkingQuery query, {String? cursor}) {
    query.validate();
    final call = _NearbyCall(query, cursor);
    nearbyCalls.add(call);
    return call.completer.future;
  }

  @override
  Future<ParkingDetail> detail(int id, VehicleType vehicle, {DateTime? at}) {
    final call = _DetailCall(id, vehicle, at);
    detailCalls.add(call);
    return call.completer.future;
  }

  @override
  Future<ParkingRates> rates(int id, VehicleType vehicle, {DateTime? at}) =>
      throw UnimplementedError();

  @override
  Future<ParkingRealtime> realtime(
    int id,
    VehicleType vehicle, {
    DateTime? at,
  }) =>
      throw UnimplementedError();
}

NearbyParkingPage _page({
  String vehicle = 'LARGE_HEAVY',
  String? nextCursor,
  bool hasMore = false,
  String evaluationAt = fixtureEvaluationAt,
  List<Map<String, dynamic>>? items,
}) =>
    NearbyParkingPage.fromJson(
      nearbyFixture(
        vehicle: vehicle,
        nextCursor: nextCursor,
        hasMore: hasMore,
        evaluationAt: evaluationAt,
        items: items,
      ),
    );

Future<void> _flush() => Future<void>.delayed(Duration.zero);

void main() {
  late _FakeRepository repo;
  late ProviderContainer container;
  late MapController controller;

  MapState state() => container.read(mapControllerProvider);

  setUp(() {
    repo = _FakeRepository();
    container = ProviderContainer(
      overrides: [parkingRepositoryProvider.overrideWithValue(repo)],
    );
    controller = container.read(mapControllerProvider.notifier);
  });

  tearDown(() => container.dispose());

  test('initial state uses the Taipei default center and RED', () {
    expect(state().query.center, const GeoPoint(25.033, 121.5654));
    expect(state().query.vehicle, VehicleType.largeHeavy);
    expect(state().hasSearched, isFalse);
    expect(repo.nearbyCalls, isEmpty);
  });

  test('search loads results and filters UNKNOWN zones by default', () async {
    final future = controller.search();
    expect(state().isLoading, isTrue);
    repo.nearbyCalls.single.completer.complete(_page());
    await future;

    expect(state().isLoading, isFalse);
    expect(state().items.map((l) => l.id), [12345]);
    expect(state().items.single.zones.map((z) => z.zoneId), [20]);
    // Server aggregate is preserved untouched.
    expect(
      state().items.single.availabilitySummary!.status,
      AvailabilityStatus.available,
    );
    expect(state().evaluationAt, DateTime.utc(2026, 10, 2, 9, 30));
    expect(state().sortVersion, 1);
  });

  test('destination search queries our backend at the destination', () async {
    final filtered = controller.setFilters(
      state().query.copyWith(vehicle: VehicleType.normalHeavy, radius: 800),
    );
    repo.nearbyCalls.single.completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
    await filtered;
    controller.cameraMoved(const GeoPoint(25.1, 121.6));
    controller.cameraIdle();
    expect(state().needsAreaSearch, isTrue);

    final future = controller.searchDestination(taipei101);
    expect(state().destination, same(taipei101));
    expect(state().needsAreaSearch, isFalse);
    final call = repo.nearbyCalls.last;
    expect(call.cursor, isNull);
    expect(call.query.center, taipei101.location);
    // Destination search keeps the explicit vehicle and filters.
    expect(call.query.vehicle, VehicleType.normalHeavy);
    expect(call.query.radius, 800);
    call.completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
    await future;
    expect(state().items, isNotEmpty);

    // The camera animating onto the destination is not a user move.
    controller.cameraMoved(
      GeoPoint(taipei101.location.lat + 0.000001, taipei101.location.lng),
    );
    controller.cameraIdle();
    expect(state().needsAreaSearch, isFalse);

    controller.clearDestination();
    expect(state().destination, isNull);
    expect(state().query.center, taipei101.location);
  });

  test('focusing a favorite replaces an active destination', () async {
    final destination = controller.searchDestination(taipei101);
    repo.nearbyCalls.last.completer.complete(_page());
    await destination;
    expect(state().destination, isNotNull);

    final focus = controller.focus(const GeoPoint(25.05, 121.55));
    expect(state().destination, isNull);
    expect(state().cameraRequest!.target, const GeoPoint(25.05, 121.55));
    expect(repo.nearbyCalls.last.query.center, const GeoPoint(25.05, 121.55));
    repo.nearbyCalls.last.completer.complete(_page());
    await focus;
  });

  test('a later area search keeps the destination marker', () async {
    final first = controller.searchDestination(taipei101);
    repo.nearbyCalls.last.completer.complete(_page());
    await first;
    controller.cameraMoved(const GeoPoint(25.05, 121.57));
    controller.cameraIdle();
    final second = controller.search();
    repo.nearbyCalls.last.completer.complete(_page());
    await second;
    expect(state().query.center, const GeoPoint(25.05, 121.57));
    expect(state().destination, same(taipei101));
  });

  test('camera frames and idle never call the API', () async {
    for (var i = 0; i < 50; i++) {
      controller.cameraMoved(GeoPoint(25.033 + i * 0.0001, 121.5654));
    }
    expect(state().needsAreaSearch, isFalse);
    controller.cameraIdle();
    await _flush();
    expect(repo.nearbyCalls, isEmpty);
    expect(state().needsAreaSearch, isTrue);
  });

  test('idle at the searched center does not prompt area search', () {
    controller.cameraMoved(state().query.center);
    controller.cameraIdle();
    expect(state().needsAreaSearch, isFalse);
  });

  test('explicit search uses the pending camera center and clears prompt',
      () async {
    const moved = GeoPoint(25.05, 121.52);
    controller
      ..cameraMoved(moved)
      ..cameraIdle();
    final future = controller.search();
    expect(repo.nearbyCalls.single.query.center, moved);
    repo.nearbyCalls.single.completer.complete(_page());
    await future;
    expect(state().query.center, moved);
    expect(state().needsAreaSearch, isFalse);
  });

  test('filter change uses the searched center, not the moving camera',
      () async {
    final first = controller.search();
    repo.nearbyCalls.last.completer.complete(_page());
    await first;
    final searched = state().query.center;

    controller
      ..cameraMoved(const GeoPoint(25.1, 121.6))
      ..cameraIdle();
    final next = controller.setFilters(
      state().query.copyWith(
            center: const GeoPoint(0, 0),
            availableOnly: true,
            spaceType: SpaceType.heavyOnly,
          ),
    );
    final call = repo.nearbyCalls.last;
    expect(call.query.center, searched);
    expect(call.query.availableOnly, isTrue);
    expect(call.query.spaceType, SpaceType.heavyOnly);
    // The area-search prompt still reflects the unsearched camera move.
    expect(state().needsAreaSearch, isTrue);
    call.completer.complete(_page());
    await next;
  });

  test('LIGHT_MOTO_ONLY filter is rejected without a request', () async {
    await expectLater(
      controller.setFilters(
        state().query.copyWith(spaceType: SpaceType.lightMotoOnly),
      ),
      throwsArgumentError,
    );
    expect(repo.nearbyCalls, isEmpty);
    expect(state().query.spaceType, isNull);
  });

  test('vehicle and filters persist across reads and later searches', () async {
    final v = controller.setVehicle(VehicleType.normalHeavy);
    repo.nearbyCalls.last.completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
    await v;
    final f = controller.setFilters(
      state().query.copyWith(includeUnknown: true, radius: 3000),
    );
    repo.nearbyCalls.last.completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
    await f;

    // Re-reading the persistent provider (e.g. after a widget rebuild).
    final again = container.read(mapControllerProvider);
    expect(again.query.vehicle, VehicleType.normalHeavy);
    expect(again.query.includeUnknown, isTrue);
    expect(again.query.radius, 3000);
    expect(again.items.map((l) => l.id), [12345, 12346]);

    final s = controller.search();
    final call = repo.nearbyCalls.last;
    expect(call.query.vehicle, VehicleType.normalHeavy);
    expect(call.query.includeUnknown, isTrue);
    call.completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
    await s;
  });

  test('setting the same vehicle is a no-op', () async {
    await controller.setVehicle(VehicleType.largeHeavy);
    expect(repo.nearbyCalls, isEmpty);
  });

  for (final space in SpaceType.searchable) {
    test('vehicle changes preserve compatible ${space.wireValue} filters',
        () async {
      final filtered =
          controller.setFilters(state().query.copyWith(spaceType: space));
      repo.nearbyCalls.last.completer.complete(_page());
      await filtered;
      final normal = controller.setVehicle(VehicleType.normalHeavy);
      expect(repo.nearbyCalls.last.query.spaceType, space);
      repo.nearbyCalls.last.completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
      await normal;
      final large = controller.setVehicle(VehicleType.largeHeavy);
      expect(repo.nearbyCalls.last.query.spaceType, space);
      repo.nearbyCalls.last.completer.complete(_page());
      await large;
    });
  }

  test('large vehicle change clears only the invalid conventional-only filter',
      () async {
    final normal = controller.setVehicle(VehicleType.normalHeavy);
    repo.nearbyCalls.last.completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
    await normal;
    final filtered = controller
        .setFilters(state().query.copyWith(spaceType: SpaceType.lightMotoOnly));
    repo.nearbyCalls.last.completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
    await filtered;
    final large = controller.setVehicle(VehicleType.largeHeavy);
    expect(repo.nearbyCalls.last.query.spaceType, isNull);
    repo.nearbyCalls.last.completer.complete(_page());
    await large;
  });

  test('out-of-order responses: stale vehicle response is discarded', () async {
    final red = controller.search();
    final yellow = controller.setVehicle(VehicleType.normalHeavy);
    expect(repo.nearbyCalls, hasLength(2));

    repo.nearbyCalls[1].completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
    await yellow;
    repo.nearbyCalls[0].completer.complete(
      _page(items: [lotJson(id: 999)]),
    );
    await red;

    expect(state().query.vehicle, VehicleType.normalHeavy);
    expect(state().items.map((l) => l.id), [12345]);
    expect(state().items.single.compatibilityVehicle, VehicleType.normalHeavy);
  });

  test('stale error after a newer search is discarded', () async {
    final a = controller.search();
    final b = controller.setFilters(state().query.copyWith(radius: 500));
    repo.nearbyCalls[1].completer.complete(_page());
    await b;
    repo.nearbyCalls[0].completer.completeError(
      const ParkingApiException(code: 'NETWORK_ERROR', message: 'x'),
    );
    await a;
    expect(state().error, isNull);
    expect(state().items, isNotEmpty);
  });

  test('unexpected repository failure finishes search loading', () async {
    final search = controller.search();
    repo.nearbyCalls.single.completer.completeError(StateError('failure'));
    await search;
    expect(state().isLoading, isFalse);
    expect(state().error!.code, ParkingApiException.unknown);
  });

  test('unexpected stale failure does not replace a newer search', () async {
    final oldSearch = controller.search();
    final search = controller.setVehicle(VehicleType.normalHeavy);
    repo.nearbyCalls.last.completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
    await search;
    repo.nearbyCalls.first.completer.completeError(StateError('old failure'));
    await oldSearch;
    expect(state().error, isNull);
    expect(state().items.single.compatibilityVehicle, VehicleType.normalHeavy);
  });

  group('pagination', () {
    Future<void> firstPage({DateTime? at}) async {
      if (at != null) {
        final f = controller.setFilters(state().query.copyWith(at: at));
        repo.nearbyCalls.last.completer
            .complete(_page(nextCursor: 'c1', hasMore: true));
        await f;
      } else {
        final f = controller.search();
        repo.nearbyCalls.last.completer
            .complete(_page(nextCursor: 'c1', hasMore: true));
        await f;
      }
    }

    test('continuation reuses the first query without adding at', () async {
      await firstPage();
      controller
        ..cameraMoved(const GeoPoint(25.2, 121.7))
        ..cameraIdle();
      final more = controller.loadMore();
      final call = repo.nearbyCalls.last;
      expect(call.cursor, 'c1');
      expect(call.query, repo.nearbyCalls.first.query);
      expect(call.query.at, isNull);
      call.completer.complete(
        _page(items: [lotJson(id: 777, distanceM: 900)]),
      );
      await more;
      expect(state().items.map((l) => l.id), [12345, 777]);
      expect(state().hasMore, isFalse);
      expect(state().nextCursor, isNull);
      // evaluation_at stays pinned to the first page.
      expect(state().evaluationAt, DateTime.utc(2026, 10, 2, 9, 30));
    });

    test('explicit at is kept on continuation', () async {
      final at = DateTime.utc(2026, 10, 3, 1);
      await firstPage(at: at);
      final more = controller.loadMore();
      expect(repo.nearbyCalls.last.query.at, at);
      repo.nearbyCalls.last.completer.complete(_page());
      await more;
    });

    test('invalid cursor surfaces an error and never restarts page one',
        () async {
      await firstPage();
      final callsBefore = repo.nearbyCalls.length;
      final more = controller.loadMore();
      repo.nearbyCalls.last.completer.completeError(
        const ParkingApiException(
          code: ParkingApiException.invalidCursor,
          message: 'The pagination cursor is invalid.',
          statusCode: 400,
        ),
      );
      await more;
      // No silent page-one request (cursor == null) after the failure.
      expect(repo.nearbyCalls, hasLength(callsBefore + 1));
      expect(repo.nearbyCalls.last.cursor, 'c1');
      expect(state().error!.isCursorError, isTrue);
      expect(state().items.map((l) => l.id), [12345]);
      expect(state().hasMore, isFalse);
      expect(state().nextCursor, isNull);

      await controller.loadMore();
      expect(repo.nearbyCalls, hasLength(callsBefore + 1));
    });

    test('network error keeps the cursor for retry', () async {
      await firstPage();
      final more = controller.loadMore();
      repo.nearbyCalls.last.completer.completeError(
        const ParkingApiException(code: 'NETWORK_ERROR', message: 'offline'),
      );
      await more;
      expect(state().nextCursor, 'c1');
      expect(state().hasMore, isTrue);
    });

    for (final mismatch in ['evaluation_at', 'sort_version']) {
      test('$mismatch mismatch keeps loaded items and stops paging', () async {
        await firstPage();
        final more = controller.loadMore();
        final json = nearbyFixture(items: [lotJson(id: 777)]);
        json[mismatch] =
            mismatch == 'evaluation_at' ? '2026-10-02T10:30:00Z' : 2;
        repo.nearbyCalls.last.completer.complete(
          NearbyParkingPage.fromJson(json),
        );
        await more;
        expect(state().items.map((lot) => lot.id), [12345]);
        expect(state().error!.code, ParkingApiException.invalidResponse);
        expect(state().isLoadingMore, isFalse);
        expect(state().hasMore, isFalse);
        expect(state().nextCursor, isNull);
        final calls = repo.nearbyCalls.length;
        await controller.loadMore();
        expect(repo.nearbyCalls, hasLength(calls));
      });
    }

    test('unexpected continuation failure finishes loading and permits retry',
        () async {
      await firstPage();
      final more = controller.loadMore();
      repo.nearbyCalls.last.completer.completeError(StateError('failure'));
      await more;
      expect(state().error!.code, ParkingApiException.unknown);
      expect(state().isLoadingMore, isFalse);
      expect(state().items.map((lot) => lot.id), [12345]);
      expect(state().hasMore, isTrue);
      expect(state().nextCursor, 'c1');
    });

    test('loadMore response is dropped after a new search', () async {
      await firstPage();
      final more = controller.loadMore();
      final search = controller.setVehicle(VehicleType.normalHeavy);
      repo.nearbyCalls.last.completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
      await search;
      repo.nearbyCalls[1].completer.complete(_page(items: [lotJson(id: 5)]));
      await more;
      expect(state().items.map((l) => l.id), [12345]);
      expect(state().query.vehicle, VehicleType.normalHeavy);
    });

    test('no concurrent loadMore calls', () async {
      await firstPage();
      unawaited(controller.loadMore());
      unawaited(controller.loadMore());
      expect(repo.nearbyCalls.where((c) => c.cursor != null), hasLength(1));
    });
  });

  group('detail', () {
    Future<ParkingLot> loaded() async {
      final f = controller.search();
      repo.nearbyCalls.last.completer.complete(_page());
      await f;
      return state().items.single;
    }

    test('requests detail with selected vehicle and pinned evaluationAt',
        () async {
      final lot = await loaded();
      final f = controller.selectLot(lot);
      expect(state().selectedLot, same(lot));
      expect(state().detailLoading, isTrue);
      final call = repo.detailCalls.single;
      expect(call.id, 12345);
      expect(call.vehicle, VehicleType.largeHeavy);
      expect(call.at, DateTime.utc(2026, 10, 2, 9, 30));
      call.completer.complete(ParkingDetail.fromJson(detailFixture()));
      await f;
      expect(state().detailLoading, isFalse);
      expect(state().detail!.navigationTarget.entrance!.id, 301);
    });

    test('stale detail response for a previous selection is dropped', () async {
      final lot = await loaded();
      final other = ParkingLot.fromJson(lotJson(id: 12346));
      final first = controller.selectLot(lot);
      final second = controller.selectLot(other);
      repo.detailCalls[1].completer.complete(
        ParkingDetail.fromJson(detailFixture(id: other.id)),
      );
      await second;
      repo.detailCalls[0].completer.complete(
        ParkingDetail.fromJson(detailFixture(id: lot.id)),
      );
      await first;
      expect(state().detail!.id, other.id);
    });

    for (final mismatch in ['id', 'vehicle', 'evaluation_at']) {
      test('$mismatch mismatch rejects the detail response', () async {
        final lot = await loaded();
        final detail = controller.selectLot(lot);
        final json = detailFixture();
        json[mismatch] = switch (mismatch) {
          'id' => 999,
          'vehicle' => 'NORMAL_HEAVY',
          _ => '2026-10-02T10:30:00Z',
        };
        repo.detailCalls.single.completer
            .complete(ParkingDetail.fromJson(json));
        await detail;
        expect(state().detail, isNull);
        expect(state().detailError!.code, ParkingApiException.invalidResponse);
        expect(state().detailLoading, isFalse);
      });
    }

    test('unexpected detail failure finishes loading', () async {
      final lot = await loaded();
      final detail = controller.selectLot(lot);
      repo.detailCalls.single.completer.completeError(StateError('failure'));
      await detail;
      expect(state().detail, isNull);
      expect(state().detailError!.code, ParkingApiException.unknown);
      expect(state().detailLoading, isFalse);
    });

    test('closeSelection drops an in-flight detail', () async {
      final lot = await loaded();
      final f = controller.selectLot(lot);
      controller.closeSelection();
      repo.detailCalls.single.completer
          .complete(ParkingDetail.fromJson(detailFixture()));
      await f;
      expect(state().selectedLot, isNull);
      expect(state().detail, isNull);
      expect(state().detailLoading, isFalse);
    });

    test('vehicle change clears selection and drops in-flight detail',
        () async {
      final lot = await loaded();
      final f = controller.selectLot(lot);
      final v = controller.setVehicle(VehicleType.normalHeavy);
      expect(state().selectedLot, isNull);
      repo.detailCalls.single.completer
          .complete(ParkingDetail.fromJson(detailFixture()));
      await f;
      expect(state().detail, isNull);
      repo.nearbyCalls.last.completer.complete(_page(vehicle: 'NORMAL_HEAVY'));
      await v;
    });

    test('detail error is reported separately from search error', () async {
      final lot = await loaded();
      final f = controller.selectLot(lot);
      repo.detailCalls.single.completer.completeError(
        const ParkingApiException(code: 'NOT_FOUND', message: 'gone'),
      );
      await f;
      expect(state().detailError!.code, 'NOT_FOUND');
      expect(state().error, isNull);
      expect(state().detailLoading, isFalse);
    });
  });

  test('responses after dispose are ignored', () async {
    final f = controller.search();
    container.dispose();
    repo.nearbyCalls.single.completer.complete(_page());
    await f;
    // Recreate so tearDown can dispose safely.
    container = ProviderContainer(
      overrides: [parkingRepositoryProvider.overrideWithValue(repo)],
    );
  });
}
