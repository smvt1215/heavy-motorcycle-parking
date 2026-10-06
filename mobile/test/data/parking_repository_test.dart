import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/parking_repository.dart';
import 'package:heavy_parking/domain/parking.dart';

import '../fixtures/parking.dart';

class _FakeAdapter implements HttpClientAdapter {
  _FakeAdapter(this.respond);

  final (int, Object?) Function(RequestOptions options) respond;
  final List<RequestOptions> requests = [];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);
    final (status, body) = respond(options);
    return ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

(DioParkingRepository, _FakeAdapter) _repo(
  (int, Object?) Function(RequestOptions options) respond,
) {
  final adapter = _FakeAdapter(respond);
  final dio = Dio(BaseOptions(baseUrl: 'http://api.test/api/v1'))
    ..httpClientAdapter = adapter;
  return (DioParkingRepository(dio), adapter);
}

void main() {
  const center = GeoPoint(25.033, 121.5654);

  test('nearby sends the exact parameter contract to /parking/nearby',
      () async {
    final (repo, adapter) = _repo((_) => (200, nearbyFixture()));
    final page = await repo.nearby(
      const ParkingQuery(
        center: center,
        vehicle: VehicleType.yellow,
        radius: 500,
        spaceType: SpaceType.heavyOnly,
        availableOnly: true,
        hourlyRateMaxTwd: 20,
      ),
    );
    final request = adapter.requests.single;
    expect(request.uri.path, '/api/v1/parking/nearby');
    expect(request.method, 'GET');
    expect(request.uri.queryParameters, {
      'lat': '25.033',
      'lng': '121.5654',
      'vehicle': 'YELLOW',
      'radius': '500',
      'space_type': 'HEAVY_ONLY',
      'available_only': 'true',
      'hourly_rate_max_twd': '20',
    });
    expect(page.items, hasLength(2));
    expect(page.evaluationAt, DateTime.utc(2026, 10, 2, 9, 30));
  });

  test('continuation reuses identical parameters plus the cursor', () async {
    final (repo, adapter) = _repo((_) => (200, nearbyFixture()));
    const query = ParkingQuery(center: center, includeUnknown: true);
    await repo.nearby(query);
    await repo.nearby(query, cursor: 'opaque-token');
    final first = adapter.requests[0].uri.queryParameters;
    final second = Map.of(adapter.requests[1].uri.queryParameters);
    expect(second.remove('cursor'), 'opaque-token');
    expect(second, first);
    expect(first.containsKey('at'), isFalse);
  });

  test('LIGHT_MOTO_ONLY search never reaches the network', () async {
    final (repo, adapter) = _repo((_) => (200, nearbyFixture()));
    expect(
      () => repo.nearby(
        const ParkingQuery(center: center, spaceType: SpaceType.lightMotoOnly),
      ),
      throwsArgumentError,
    );
    expect(adapter.requests, isEmpty);
  });

  test('detail sends explicit vehicle and pinned at', () async {
    final (repo, adapter) = _repo((_) => (200, detailFixture()));
    final detail = await repo.detail(
      12345,
      VehicleType.red,
      at: DateTime.utc(2026, 10, 2, 9, 30),
    );
    final request = adapter.requests.single;
    expect(request.uri.path, '/api/v1/parking/12345');
    expect(request.uri.queryParameters, {
      'vehicle': 'RED',
      'at': '2026-10-02T09:30:00.000Z',
    });
    expect(detail.navigationTarget.isConfirmedEntrance, isTrue);
  });

  test('detail without at sends vehicle only', () async {
    final (repo, adapter) =
        _repo((_) => (200, detailFixture(vehicle: 'YELLOW')));
    final detail = await repo.detail(7, VehicleType.yellow);
    expect(adapter.requests.single.uri.queryParameters, {'vehicle': 'YELLOW'});
    expect(detail.vehicle, VehicleType.yellow);
  });

  test('realtime and rates carry vehicle', () async {
    final (repo, adapter) = _repo((options) {
      if (options.uri.path.endsWith('/realtime')) {
        return (
          200,
          {
            'parking_id': 1,
            'vehicle': 'RED',
            'evaluation_at': fixtureEvaluationAt,
            'zones': [zoneJson(availability: availabilityJson())],
            'availability_summary': summaryJson(),
          },
        );
      }
      return (
        200,
        {
          'parking_id': 1,
          'vehicle': 'RED',
          'evaluation_at': fixtureEvaluationAt,
          'zones': [
            {
              ...zoneJson(rateSummary: rateSummaryJson()),
              'rates': [
                {
                  'rate_id': 901,
                  'rate_type': 'HOURLY',
                  'currency': 'TWD',
                  'base_amount': 20,
                  'unit_minutes': 60,
                  'free_minutes': 0,
                  'daily_max_twd': 100,
                  'description': '20元/小時',
                  'raw_text': null,
                  'parse_status': 'PARSED',
                  'applicability': 'MATCH',
                  'provenance': sourceJson(sourceId: 7),
                  'supporting_sources': <Object>[],
                },
              ],
            },
          ],
        },
      );
    });
    final realtime = await repo.realtime(1, VehicleType.red);
    final rates = await repo.rates(1, VehicleType.red);
    expect(adapter.requests.map((r) => r.uri.path), [
      '/api/v1/parking/1/realtime',
      '/api/v1/parking/1/rates',
    ]);
    for (final r in adapter.requests) {
      expect(r.uri.queryParameters['vehicle'], 'RED');
    }
    expect(realtime.availabilitySummary!.canShowTotals, isTrue);
    expect(rates.zones.single.rates.single.applicability, 'MATCH');
    expect(rates.zones.single.zone.rateSummary!.confirmedHourlyRateTwd, 20);
  });

  test('error envelope maps to a stable cursor error', () async {
    final (repo, _) = _repo(
      (_) => (
        400,
        {
          'error': {
            'code': 'INVALID_CURSOR',
            'message': 'The pagination cursor is invalid.',
          },
        },
      ),
    );
    await expectLater(
      repo.nearby(const ParkingQuery(center: center), cursor: 'bad'),
      throwsA(
        isA<ParkingApiException>()
            .having((e) => e.code, 'code', 'INVALID_CURSOR')
            .having((e) => e.statusCode, 'statusCode', 400)
            .having((e) => e.isCursorError, 'isCursorError', isTrue),
      ),
    );
  });

  test('malformed body maps to INVALID_RESPONSE', () async {
    final (repo, _) = _repo((_) => (200, {'items': 'nope'}));
    await expectLater(
      repo.nearby(const ParkingQuery(center: center)),
      throwsA(
        isA<ParkingApiException>().having(
          (e) => e.code,
          'code',
          ParkingApiException.invalidResponse,
        ),
      ),
    );
  });
}
