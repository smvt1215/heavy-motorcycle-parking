import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/parking_repository.dart';
import 'package:heavy_parking/data/user_repository.dart';
import 'package:heavy_parking/domain/parking.dart';
import 'package:heavy_parking/domain/user.dart';

import '../fixtures/parking.dart';

class _Adapter implements HttpClientAdapter {
  _Adapter(this.respond);
  final (int, Object?) Function(RequestOptions) respond;
  final requests = <RequestOptions>[];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);
    final (status, body) = respond(options);
    return ResponseBody.fromString(
      body == null ? '' : jsonEncode(body),
      status,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

Dio _dio(_Adapter adapter) =>
    Dio(BaseOptions(baseUrl: 'http://api.test/api/v1'))
      ..httpClientAdapter = adapter;

const token = 'hmp_token';

void main() {
  test('user-scoped calls send the bearer token and never a user ID', () async {
    final adapter = _Adapter(
      (o) => switch (o.path) {
        '/me' => (
            200,
            {
              'id': 7,
              'display_name': 'rider',
              'email': null,
              'role': 'USER',
              'preferred_vehicle': 'RED',
            }
          ),
        '/favorites' when o.method == 'GET' => (
            200,
            {
              'items': [
                {
                  'parking_id': 12,
                  'name': 'Lot',
                  'location': {'lat': 25.0, 'lng': 121.5},
                  'created_at': '2026-10-07T04:00:00Z',
                },
              ],
            }
          ),
        _ => (201, {'parking_id': 12}),
      },
    );
    final repo = DioUserRepository(_dio(adapter));
    final me = await repo.me(token);
    expect(me.preferredVehicle, VehicleType.red);
    final favorites = await repo.favorites(token);
    expect(favorites.single.parkingId, 12);
    await repo.addFavorite(token, 12);

    for (final request in adapter.requests) {
      expect(request.headers['Authorization'], 'Bearer $token');
      expect(request.queryParameters, isNot(contains('user_id')));
    }
    expect(adapter.requests.last.data, {'parking_id': 12});
  });

  test('public parking requests stay anonymous', () async {
    final adapter = _Adapter((_) => (200, nearbyFixture()));
    await DioParkingRepository(_dio(adapter)).nearby(
      const ParkingQuery(center: GeoPoint(25.03, 121.56)),
    );
    expect(adapter.requests.single.headers, isNot(contains('Authorization')));
  });

  test('401 envelope maps to UNAUTHENTICATED', () async {
    final adapter = _Adapter(
      (_) => (
        401,
        {
          'error': {'code': 'UNAUTHENTICATED', 'message': 'no'},
        }
      ),
    );
    await expectLater(
      DioUserRepository(_dio(adapter)).favorites(token),
      throwsA(
        isA<UserApiException>()
            .having((e) => e.isUnauthenticated, 'unauthenticated', isTrue),
      ),
    );
  });

  test('reports are parsed only as community provenance', () async {
    final body = {
      'id': 1,
      'parking_id': 12,
      'zone_id': null,
      'report_type': 'WRONG_RATE',
      'status': 'PENDING',
      'description': null,
      'photo_count': 0,
      'created_at': '2026-10-07T04:00:00Z',
      'resolved_at': null,
      'provenance': {'source_type': 'COMMUNITY'},
    };
    final adapter = _Adapter((_) => (201, body));
    final report = await DioUserRepository(_dio(adapter)).createReport(
      token,
      parkingId: 12,
      type: ReportType.wrongRate,
      description: '  ',
    );
    expect(report.type, ReportType.wrongRate);
    expect(adapter.requests.single.data, {
      'parking_id': 12,
      'report_type': 'WRONG_RATE',
    });
    expect(
      () => CommunityReport.fromJson({
        ...body,
        'provenance': {'source_type': 'GOVERNMENT'},
      }),
      throwsFormatException,
    );
  });

  test('photo upload is multipart with the bearer token', () async {
    final adapter = _Adapter((_) => (201, {'id': 1}));
    await DioUserRepository(_dio(adapter)).uploadPhoto(
      token,
      3,
      Uint8List.fromList([1, 2, 3]),
      'p.jpg',
    );
    final request = adapter.requests.single;
    expect(request.path, '/reports/3/photos');
    expect(request.data, isA<FormData>());
    expect(request.headers['Authorization'], 'Bearer $token');
  });
}
