import 'parking.dart';

/// Signed-in rider profile from `GET /me`. Identity always comes from the
/// backend's validated token; the app never chooses a user ID.
class UserProfile {
  const UserProfile({
    required this.id,
    required this.role,
    this.displayName,
    this.email,
    this.preferredVehicle,
  });

  factory UserProfile.fromJson(Map<String, dynamic> json) {
    final id = json['id'];
    final role = json['role'];
    if (id is! int || role is! String) {
      throw const FormatException('Invalid profile');
    }
    final vehicle = json['preferred_vehicle'];
    return UserProfile(
      id: id,
      role: role,
      displayName: json['display_name'] as String?,
      email: json['email'] as String?,
      preferredVehicle:
          vehicle == null ? null : VehicleType.fromWire(vehicle as String),
    );
  }

  final int id;
  final String role;
  final String? displayName;
  final String? email;

  /// Client default only; parking requests still send `vehicle` explicitly.
  final VehicleType? preferredVehicle;

  String get label => displayName ?? '騎士 #$id';
}

class FavoriteParking {
  const FavoriteParking({
    required this.parkingId,
    required this.name,
    required this.location,
    required this.createdAt,
  });

  factory FavoriteParking.fromJson(Map<String, dynamic> json) {
    final id = json['parking_id'];
    final name = json['name'];
    final location = json['location'];
    final created = json['created_at'];
    if (id is! int ||
        name is! String ||
        location is! Map ||
        created is! String) {
      throw const FormatException('Invalid favorite');
    }
    final point = GeoPoint.fromJson(Map<String, dynamic>.from(location));
    if (!point.isValid) {
      throw const FormatException('Invalid favorite location');
    }
    return FavoriteParking(
      parkingId: id,
      name: name,
      location: point,
      createdAt: DateTime.parse(created),
    );
  }

  final int parkingId;
  final String name;
  final GeoPoint location;
  final DateTime createdAt;
}

enum ReportType {
  parkingAllowed('PARKING_ALLOWED', '這裡可以停重機'),
  parkingNotAllowed('PARKING_NOT_ALLOWED', '這裡不能停重機'),
  wrongSpaceType('WRONG_SPACE_TYPE', '車格類型有誤'),
  wrongRate('WRONG_RATE', '費率有誤'),
  wrongAvailability('WRONG_AVAILABILITY', '空位資訊有誤'),
  wrongEntrance('WRONG_ENTRANCE', '入口位置有誤'),
  closed('CLOSED', '停車場已關閉'),
  plateRecognitionFailed('PLATE_RECOGNITION_FAILED', '車牌辨識失敗'),
  gateSensorFailed('GATE_SENSOR_FAILED', '柵欄感應失敗'),
  other('OTHER', '其他');

  const ReportType(this.wireValue, this.label);
  final String wireValue;
  final String label;

  static ReportType fromWire(String value) => values.firstWhere(
        (type) => type.wireValue == value,
        orElse: () => throw FormatException('Unknown report type $value'),
      );
}

enum ReportStatus {
  pending('PENDING', '待審核'),
  verified('VERIFIED', '已確認'),
  rejected('REJECTED', '未採納'),
  superseded('SUPERSEDED', '已被更新資料取代');

  const ReportStatus(this.wireValue, this.label);
  final String wireValue;
  final String label;

  static ReportStatus fromWire(String value) => values.firstWhere(
        (status) => status.wireValue == value,
        orElse: () => throw FormatException('Unknown report status $value'),
      );
}

/// Community evidence. It never changes official parking facts in the app.
class CommunityReport {
  const CommunityReport({
    required this.id,
    required this.parkingId,
    required this.type,
    required this.status,
    required this.photoCount,
    required this.createdAt,
    this.zoneId,
    this.description,
  });

  factory CommunityReport.fromJson(Map<String, dynamic> json) {
    final provenance = json['provenance'];
    if (provenance is! Map || provenance['source_type'] != 'COMMUNITY') {
      throw const FormatException('Report must be community provenance');
    }
    return CommunityReport(
      id: json['id'] as int,
      parkingId: json['parking_id'] as int,
      zoneId: json['zone_id'] as int?,
      type: ReportType.fromWire(json['report_type'] as String),
      status: ReportStatus.fromWire(json['status'] as String),
      description: json['description'] as String?,
      photoCount: json['photo_count'] as int,
      createdAt: DateTime.parse(json['created_at'] as String),
    );
  }

  final int id;
  final int parkingId;
  final int? zoneId;
  final ReportType type;
  final ReportStatus status;
  final String? description;
  final int photoCount;
  final DateTime createdAt;
}
