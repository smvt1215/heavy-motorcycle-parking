import 'package:dio/dio.dart';

import '../domain/parking.dart';
import '../domain/place.dart';

class PlacesException implements Exception {
  const PlacesException({
    required this.code,
    required this.message,
    this.statusCode,
  });

  static const String cancelled = 'CANCELLED';
  static const String network = 'NETWORK_ERROR';
  static const String invalidResponse = 'INVALID_RESPONSE';
  static const String unknown = 'UNKNOWN_ERROR';
  static const String notFound = 'PLACE_NOT_FOUND';
  static const String unavailable = 'PLACES_UNAVAILABLE';
  static const String rateLimited = 'RATE_LIMITED';

  final String code;
  final String message;
  final int? statusCode;

  bool get isCancelled => code == cancelled;

  @override
  String toString() => 'PlacesException($code, $statusCode): $message';
}

abstract interface class PlacesRepository {
  /// One billed Autocomplete session spans every keystroke request and the
  /// final [details] call sharing [sessionToken].
  Future<List<PlaceSuggestion>> autocomplete(
    String input, {
    required String sessionToken,
    GeoPoint? bias,
    CancelToken? cancelToken,
  });

  Future<PlaceDestination> details(
    String placeId, {
    required String sessionToken,
    CancelToken? cancelToken,
  });
}

/// Calls our backend proxy; the Google key never ships in the app.
class DioPlacesRepository implements PlacesRepository {
  DioPlacesRepository(this._dio);

  final Dio _dio;

  @override
  Future<List<PlaceSuggestion>> autocomplete(
    String input, {
    required String sessionToken,
    GeoPoint? bias,
    CancelToken? cancelToken,
  }) =>
      _get(
        '/places/autocomplete',
        {
          'input': input,
          'session_token': sessionToken,
          if (bias != null && bias.isValid) ...{
            'lat': bias.lat,
            'lng': bias.lng,
          },
        },
        cancelToken,
        (json) {
          final items = json['suggestions'];
          if (items is! List) throw const FormatException('suggestions');
          return List.unmodifiable([
            for (final item in items)
              PlaceSuggestion.fromJson(
                Map<String, dynamic>.from(item as Map),
              ),
          ]);
        },
      );

  @override
  Future<PlaceDestination> details(
    String placeId, {
    required String sessionToken,
    CancelToken? cancelToken,
  }) =>
      _get(
        '/places/${Uri.encodeComponent(placeId)}',
        {'session_token': sessionToken},
        cancelToken,
        PlaceDestination.fromJson,
      );

  Future<T> _get<T>(
    String path,
    Map<String, dynamic> params,
    CancelToken? cancelToken,
    T Function(Map<String, dynamic>) parse,
  ) async {
    final Response<dynamic> response;
    try {
      response = await _dio.get<dynamic>(
        path,
        queryParameters: params,
        cancelToken: cancelToken,
      );
    } on DioException catch (e) {
      throw _fromDio(e);
    }
    final data = response.data;
    try {
      if (data is! Map) throw const FormatException('Expected a JSON object');
      return parse(Map<String, dynamic>.from(data));
    } on FormatException catch (e) {
      throw PlacesException(
        code: PlacesException.invalidResponse,
        message: e.message,
        statusCode: response.statusCode,
      );
    } on TypeError catch (e) {
      throw PlacesException(
        code: PlacesException.invalidResponse,
        message: e.toString(),
        statusCode: response.statusCode,
      );
    }
  }

  static PlacesException _fromDio(DioException e) {
    if (e.type == DioExceptionType.cancel) {
      return const PlacesException(
        code: PlacesException.cancelled,
        message: 'Request cancelled',
      );
    }
    final response = e.response;
    final data = response?.data;
    if (data is Map && data['error'] is Map) {
      final error = data['error'] as Map;
      final code = error['code'];
      final message = error['message'];
      if (code is String) {
        return PlacesException(
          code: code,
          message: message is String ? message : code,
          statusCode: response?.statusCode,
        );
      }
    }
    if (response == null) {
      return PlacesException(
        code: PlacesException.network,
        message: e.message ?? 'Network error',
      );
    }
    return PlacesException(
      code: PlacesException.unknown,
      message: e.message ?? 'HTTP ${response.statusCode}',
      statusCode: response.statusCode,
    );
  }
}
