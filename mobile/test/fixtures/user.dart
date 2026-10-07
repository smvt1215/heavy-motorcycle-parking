import 'dart:typed_data';

import 'package:heavy_parking/data/token_store.dart';
import 'package:heavy_parking/data/user_repository.dart';
import 'package:heavy_parking/domain/parking.dart';
import 'package:heavy_parking/domain/user.dart';

const validToken = 'hmp_valid';

class MemoryTokenStore implements TokenStore {
  MemoryTokenStore([this.token]);
  String? token;

  @override
  Future<void> clear() async => token = null;

  @override
  Future<String?> read() async => token;

  @override
  Future<void> write(String token) async => this.token = token;
}

const unauthenticated = UserApiException(
  code: UserApiException.unauthenticated,
  message: 'expired',
  statusCode: 401,
);

/// In-memory backend: one user per valid token; favorites keyed by that user.
class FakeUserRepository implements UserRepository {
  final tokens = <String>{validToken};
  final favoriteIds = <int>[];
  final calls = <String>[];
  final reports = <CommunityReport>[];
  final photos = <(int, String)>[];
  VehicleType? preferred;
  UserApiException? photoError;
  UserApiException? favoriteError;

  void _check(String token) {
    if (!tokens.contains(token)) throw unauthenticated;
  }

  UserProfile _profile() => UserProfile(
        id: 7,
        role: 'USER',
        displayName: 'rider',
        preferredVehicle: preferred,
      );

  @override
  Future<DevSession> devSession(String subject, {String? displayName}) async {
    calls.add('devSession:$subject');
    final token = 'hmp_$subject';
    tokens.add(token);
    return DevSession(token, DateTime.utc(2026, 11, 6));
  }

  @override
  Future<void> logout(String token) async {
    calls.add('logout:$token');
    tokens.remove(token);
  }

  @override
  Future<UserProfile> me(String token) async {
    calls.add('me:$token');
    _check(token);
    return _profile();
  }

  @override
  Future<UserProfile> setVehicle(String token, VehicleType? vehicle) async {
    _check(token);
    preferred = vehicle;
    return _profile();
  }

  @override
  Future<List<FavoriteParking>> favorites(String token) async {
    calls.add('favorites');
    _check(token);
    return [
      for (final id in favoriteIds)
        FavoriteParking(
          parkingId: id,
          name: 'Lot $id',
          location: const GeoPoint(25.05, 121.55),
          createdAt: DateTime.utc(2026, 10, 7),
        ),
    ];
  }

  @override
  Future<void> addFavorite(String token, int parkingId) async {
    calls.add('add:$parkingId');
    _check(token);
    if (favoriteError case final error?) throw error;
    if (!favoriteIds.contains(parkingId)) favoriteIds.add(parkingId);
  }

  @override
  Future<void> removeFavorite(String token, int parkingId) async {
    calls.add('remove:$parkingId');
    _check(token);
    if (!favoriteIds.remove(parkingId)) {
      throw const UserApiException(
        code: 'FAVORITE_NOT_FOUND',
        message: 'missing',
        statusCode: 404,
      );
    }
  }

  @override
  Future<CommunityReport> createReport(
    String token, {
    required int parkingId,
    required ReportType type,
    int? zoneId,
    String? description,
  }) async {
    _check(token);
    final report = CommunityReport(
      id: reports.length + 1,
      parkingId: parkingId,
      zoneId: zoneId,
      type: type,
      status: ReportStatus.pending,
      description: description,
      photoCount: 0,
      createdAt: DateTime.utc(2026, 10, 7),
    );
    reports.add(report);
    return report;
  }

  @override
  Future<void> uploadPhoto(
    String token,
    int reportId,
    Uint8List bytes,
    String filename,
  ) async {
    _check(token);
    if (photoError case final error?) throw error;
    photos.add((reportId, filename));
  }

  @override
  Future<List<CommunityReport>> myReports(String token) async {
    _check(token);
    return reports;
  }
}
