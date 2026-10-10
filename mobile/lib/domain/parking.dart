/// Immutable parking-domain DTOs mirroring the backend v1 wire contract
/// (`backend/app/schemas/parking.py`).
///
/// Server-derived facts (lot compatibility rollup, availability summary,
/// ranking) are preserved as received and never re-derived on the client.
library;

// ---------------------------------------------------------------------------
// JSON helpers
// ---------------------------------------------------------------------------

typedef Json = Map<String, dynamic>;

/// Required key; the value may still be null when [T] is nullable.
T _req<T>(Json json, String key) {
  if (!json.containsKey(key)) {
    throw FormatException('Missing required field "$key"');
  }
  final value = json[key];
  if (value is! T) {
    throw FormatException(
      'Field "$key" has unexpected type ${value.runtimeType}',
    );
  }
  return value;
}

/// Optional key; absent and null are both treated as null.
T? _opt<T extends Object>(Json json, String key) {
  final value = json[key];
  if (value == null) return null;
  if (value is! T) {
    throw FormatException(
      'Field "$key" has unexpected type ${value.runtimeType}',
    );
  }
  return value;
}

Json _asMap(Object? value, String context) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) return Map<String, dynamic>.from(value);
  throw FormatException('Expected object for $context');
}

DateTime? _date(Object? value, String key) {
  if (value == null) return null;
  if (value is! String) {
    throw FormatException('Field "$key" must be an RFC3339 string');
  }
  return DateTime.parse(value);
}

DateTime _reqDate(Json json, String key) {
  final value = _date(_req<Object?>(json, key), key);
  if (value == null) throw FormatException('Field "$key" must not be null');
  return value;
}

List<T> _list<T>(Json json, String key, T Function(Json) parse) =>
    List<T>.unmodifiable(
      _req<List<dynamic>>(json, key).map((e) => parse(_asMap(e, key))),
    );

List<T> _optList<T>(Json json, String key, T Function(Json) parse) {
  final raw = _opt<List<dynamic>>(json, key);
  if (raw == null) return List<T>.unmodifiable(const []);
  return List<T>.unmodifiable(raw.map((e) => parse(_asMap(e, key))));
}

/// A nonnegative integer count, or null when the value is not one.
bool _isCount(num? value) => value is int && value >= 0;

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

E _fromWire<E extends Enum>(
  List<E> values,
  String Function(E) wire,
  Object? raw,
  String field,
) {
  for (final value in values) {
    if (wire(value) == raw) return value;
  }
  throw FormatException('Unsupported $field value "$raw"');
}

enum VehicleType {
  normalHeavy('NORMAL_HEAVY', '普重', '普通重型機車'),
  largeHeavy('LARGE_HEAVY', '大重', '大型重型機車（黃牌／紅牌）');

  const VehicleType(this.wireValue, this.label, this.formalName);
  final String formalName;
  final String wireValue;
  final String label;

  static VehicleType fromWire(Object? raw) =>
      _fromWire(values, (v) => v.wireValue, raw, 'vehicle');
}

enum SpaceType {
  heavyOnly('HEAVY_ONLY', '重機專用'),
  motoShared('MOTO_SHARED', '機車共用'),
  carShared('CAR_SHARED', '汽車格共用'),
  lightMotoOnly('LIGHT_MOTO_ONLY', '輕型機車專用');

  const SpaceType(this.wireValue, this.label);
  final String wireValue;
  final String label;

  /// Space types offered as LARGE_HEAVY search filters. LIGHT_MOTO_ONLY is
  /// known NOT_ALLOWED for heavy motorcycles and is never a search mode.
  static const List<SpaceType> searchable = [heavyOnly, motoShared, carShared];

  static List<SpaceType> forVehicle(VehicleType vehicle) =>
      vehicle == VehicleType.normalHeavy ? values : searchable;

  bool get isSearchable => this != lightMotoOnly;

  static SpaceType fromWire(Object? raw) =>
      _fromWire(values, (v) => v.wireValue, raw, 'space_type');
}

/// Tri-state compatibility; also used for entrance `heavy_motorcycle_access`.
enum CompatibilityStatus {
  allowed('ALLOWED', '可停放'),
  notAllowed('NOT_ALLOWED', '不可停放'),
  unknown('UNKNOWN', '未確認');

  const CompatibilityStatus(this.wireValue, this.label);
  final String wireValue;
  final String label;

  static CompatibilityStatus fromWire(Object? raw) =>
      _fromWire(values, (v) => v.wireValue, raw, 'compatibility status');
}

/// Observed availability. STALE is never an availability status.
enum AvailabilityStatus {
  available('AVAILABLE', '有空位'),
  full('FULL', '已滿'),
  unknown('UNKNOWN', '空位不明'),
  closed('CLOSED', '未開放');

  const AvailabilityStatus(this.wireValue, this.label);
  final String wireValue;
  final String label;

  static AvailabilityStatus fromWire(Object? raw) =>
      _fromWire(values, (v) => v.wireValue, raw, 'availability status');
}

enum FreshnessStatus {
  fresh('FRESH', '即時'),
  stale('STALE', '可能已過時'),
  unknown('UNKNOWN', '更新時間不明');

  const FreshnessStatus(this.wireValue, this.label);
  final String wireValue;
  final String label;

  static FreshnessStatus fromWire(Object? raw) =>
      _fromWire(values, (v) => v.wireValue, raw, 'freshness status');

  /// Parses the wire shape `{"status": "FRESH"}`.
  static FreshnessStatus fromJson(Json json) =>
      fromWire(_req<String>(json, 'status'));
}

enum Coverage {
  complete('COMPLETE', '完整'),
  partial('PARTIAL', '部分'),
  none('NONE', '無即時資料');

  const Coverage(this.wireValue, this.label);
  final String wireValue;
  final String label;

  static Coverage fromWire(Object? raw) =>
      _fromWire(values, (v) => v.wireValue, raw, 'coverage');
}

// ---------------------------------------------------------------------------
// Value objects
// ---------------------------------------------------------------------------

class GeoPoint {
  const GeoPoint(this.lat, this.lng);

  factory GeoPoint.fromJson(Json json) => GeoPoint(
        _req<num>(json, 'lat').toDouble(),
        _req<num>(json, 'lng').toDouble(),
      );

  final double lat;
  final double lng;

  /// Finite WGS84 coordinate within lat -90..90 and lng -180..180.
  bool get isValid =>
      lat.isFinite &&
      lng.isFinite &&
      lat >= -90 &&
      lat <= 90 &&
      lng >= -180 &&
      lng <= 180;

  @override
  bool operator ==(Object other) =>
      other is GeoPoint && other.lat == lat && other.lng == lng;

  @override
  int get hashCode => Object.hash(lat, lng);

  @override
  String toString() => 'GeoPoint($lat, $lng)';
}

/// Provenance of a single fact. Rule, rate, realtime and entrance facts each
/// carry their own [ParkingSource]; never substitute one for another.
class ParkingSource {
  const ParkingSource({
    required this.sourceId,
    required this.sourceType,
    this.sourceName,
    this.sourceUrl,
    this.sourceRecordId,
    required this.sourceUpdatedAt,
    required this.fetchedAt,
    required this.verifiedAt,
  });

  factory ParkingSource.fromJson(Json json) => ParkingSource(
        sourceId: _req<int>(json, 'source_id'),
        sourceType: _req<String?>(json, 'source_type'),
        sourceName: _opt<String>(json, 'source_name'),
        sourceUrl: _opt<String>(json, 'source_url'),
        sourceRecordId: _opt<String>(json, 'source_record_id'),
        sourceUpdatedAt: _date(
          _req<Object?>(json, 'source_updated_at'),
          'source_updated_at',
        ),
        fetchedAt: _date(_req<Object?>(json, 'fetched_at'), 'fetched_at'),
        verifiedAt: _date(_req<Object?>(json, 'verified_at'), 'verified_at'),
      );

  final int sourceId;
  final String? sourceType;
  final String? sourceName;
  final String? sourceUrl;
  final String? sourceRecordId;
  final DateTime? sourceUpdatedAt;
  final DateTime? fetchedAt;
  final DateTime? verifiedAt;
}

class RuleEvidence {
  const RuleEvidence({required this.ruleId, required this.provenance});

  factory RuleEvidence.fromJson(Json json) => RuleEvidence(
        ruleId: _req<int>(json, 'rule_id'),
        provenance: ParkingSource.fromJson(
          _asMap(_req<Object>(json, 'provenance'), 'provenance'),
        ),
      );

  final int ruleId;
  final ParkingSource provenance;
}

class ZoneCompatibility {
  const ZoneCompatibility({
    required this.status,
    required this.vehicle,
    this.reason,
    this.confidence,
    this.provenance,
    this.ruleEvidence = const [],
  });

  factory ZoneCompatibility.fromJson(Json json) {
    final provenance = _opt<Object>(json, 'provenance');
    return ZoneCompatibility(
      status: CompatibilityStatus.fromWire(_req<String>(json, 'status')),
      vehicle: VehicleType.fromWire(_req<String>(json, 'vehicle')),
      reason: _opt<String>(json, 'reason'),
      confidence: _opt<num>(json, 'confidence')?.toDouble(),
      provenance: provenance == null
          ? null
          : ParkingSource.fromJson(_asMap(provenance, 'provenance')),
      ruleEvidence: _optList(json, 'rule_evidence', RuleEvidence.fromJson),
    );
  }

  final CompatibilityStatus status;
  final VehicleType vehicle;
  final String? reason;

  /// Descriptive only; never overrides [status].
  final double? confidence;

  /// Null when no single winning rule exists; see [ruleEvidence].
  final ParkingSource? provenance;
  final List<RuleEvidence> ruleEvidence;
}

class RateSummary {
  const RateSummary({
    required this.displayText,
    required this.comparisonEligible,
    required this.comparisonHourlyRateTwd,
    required this.dailyMaxTwd,
    required this.parseStatus,
    required this.provenance,
    this.supportingSources = const [],
  });

  factory RateSummary.fromJson(Json json) {
    final provenance = _req<Object?>(json, 'provenance');
    return RateSummary(
      displayText: _req<String?>(json, 'display_text'),
      comparisonEligible: _req<bool>(json, 'comparison_eligible'),
      comparisonHourlyRateTwd: _req<num?>(json, 'comparison_hourly_rate_twd'),
      dailyMaxTwd: _req<num?>(json, 'daily_max_twd'),
      parseStatus: _req<String>(json, 'parse_status'),
      provenance: provenance == null
          ? null
          : ParkingSource.fromJson(_asMap(provenance, 'provenance')),
      supportingSources:
          _optList(json, 'supporting_sources', ParkingSource.fromJson),
    );
  }

  final String? displayText;
  final bool comparisonEligible;
  final num? comparisonHourlyRateTwd;
  final num? dailyMaxTwd;
  final String parseStatus;
  final ParkingSource? provenance;
  final List<ParkingSource> supportingSources;

  /// Server-confirmed hourly-equivalent rate; null when not comparable.
  /// Never compute an hourly rate client-side from raw/progressive rates.
  num? get confirmedHourlyRateTwd =>
      comparisonEligible ? comparisonHourlyRateTwd : null;
}

/// One zone-scoped realtime observation. [status] is the last observed
/// status and is independent of [freshness]; aging never rewrites it.
class ParkingAvailability {
  const ParkingAvailability({
    required this.status,
    required this.available,
    required this.total,
    required this.freshness,
    required this.provenance,
  });

  factory ParkingAvailability.fromJson(Json json) => ParkingAvailability(
        status: AvailabilityStatus.fromWire(_req<String>(json, 'status')),
        available: _req<num?>(json, 'available'),
        total: _req<num?>(json, 'total'),
        freshness: FreshnessStatus.fromJson(
          _asMap(_req<Object>(json, 'freshness'), 'freshness'),
        ),
        provenance: ParkingSource.fromJson(
          _asMap(_req<Object>(json, 'provenance'), 'provenance'),
        ),
      );

  final AvailabilityStatus status;

  /// Raw counts as received; use [hasValidCounts] before trusting them.
  final num? available;
  final num? total;
  final FreshnessStatus freshness;
  final ParkingSource provenance;

  /// Nonnegative integer counts, and available <= total when both known.
  bool get hasValidCounts {
    final a = available;
    final t = total;
    if (a != null && !_isCount(a)) return false;
    if (t != null && !_isCount(t)) return false;
    if (a != null && t != null && a > t) return false;
    return true;
  }

  bool get isStale => freshness == FreshnessStatus.stale;

  /// AVAILABLE + integer available > 0 + valid total + FRESH + fetched_at.
  bool get isConfirmedAvailable {
    final a = available;
    return status == AvailabilityStatus.available &&
        freshness == FreshnessStatus.fresh &&
        provenance.fetchedAt != null &&
        a is int &&
        a > 0 &&
        hasValidCounts;
  }
}

class AvailabilitySummary {
  const AvailabilitySummary({
    required this.status,
    required this.available,
    required this.total,
    required this.coverage,
    required this.eligibleZoneCount,
    required this.freshRealtimeZoneCount,
    required this.freshness,
    required this.oldestSourceUpdatedAt,
    required this.oldestFetchedAt,
    required this.contributingSources,
  });

  factory AvailabilitySummary.fromJson(Json json) => AvailabilitySummary(
        status: AvailabilityStatus.fromWire(_req<String>(json, 'status')),
        available: _req<num?>(json, 'available'),
        total: _req<num?>(json, 'total'),
        coverage: Coverage.fromWire(_req<String>(json, 'coverage')),
        eligibleZoneCount: _req<int>(json, 'eligible_zone_count'),
        freshRealtimeZoneCount: _req<int>(json, 'fresh_realtime_zone_count'),
        freshness: FreshnessStatus.fromJson(
          _asMap(_req<Object>(json, 'freshness'), 'freshness'),
        ),
        oldestSourceUpdatedAt: _date(
          _req<Object?>(json, 'oldest_source_updated_at'),
          'oldest_source_updated_at',
        ),
        oldestFetchedAt: _date(
          _req<Object?>(json, 'oldest_fetched_at'),
          'oldest_fetched_at',
        ),
        contributingSources:
            _list(json, 'contributing_sources', ParkingSource.fromJson),
      );

  /// Server-derived aggregate status; never recomputed from zone rows.
  final AvailabilityStatus status;
  final num? available;
  final num? total;
  final Coverage coverage;
  final int eligibleZoneCount;
  final int freshRealtimeZoneCount;
  final FreshnessStatus freshness;
  final DateTime? oldestSourceUpdatedAt;
  final DateTime? oldestFetchedAt;
  final List<ParkingSource> contributingSources;

  /// Lot numeric totals may be shown only for a COMPLETE, FRESH aggregate
  /// covering every eligible zone, whose every contributing source has a
  /// fetch time, whose counts are valid nonnegative integers, and whose
  /// server status is consistent with those counts. Any contradiction means
  /// no confirmed numeric aggregate is rendered.
  bool get canShowTotals {
    final a = available;
    final t = total;
    if (coverage != Coverage.complete ||
        freshness != FreshnessStatus.fresh ||
        eligibleZoneCount <= 0 ||
        freshRealtimeZoneCount != eligibleZoneCount ||
        oldestFetchedAt == null ||
        contributingSources.isEmpty ||
        contributingSources.any((s) => s.fetchedAt == null)) {
      return false;
    }
    if (a is! int || t is! int || a < 0 || t < 0 || a > t) return false;
    return switch (status) {
      AvailabilityStatus.available => a > 0,
      AvailabilityStatus.full || AvailabilityStatus.closed => a == 0,
      AvailabilityStatus.unknown => false,
    };
  }
}

// ---------------------------------------------------------------------------
// Zones, entrances, lots
// ---------------------------------------------------------------------------

/// Common zone base shared by every selected-vehicle parking endpoint.
class ParkingZone {
  const ParkingZone({
    required this.zoneId,
    required this.name,
    required this.spaceType,
    required this.capacity,
    required this.compatibility,
    required this.rateSummary,
    required this.availability,
  });

  factory ParkingZone.fromJson(Json json) {
    final rate = _req<Object?>(json, 'rate_summary');
    final availability = _req<Object?>(json, 'availability');
    return ParkingZone(
      zoneId: _req<int>(json, 'zone_id'),
      name: _req<String?>(json, 'name'),
      spaceType: SpaceType.fromWire(_req<String>(json, 'space_type')),
      capacity: _req<int?>(json, 'capacity'),
      compatibility: ZoneCompatibility.fromJson(
        _asMap(_req<Object>(json, 'compatibility'), 'compatibility'),
      ),
      rateSummary:
          rate == null ? null : RateSummary.fromJson(_asMap(rate, 'rate')),
      availability: availability == null
          ? null
          : ParkingAvailability.fromJson(
              _asMap(availability, 'availability'),
            ),
    );
  }

  final int zoneId;
  final String? name;
  final SpaceType spaceType;
  final int? capacity;
  final ZoneCompatibility compatibility;
  final RateSummary? rateSummary;
  final ParkingAvailability? availability;

  /// Matches backend `available_only` semantics for this zone.
  bool get hasConfirmedAvailability =>
      compatibility.status == CompatibilityStatus.allowed &&
      (availability?.isConfirmedAvailable ?? false);
}

class ParkingEntrance {
  const ParkingEntrance({
    required this.id,
    required this.name,
    required this.location,
    required this.entranceType,
    required this.heavyMotorcycleAccess,
    required this.notes,
    required this.provenance,
  });

  factory ParkingEntrance.fromJson(Json json) {
    final location = _req<Object?>(json, 'location');
    return ParkingEntrance(
      id: _req<int>(json, 'id'),
      name: _req<String?>(json, 'name'),
      location: location == null
          ? null
          : GeoPoint.fromJson(_asMap(location, 'location')),
      entranceType: _req<String?>(json, 'entrance_type'),
      heavyMotorcycleAccess: CompatibilityStatus.fromWire(
        _req<String>(json, 'heavy_motorcycle_access'),
      ),
      notes: _req<String?>(json, 'notes'),
      provenance: ParkingSource.fromJson(
        _asMap(_req<Object>(json, 'provenance'), 'provenance'),
      ),
    );
  }

  final int id;
  final String? name;

  /// Entrance coordinate; distinct from the lot center.
  final GeoPoint? location;
  final String? entranceType;
  final CompatibilityStatus heavyMotorcycleAccess;
  final String? notes;
  final ParkingSource provenance;
}

/// A nearby search result item.
class ParkingLot {
  const ParkingLot({
    required this.id,
    required this.name,
    required this.distanceM,
    required this.location,
    required this.compatibility,
    required this.compatibilityVehicle,
    required this.zones,
    required this.availabilitySummary,
    this.rankingGroup,
    this.rankingScoreBp,
  });

  factory ParkingLot.fromJson(Json json) {
    final compatibility =
        _asMap(_req<Object>(json, 'compatibility'), 'compatibility');
    final summary = _opt<Object>(json, 'availability_summary');
    return ParkingLot(
      id: _req<int>(json, 'id'),
      name: _req<String>(json, 'name'),
      distanceM: _req<int>(json, 'distance_m'),
      location: GeoPoint.fromJson(
        _asMap(_req<Object>(json, 'location'), 'location'),
      ),
      compatibility:
          CompatibilityStatus.fromWire(_req<String>(compatibility, 'status')),
      compatibilityVehicle:
          VehicleType.fromWire(_req<String>(compatibility, 'vehicle')),
      zones: _list(json, 'zones', ParkingZone.fromJson),
      availabilitySummary: summary == null
          ? null
          : AvailabilitySummary.fromJson(
              _asMap(summary, 'availability_summary'),
            ),
      rankingGroup: _opt<int>(json, 'ranking_group'),
      rankingScoreBp: _opt<int>(json, 'ranking_score_bp'),
    );
  }

  final int id;
  final String name;
  final int distanceM;

  /// Lot center coordinate; not an entrance.
  final GeoPoint location;

  /// Server-derived lot rollup (ALLOWED or UNKNOWN in v1).
  final CompatibilityStatus compatibility;
  final VehicleType compatibilityVehicle;
  final List<ParkingZone> zones;
  final AvailabilitySummary? availabilitySummary;
  final int? rankingGroup;
  final int? rankingScoreBp;

  bool get isUnverified => compatibility == CompatibilityStatus.unknown;

  ParkingLot _withZones(List<ParkingZone> zones) => ParkingLot(
        id: id,
        name: name,
        distanceM: distanceM,
        location: location,
        compatibility: compatibility,
        compatibilityVehicle: compatibilityVehicle,
        zones: List.unmodifiable(zones),
        availabilitySummary: availabilitySummary,
        rankingGroup: rankingGroup,
        rankingScoreBp: rankingScoreBp,
      );

  /// Defensive presentation filter for selected-vehicle search results.
  ///
  /// Drops zones that are known NOT_ALLOWED, LIGHT_MOTO_ONLY, evaluated for a
  /// different vehicle, or UNKNOWN when unknown results were not requested.
  /// Returns null when the lot has nothing displayable or its server rollup is
  /// inconsistent with the remaining zones. Server aggregate and ranking
  /// fields are preserved unchanged.
  ParkingLot? visibleFor(ParkingQuery query) {
    if (compatibilityVehicle != query.vehicle) return null;
    if (compatibility == CompatibilityStatus.notAllowed) return null;
    if (compatibility == CompatibilityStatus.unknown && !query.includeUnknown) {
      return null;
    }
    final kept = zones.where((zone) {
      final c = zone.compatibility;
      if (c.vehicle != query.vehicle) return false;
      if (!SpaceType.forVehicle(query.vehicle).contains(zone.spaceType)) {
        return false;
      }
      return switch (c.status) {
        CompatibilityStatus.allowed => true,
        CompatibilityStatus.notAllowed => false,
        CompatibilityStatus.unknown => query.includeUnknown,
      };
    }).toList();
    if (kept.isEmpty) return null;
    final hasAllowed =
        kept.any((z) => z.compatibility.status == CompatibilityStatus.allowed);
    if (compatibility == CompatibilityStatus.allowed && !hasAllowed) {
      return null;
    }
    return kept.length == zones.length ? this : _withZones(kept);
  }
}

class NavigationTarget {
  const NavigationTarget({
    required this.location,
    required this.label,
    required this.isConfirmedEntrance,
    this.entrance,
  });

  final GeoPoint location;
  final String label;

  /// True only for an entrance with heavy_motorcycle_access=ALLOWED.
  /// False means an explicitly labeled lot-center fallback.
  final bool isConfirmedEntrance;
  final ParkingEntrance? entrance;
}

class ParkingDetail {
  const ParkingDetail({
    required this.id,
    required this.name,
    required this.vehicle,
    required this.evaluationAt,
    required this.location,
    required this.zones,
    required this.entrances,
  });

  factory ParkingDetail.fromJson(Json json) => ParkingDetail(
        id: _req<int>(json, 'id'),
        name: _req<String>(json, 'name'),
        vehicle: VehicleType.fromWire(_req<String>(json, 'vehicle')),
        evaluationAt: _reqDate(json, 'evaluation_at'),
        location: GeoPoint.fromJson(
          _asMap(_req<Object>(json, 'location'), 'location'),
        ),
        zones: _list(json, 'zones', ParkingZone.fromJson),
        entrances: _list(json, 'entrances', ParkingEntrance.fromJson),
      );

  static const String confirmedEntranceFallbackLabel = '已確認重機入口';
  static const String lotCenterFallbackLabel = '停車場中心位置（無已確認重機入口）';

  final int id;
  final String name;
  final VehicleType vehicle;
  final DateTime evaluationAt;

  /// Lot center coordinate; not an entrance.
  final GeoPoint location;
  final List<ParkingZone> zones;
  final List<ParkingEntrance> entrances;

  /// Entrances with UNKNOWN heavy-motorcycle access; show as unverified only.
  List<ParkingEntrance> get unverifiedEntrances => entrances
      .where((e) => e.heavyMotorcycleAccess == CompatibilityStatus.unknown)
      .toList(growable: false);

  /// First ALLOWED entrance with coordinates, else an explicitly labeled lot
  /// center. UNKNOWN and NOT_ALLOWED entrances are never selected.
  NavigationTarget get navigationTarget {
    for (final entrance in entrances) {
      final point = entrance.location;
      if (entrance.heavyMotorcycleAccess == CompatibilityStatus.allowed &&
          point != null) {
        return NavigationTarget(
          location: point,
          label: entrance.name ?? confirmedEntranceFallbackLabel,
          isConfirmedEntrance: true,
          entrance: entrance,
        );
      }
    }
    return NavigationTarget(
      location: location,
      label: lotCenterFallbackLabel,
      isConfirmedEntrance: false,
    );
  }
}

// ---------------------------------------------------------------------------
// Rates and realtime endpoint responses
// ---------------------------------------------------------------------------

class ParkingRate {
  const ParkingRate({
    required this.rateId,
    required this.rateType,
    required this.currency,
    required this.baseAmount,
    required this.unitMinutes,
    required this.freeMinutes,
    required this.dailyMaxTwd,
    required this.description,
    required this.rawText,
    required this.parseStatus,
    required this.applicability,
    required this.provenance,
    this.supportingSources = const [],
  });

  factory ParkingRate.fromJson(Json json) => ParkingRate(
        rateId: _req<int>(json, 'rate_id'),
        rateType: _req<String?>(json, 'rate_type'),
        currency: _req<String>(json, 'currency'),
        baseAmount: _req<num?>(json, 'base_amount'),
        unitMinutes: _req<int?>(json, 'unit_minutes'),
        freeMinutes: _req<int?>(json, 'free_minutes'),
        dailyMaxTwd: _req<num?>(json, 'daily_max_twd'),
        description: _req<String?>(json, 'description'),
        rawText: _opt<String>(json, 'raw_text'),
        parseStatus: _req<String>(json, 'parse_status'),
        applicability: _opt<String>(json, 'applicability') ?? 'UNKNOWN',
        provenance: ParkingSource.fromJson(
          _asMap(_req<Object>(json, 'provenance'), 'provenance'),
        ),
        supportingSources:
            _optList(json, 'supporting_sources', ParkingSource.fromJson),
      );

  final int rateId;
  final String? rateType;
  final String currency;
  final num? baseAmount;
  final int? unitMinutes;
  final int? freeMinutes;
  final num? dailyMaxTwd;
  final String? description;
  final String? rawText;
  final String parseStatus;

  /// `MATCH` or `UNKNOWN`.
  final String applicability;
  final ParkingSource provenance;
  final List<ParkingSource> supportingSources;
}

class ParkingRatesZone {
  const ParkingRatesZone({required this.zone, required this.rates});

  factory ParkingRatesZone.fromJson(Json json) => ParkingRatesZone(
        zone: ParkingZone.fromJson(json),
        rates: _list(json, 'rates', ParkingRate.fromJson),
      );

  final ParkingZone zone;
  final List<ParkingRate> rates;
}

class ParkingRates {
  const ParkingRates({
    required this.parkingId,
    required this.vehicle,
    required this.evaluationAt,
    required this.zones,
  });

  factory ParkingRates.fromJson(Json json) => ParkingRates(
        parkingId: _req<int>(json, 'parking_id'),
        vehicle: VehicleType.fromWire(_req<String>(json, 'vehicle')),
        evaluationAt: _reqDate(json, 'evaluation_at'),
        zones: _list(json, 'zones', ParkingRatesZone.fromJson),
      );

  final int parkingId;
  final VehicleType vehicle;
  final DateTime evaluationAt;
  final List<ParkingRatesZone> zones;
}

class ParkingRealtime {
  const ParkingRealtime({
    required this.parkingId,
    required this.vehicle,
    required this.evaluationAt,
    required this.zones,
    required this.availabilitySummary,
  });

  factory ParkingRealtime.fromJson(Json json) {
    final summary = _opt<Object>(json, 'availability_summary');
    return ParkingRealtime(
      parkingId: _req<int>(json, 'parking_id'),
      vehicle: VehicleType.fromWire(_req<String>(json, 'vehicle')),
      evaluationAt: _reqDate(json, 'evaluation_at'),
      zones: _list(json, 'zones', ParkingZone.fromJson),
      availabilitySummary: summary == null
          ? null
          : AvailabilitySummary.fromJson(
              _asMap(summary, 'availability_summary'),
            ),
    );
  }

  final int parkingId;
  final VehicleType vehicle;
  final DateTime evaluationAt;
  final List<ParkingZone> zones;
  final AvailabilitySummary? availabilitySummary;
}

// ---------------------------------------------------------------------------
// Nearby query and page
// ---------------------------------------------------------------------------

const Object _unset = Object();

class ParkingQuery {
  const ParkingQuery({
    required this.center,
    this.vehicle = VehicleType.largeHeavy,
    this.radius = 1000,
    this.spaceType,
    this.availableOnly = false,
    this.hourlyRateMaxTwd,
    this.dailyMaxRequired = false,
    this.includeUnknown = false,
    this.at,
    this.limit,
  });

  static const int maxRadius = 5000;

  final GeoPoint center;
  final VehicleType vehicle;
  final int radius;

  /// [SpaceType.lightMotoOnly] is available only for NORMAL_HEAVY.
  final SpaceType? spaceType;
  final bool availableOnly;
  final num? hourlyRateMaxTwd;
  final bool dailyMaxRequired;
  final bool includeUnknown;

  /// Explicit evaluation instant. Null lets the server pin request time.
  final DateTime? at;
  final int? limit;

  /// Pass `null` explicitly to clear [spaceType], [hourlyRateMaxTwd], [at]
  /// or [limit]; omit an argument to keep its current value.
  ParkingQuery copyWith({
    GeoPoint? center,
    VehicleType? vehicle,
    int? radius,
    Object? spaceType = _unset,
    bool? availableOnly,
    Object? hourlyRateMaxTwd = _unset,
    bool? dailyMaxRequired,
    bool? includeUnknown,
    Object? at = _unset,
    Object? limit = _unset,
  }) =>
      ParkingQuery(
        center: center ?? this.center,
        vehicle: vehicle ?? this.vehicle,
        radius: radius ?? this.radius,
        spaceType: identical(spaceType, _unset)
            ? this.spaceType
            : spaceType as SpaceType?,
        availableOnly: availableOnly ?? this.availableOnly,
        hourlyRateMaxTwd: identical(hourlyRateMaxTwd, _unset)
            ? this.hourlyRateMaxTwd
            : hourlyRateMaxTwd as num?,
        dailyMaxRequired: dailyMaxRequired ?? this.dailyMaxRequired,
        includeUnknown: includeUnknown ?? this.includeUnknown,
        at: identical(at, _unset) ? this.at : at as DateTime?,
        limit: identical(limit, _unset) ? this.limit : limit as int?,
      );

  /// Throws [ArgumentError] for values the v1 search contract forbids.
  void validate() {
    if (!center.isValid) {
      throw ArgumentError.value(center, 'center', 'invalid coordinate');
    }
    if (vehicle == VehicleType.largeHeavy &&
        spaceType == SpaceType.lightMotoOnly) {
      throw ArgumentError.value(
        spaceType,
        'spaceType',
        'LIGHT_MOTO_ONLY is not a LARGE_HEAVY search filter',
      );
    }
    if (radius <= 0 || radius > maxRadius) {
      throw ArgumentError.value(radius, 'radius', 'must be in 1..$maxRadius');
    }
    final rate = hourlyRateMaxTwd;
    if (rate != null && (rate < 0 || !rate.isFinite)) {
      throw ArgumentError.value(rate, 'hourlyRateMaxTwd', 'must be >= 0');
    }
    final l = limit;
    if (l != null && (l < 1 || l > 100)) {
      throw ArgumentError.value(l, 'limit', 'must be in 1..100');
    }
  }

  /// Wire parameters for `GET /parking/nearby`. Absent/default-false filters
  /// are omitted; `vehicle` is always explicit.
  Map<String, dynamic> toQueryParameters() {
    validate();
    final rate = hourlyRateMaxTwd;
    return {
      'lat': center.lat,
      'lng': center.lng,
      'vehicle': vehicle.wireValue,
      'radius': radius,
      if (spaceType != null) 'space_type': spaceType!.wireValue,
      if (availableOnly) 'available_only': true,
      if (rate != null) 'hourly_rate_max_twd': rate,
      if (dailyMaxRequired) 'daily_max_required': true,
      if (includeUnknown) 'include_unknown': true,
      if (at != null) 'at': at!.toUtc().toIso8601String(),
      if (limit != null) 'limit': limit,
    };
  }

  @override
  bool operator ==(Object other) =>
      other is ParkingQuery &&
      other.center == center &&
      other.vehicle == vehicle &&
      other.radius == radius &&
      other.spaceType == spaceType &&
      other.availableOnly == availableOnly &&
      other.hourlyRateMaxTwd == hourlyRateMaxTwd &&
      other.dailyMaxRequired == dailyMaxRequired &&
      other.includeUnknown == includeUnknown &&
      other.at == at &&
      other.limit == limit;

  @override
  int get hashCode => Object.hash(
        center,
        vehicle,
        radius,
        spaceType,
        availableOnly,
        hourlyRateMaxTwd,
        dailyMaxRequired,
        includeUnknown,
        at,
        limit,
      );
}

class NearbyParkingPage {
  const NearbyParkingPage({
    required this.items,
    required this.evaluationAt,
    required this.nextCursor,
    required this.hasMore,
    required this.sortVersion,
  });

  factory NearbyParkingPage.fromJson(Json json) {
    final page = _asMap(_req<Object>(json, 'page'), 'page');
    return NearbyParkingPage(
      items: _list(json, 'items', ParkingLot.fromJson),
      evaluationAt: _reqDate(json, 'evaluation_at'),
      nextCursor: _req<String?>(page, 'next_cursor'),
      hasMore: _req<bool>(page, 'has_more'),
      sortVersion: _opt<int>(json, 'sort_version'),
    );
  }

  final List<ParkingLot> items;
  final DateTime evaluationAt;
  final String? nextCursor;
  final bool hasMore;
  final int? sortVersion;
}
