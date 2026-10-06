import 'package:dio/dio.dart';

import '../domain/parking.dart';

/// Stable machine-readable error from the backend error envelope, or a
/// client-side code for transport/parse failures.
class ParkingApiException implements Exception {
  const ParkingApiException({
    required this.code,
    required this.message,
    this.statusCode,
  });

  static const String invalidCursor = 'INVALID_CURSOR';
  static const String cursorQueryMismatch = 'CURSOR_QUERY_MISMATCH';
  static const String cursorVersionUnsupported = 'CURSOR_VERSION_UNSUPPORTED';
  static const String network = 'NETWORK_ERROR';
  static const String invalidResponse = 'INVALID_RESPONSE';
  static const String unknown = 'UNKNOWN_ERROR';

  final String code;
  final String message;
  final int? statusCode;

  bool get isCursorError =>
      code == invalidCursor ||
      code == cursorQueryMismatch ||
      code == cursorVersionUnsupported;

  @override
  String toString() => 'ParkingApiException($code, $statusCode): $message';
}

abstract interface class ParkingRepository {
  /// First page when [cursor] is null. Continuations must pass the exact same
  /// [query] as the first page; the server binds the cursor to it and to the
  /// pinned evaluation instant.
  Future<NearbyParkingPage> nearby(ParkingQuery query, {String? cursor});

  Future<ParkingDetail> detail(int id, VehicleType vehicle, {DateTime? at});

  Future<ParkingRates> rates(int id, VehicleType vehicle, {DateTime? at});

  Future<ParkingRealtime> realtime(int id, VehicleType vehicle, {DateTime? at});
}

/// Calls the app backend only (never government APIs). The injected [Dio]
/// must have `baseUrl` set to the API base path, e.g. `.../api/v1`.
class DioParkingRepository implements ParkingRepository {
  DioParkingRepository(this._dio);

  final Dio _dio;

  @override
  Future<NearbyParkingPage> nearby(ParkingQuery query, {String? cursor}) {
    final params = query.toQueryParameters();
    if (cursor != null) {
      if (cursor.isEmpty) {
        throw ArgumentError.value(cursor, 'cursor', 'must not be empty');
      }
      params['cursor'] = cursor;
    }
    return _get('/parking/nearby', params, NearbyParkingPage.fromJson);
  }

  @override
  Future<ParkingDetail> detail(int id, VehicleType vehicle, {DateTime? at}) =>
      _get('/parking/$id', _vehicleParams(vehicle, at), ParkingDetail.fromJson);

  @override
  Future<ParkingRates> rates(int id, VehicleType vehicle, {DateTime? at}) =>
      _get(
        '/parking/$id/rates',
        _vehicleParams(vehicle, at),
        ParkingRates.fromJson,
      );

  @override
  Future<ParkingRealtime> realtime(
    int id,
    VehicleType vehicle, {
    DateTime? at,
  }) =>
      _get(
        '/parking/$id/realtime',
        _vehicleParams(vehicle, at),
        ParkingRealtime.fromJson,
      );

  static Map<String, dynamic> _vehicleParams(
    VehicleType vehicle,
    DateTime? at,
  ) =>
      {
        'vehicle': vehicle.wireValue,
        if (at != null) 'at': at.toUtc().toIso8601String(),
      };

  Future<T> _get<T>(
    String path,
    Map<String, dynamic> params,
    T Function(Map<String, dynamic>) parse,
  ) async {
    final Response<dynamic> response;
    try {
      response = await _dio.get<dynamic>(path, queryParameters: params);
    } on DioException catch (e) {
      throw _fromDio(e);
    }
    final data = response.data;
    try {
      if (data is! Map) {
        throw const FormatException('Expected a JSON object');
      }
      return parse(Map<String, dynamic>.from(data));
    } on FormatException catch (e) {
      throw ParkingApiException(
        code: ParkingApiException.invalidResponse,
        message: e.message,
        statusCode: response.statusCode,
      );
    } on TypeError catch (e) {
      throw ParkingApiException(
        code: ParkingApiException.invalidResponse,
        message: e.toString(),
        statusCode: response.statusCode,
      );
    }
  }

  static ParkingApiException _fromDio(DioException e) {
    final response = e.response;
    final data = response?.data;
    if (data is Map && data['error'] is Map) {
      final error = data['error'] as Map;
      final code = error['code'];
      final message = error['message'];
      if (code is String) {
        return ParkingApiException(
          code: code,
          message: message is String ? message : code,
          statusCode: response?.statusCode,
        );
      }
    }
    if (response == null) {
      return ParkingApiException(
        code: ParkingApiException.network,
        message: e.message ?? 'Network error',
      );
    }
    return ParkingApiException(
      code: ParkingApiException.unknown,
      message: e.message ?? 'HTTP ${response.statusCode}',
      statusCode: response.statusCode,
    );
  }
}
