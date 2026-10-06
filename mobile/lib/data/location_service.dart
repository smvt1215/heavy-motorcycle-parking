import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';

import '../domain/parking.dart';

class LocationFailure implements Exception {
  const LocationFailure(this.message);
  final String message;
}

abstract interface class LocationService {
  Future<GeoPoint> locate();
}

class ForegroundLocationService implements LocationService {
  @override
  Future<GeoPoint> locate() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      throw const LocationFailure('請開啟裝置定位，或移動地圖搜尋附近停車。');
    }
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.deniedForever) {
      throw const LocationFailure('定位權限已關閉，請至系統設定開啟使用期間定位。');
    }
    if (permission != LocationPermission.whileInUse &&
        permission != LocationPermission.always) {
      throw const LocationFailure('未取得定位權限，仍可移動地圖搜尋停車。');
    }
    final position = await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.high,
        timeLimit: Duration(seconds: 15),
      ),
    );
    return GeoPoint(position.latitude, position.longitude);
  }
}

final locationServiceProvider = Provider<LocationService>(
  (ref) => ForegroundLocationService(),
);
