import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/places_repository.dart';
import 'package:heavy_parking/domain/parking.dart';

class _FakeAdapter implements HttpClientAdapter {
  _FakeAdapter(this.respond);

  final (int, Object?) Function(RequestOptions options) respond;
  final List<RequestOptions> requests = [];
  Completer<void>? hold;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);
    if (hold case final hold?) {
      await Future.any([hold.future, if (cancelFuture != null) cancelFuture]);
    }
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

(DioPlacesRepository, _FakeAdapter) _repo(
  (int, Object?) Function(RequestOptions options) respond,
) {
  final adapter = _FakeAdapter(respond);
  final dio = Dio(BaseOptions(baseUrl: 'http://api.test/api/v1'))
    ..httpClientAdapter = adapter;
  return (DioPlacesRepository(dio), adapter);
}

const token = '0b6f5c1e-7a52-4c4f-9a7e-3d2b1c0a9f88';

void main() {
  test('autocomplete calls our backend proxy with session and bias', () async {
    final (repo, adapter) = _repo(
      (_) => (
        200,
        {
          'suggestions': [
            {
              'place_id': 'ChIJ101',
              'primary_text': '台北101',
              'secondary_text': '信義區',
            },
          ],
          'attribution': 'GOOGLE',
        }
      ),
    );
    final result = await repo.autocomplete(
      '台北101',
      sessionToken: token,
      bias: const GeoPoint(25.03, 121.56),
    );
    expect(result.single.placeId, 'ChIJ101');
    final request = adapter.requests.single;
    expect(request.uri.host, 'api.test');
    expect(request.uri.path, '/api/v1/places/autocomplete');
    expect(request.queryParameters, {
      'input': '台北101',
      'session_token': token,
      'lat': 25.03,
      'lng': 121.56,
    });
  });

  test('details resolves coordinates with the same session token', () async {
    final (repo, adapter) = _repo(
      (_) => (
        200,
        {
          'place_id': 'ChIJ101',
          'name': '台北101',
          'address': null,
          'location': {'lat': 25.0339639, 'lng': 121.5644722},
          'attribution': 'GOOGLE',
        }
      ),
    );
    final place = await repo.details('ChIJ101', sessionToken: token);
    expect(place.location, const GeoPoint(25.0339639, 121.5644722));
    expect(adapter.requests.single.uri.path, '/api/v1/places/ChIJ101');
    expect(adapter.requests.single.queryParameters, {'session_token': token});
  });

  test('backend error envelope and malformed payloads become stable errors',
      () async {
    final (repo, _) = _repo(
      (_) => (
        503,
        {
          'error': {'code': 'PLACES_UNAVAILABLE', 'message': 'off'},
        }
      ),
    );
    await expectLater(
      repo.autocomplete('台北', sessionToken: token),
      throwsA(
        isA<PlacesException>()
            .having((e) => e.code, 'code', PlacesException.unavailable)
            .having((e) => e.statusCode, 'status', 503),
      ),
    );

    final (bad, _) = _repo((_) => (200, {'suggestions': 'nope'}));
    await expectLater(
      bad.autocomplete('台北', sessionToken: token),
      throwsA(
        isA<PlacesException>().having(
          (e) => e.code,
          'code',
          PlacesException.invalidResponse,
        ),
      ),
    );
  });

  test('cancel token aborts an in-flight request as CANCELLED', () async {
    final (repo, adapter) = _repo((_) => (200, {'suggestions': []}));
    adapter.hold = Completer<void>();
    final cancel = CancelToken();
    final future = repo.autocomplete(
      '台北',
      sessionToken: token,
      cancelToken: cancel,
    );
    cancel.cancel();
    await expectLater(
      future,
      throwsA(isA<PlacesException>().having((e) => e.isCancelled, 'c', true)),
    );
  });
}
