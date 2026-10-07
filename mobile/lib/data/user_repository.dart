import 'dart:typed_data';

import 'package:dio/dio.dart';

import '../domain/parking.dart';
import '../domain/user.dart';

class UserApiException implements Exception {
  const UserApiException({
    required this.code,
    required this.message,
    this.statusCode,
  });

  static const String unauthenticated = 'UNAUTHENTICATED';
  static const String forbidden = 'FORBIDDEN';
  static const String network = 'NETWORK_ERROR';
  static const String invalidResponse = 'INVALID_RESPONSE';
  static const String unknown = 'UNKNOWN_ERROR';

  final String code;
  final String message;
  final int? statusCode;

  bool get isUnauthenticated => code == unauthenticated || statusCode == 401;

  @override
  String toString() => 'UserApiException($code, $statusCode): $message';
}

class DevSession {
  const DevSession(this.accessToken, this.expiresAt);
  final String accessToken;
  final DateTime expiresAt;
}

/// User-scoped backend calls. Each method receives the token explicitly and
/// sends it only on these requests; public parking requests stay anonymous.
abstract interface class UserRepository {
  Future<DevSession> devSession(String subject, {String? displayName});

  Future<void> logout(String token);

  Future<UserProfile> me(String token);

  Future<UserProfile> setVehicle(String token, VehicleType? vehicle);

  Future<List<FavoriteParking>> favorites(String token);

  Future<void> addFavorite(String token, int parkingId);

  Future<void> removeFavorite(String token, int parkingId);

  Future<CommunityReport> createReport(
    String token, {
    required int parkingId,
    required ReportType type,
    int? zoneId,
    String? description,
  });

  Future<void> uploadPhoto(
    String token,
    int reportId,
    Uint8List bytes,
    String filename,
  );

  Future<List<CommunityReport>> myReports(String token);
}

class DioUserRepository implements UserRepository {
  DioUserRepository(this._dio);

  final Dio _dio;

  static Options _auth(String token) =>
      Options(headers: {'Authorization': 'Bearer $token'});

  @override
  Future<DevSession> devSession(String subject, {String? displayName}) => _call(
        () => _dio.post<dynamic>(
          '/auth/dev-session',
          data: {
            'subject': subject,
            if (displayName != null) 'display_name': displayName,
          },
        ),
        (json) => DevSession(
          json['access_token'] as String,
          DateTime.parse(json['expires_at'] as String),
        ),
      );

  @override
  Future<void> logout(String token) => _call(
        () => _dio.post<dynamic>('/auth/logout', options: _auth(token)),
        null,
      );

  @override
  Future<UserProfile> me(String token) => _call(
        () => _dio.get<dynamic>('/me', options: _auth(token)),
        UserProfile.fromJson,
      );

  @override
  Future<UserProfile> setVehicle(String token, VehicleType? vehicle) => _call(
        () => _dio.put<dynamic>(
          '/me/vehicle',
          data: {'vehicle': vehicle?.wireValue},
          options: _auth(token),
        ),
        UserProfile.fromJson,
      );

  @override
  Future<List<FavoriteParking>> favorites(String token) => _call(
        () => _dio.get<dynamic>('/favorites', options: _auth(token)),
        (json) => [
          for (final item in json['items'] as List)
            FavoriteParking.fromJson(Map<String, dynamic>.from(item as Map)),
        ],
      );

  @override
  Future<void> addFavorite(String token, int parkingId) => _call(
        () => _dio.post<dynamic>(
          '/favorites',
          data: {'parking_id': parkingId},
          options: _auth(token),
        ),
        null,
      );

  @override
  Future<void> removeFavorite(String token, int parkingId) => _call(
        () => _dio.delete<dynamic>(
          '/favorites/$parkingId',
          options: _auth(token),
        ),
        null,
      );

  @override
  Future<CommunityReport> createReport(
    String token, {
    required int parkingId,
    required ReportType type,
    int? zoneId,
    String? description,
  }) =>
      _call(
        () => _dio.post<dynamic>(
          '/reports',
          data: {
            'parking_id': parkingId,
            'report_type': type.wireValue,
            if (zoneId != null) 'zone_id': zoneId,
            if (description != null && description.trim().isNotEmpty)
              'description': description.trim(),
          },
          options: _auth(token),
        ),
        CommunityReport.fromJson,
      );

  @override
  Future<void> uploadPhoto(
    String token,
    int reportId,
    Uint8List bytes,
    String filename,
  ) =>
      _call(
        () => _dio.post<dynamic>(
          '/reports/$reportId/photos',
          data: FormData.fromMap({
            'file': MultipartFile.fromBytes(bytes, filename: filename),
          }),
          options: _auth(token),
        ),
        null,
      );

  @override
  Future<List<CommunityReport>> myReports(String token) => _call(
        () => _dio.get<dynamic>('/me/reports', options: _auth(token)),
        (json) => [
          for (final item in json['items'] as List)
            CommunityReport.fromJson(Map<String, dynamic>.from(item as Map)),
        ],
      );

  Future<T> _call<T>(
    Future<Response<dynamic>> Function() request,
    T Function(Map<String, dynamic>)? parse,
  ) async {
    final Response<dynamic> response;
    try {
      response = await request();
    } on DioException catch (e) {
      throw _fromDio(e);
    }
    if (parse == null) return null as T;
    try {
      final data = response.data;
      if (data is! Map) throw const FormatException('Expected a JSON object');
      return parse(Map<String, dynamic>.from(data));
    } on FormatException catch (e) {
      throw UserApiException(
        code: UserApiException.invalidResponse,
        message: e.message,
        statusCode: response.statusCode,
      );
    } on TypeError catch (e) {
      throw UserApiException(
        code: UserApiException.invalidResponse,
        message: e.toString(),
        statusCode: response.statusCode,
      );
    }
  }

  static UserApiException _fromDio(DioException e) {
    final response = e.response;
    final data = response?.data;
    if (data is Map && data['error'] is Map) {
      final error = data['error'] as Map;
      final code = error['code'];
      if (code is String) {
        final message = error['message'];
        return UserApiException(
          code: code,
          message: message is String ? message : code,
          statusCode: response?.statusCode,
        );
      }
    }
    if (response == null) {
      return UserApiException(
        code: UserApiException.network,
        message: e.message ?? 'Network error',
      );
    }
    return UserApiException(
      code: UserApiException.unknown,
      message: e.message ?? 'HTTP ${response.statusCode}',
      statusCode: response.statusCode,
    );
  }
}
