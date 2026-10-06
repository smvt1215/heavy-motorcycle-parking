/// Backend-shaped JSON fixtures matching `backend/app/schemas/parking.py`
/// (`extra="forbid"` wire models). Shared by domain, data, controller and
/// widget tests.
library;

const String fixtureEvaluationAt = '2026-10-02T09:30:00Z';

Map<String, dynamic> sourceJson({
  int sourceId = 4,
  String? sourceType = 'GOVERNMENT',
  String? sourceUpdatedAt = '2026-10-02T01:50:00Z',
  String? fetchedAt = '2026-10-02T01:51:00Z',
  String? verifiedAt,
}) =>
    {
      'source_id': sourceId,
      'source_type': sourceType,
      'source_updated_at': sourceUpdatedAt,
      'fetched_at': fetchedAt,
      'verified_at': verifiedAt,
    };

Map<String, dynamic> compatibilityJson({
  String status = 'ALLOWED',
  String vehicle = 'RED',
  String reason = 'explicit_vehicle_permission',
  double? confidence = 1.0,
  bool withProvenance = true,
}) =>
    {
      'status': status,
      'vehicle': vehicle,
      'reason': reason,
      'confidence': confidence,
      'provenance': withProvenance ? sourceJson() : null,
      'rule_evidence': [
        if (withProvenance) {'rule_id': 501, 'provenance': sourceJson()},
      ],
    };

Map<String, dynamic> rateSummaryJson({
  bool comparisonEligible = true,
  num? hourly = 20,
  num? dailyMax = 100,
  String parseStatus = 'PARSED',
}) =>
    {
      'display_text': '20元/小時・最高100元/日',
      'comparison_eligible': comparisonEligible,
      'comparison_hourly_rate_twd': hourly,
      'daily_max_twd': dailyMax,
      'parse_status': parseStatus,
      'provenance': sourceJson(
        sourceId: 7,
        sourceUpdatedAt: '2026-10-01T00:00:00Z',
        fetchedAt: '2026-10-02T01:00:00Z',
      ),
      'supporting_sources': <Map<String, dynamic>>[],
    };

Map<String, dynamic> availabilityJson({
  String status = 'AVAILABLE',
  Object? available = 8,
  Object? total = 20,
  String freshness = 'FRESH',
  String? fetchedAt = '2026-10-02T02:01:00Z',
  int sourceId = 9,
}) =>
    {
      'status': status,
      'available': available,
      'total': total,
      'freshness': {'status': freshness},
      'provenance': sourceJson(
        sourceId: sourceId,
        sourceType: 'OPERATOR',
        sourceUpdatedAt: '2026-10-02T02:00:00Z',
        fetchedAt: fetchedAt,
      ),
    };

Map<String, dynamic> zoneJson({
  int zoneId = 20,
  String? name = 'B2 大重機區',
  String spaceType = 'HEAVY_ONLY',
  int? capacity = 20,
  String status = 'ALLOWED',
  String vehicle = 'RED',
  Map<String, dynamic>? rateSummary,
  Map<String, dynamic>? availability,
}) =>
    {
      'zone_id': zoneId,
      'name': name,
      'space_type': spaceType,
      'capacity': capacity,
      'compatibility': compatibilityJson(status: status, vehicle: vehicle),
      'rate_summary': rateSummary,
      'availability': availability,
    };

Map<String, dynamic> summaryJson({
  String status = 'AVAILABLE',
  int? available = 8,
  int? total = 20,
  String coverage = 'COMPLETE',
  int eligibleZoneCount = 1,
  int freshRealtimeZoneCount = 1,
  String freshness = 'FRESH',
  String? oldestSourceUpdatedAt = '2026-10-02T02:00:00Z',
  String? oldestFetchedAt = '2026-10-02T02:01:00Z',
  List<Map<String, dynamic>>? contributingSources,
}) =>
    {
      'status': status,
      'available': available,
      'total': total,
      'coverage': coverage,
      'eligible_zone_count': eligibleZoneCount,
      'fresh_realtime_zone_count': freshRealtimeZoneCount,
      'freshness': {'status': freshness},
      'oldest_source_updated_at': oldestSourceUpdatedAt,
      'oldest_fetched_at': oldestFetchedAt,
      'contributing_sources': contributingSources ??
          [
            sourceJson(
              sourceId: 9,
              sourceType: 'OPERATOR',
              sourceUpdatedAt: '2026-10-02T02:00:00Z',
              fetchedAt: '2026-10-02T02:01:00Z',
            ),
          ],
    };

/// Summary for PARTIAL/NONE coverage: status UNKNOWN with null totals.
Map<String, dynamic> unknownSummaryJson({
  String coverage = 'NONE',
  int eligibleZoneCount = 1,
  int freshRealtimeZoneCount = 0,
  String freshness = 'UNKNOWN',
}) =>
    summaryJson(
      status: 'UNKNOWN',
      available: null,
      total: null,
      coverage: coverage,
      eligibleZoneCount: eligibleZoneCount,
      freshRealtimeZoneCount: freshRealtimeZoneCount,
      freshness: freshness,
      oldestSourceUpdatedAt: null,
      oldestFetchedAt: null,
      contributingSources: const [],
    );

Map<String, dynamic> lotJson({
  int id = 12345,
  String name = 'XX地下停車場',
  int distanceM = 420,
  double lat = 25.0331,
  double lng = 121.5628,
  String status = 'ALLOWED',
  String vehicle = 'RED',
  List<Map<String, dynamic>>? zones,
  Map<String, dynamic>? availabilitySummary,
  int rankingGroup = 0,
  int? rankingScoreBp = 7420,
}) =>
    {
      'id': id,
      'name': name,
      'distance_m': distanceM,
      'location': {'lat': lat, 'lng': lng},
      'compatibility': {'status': status, 'vehicle': vehicle},
      'zones': zones ??
          [
            zoneJson(
              vehicle: vehicle,
              rateSummary: rateSummaryJson(),
              availability: availabilityJson(),
            ),
          ],
      'availability_summary': availabilitySummary ?? summaryJson(),
      'ranking_group': rankingGroup,
      'ranking_score_bp': rankingScoreBp,
    };

/// Nearby page with a mixed-zone ALLOWED lot (ALLOWED heavy zone + UNKNOWN
/// car-shared zone with a STALE AVAILABLE observation) and an unknown-only
/// lot. Represents an `include_unknown=true` RED response.
Map<String, dynamic> nearbyFixture({
  String vehicle = 'RED',
  String? nextCursor,
  bool hasMore = false,
  String evaluationAt = fixtureEvaluationAt,
  List<Map<String, dynamic>>? items,
}) =>
    {
      'evaluation_at': evaluationAt,
      'sort_version': 1,
      'items': items ??
          [
            lotJson(
              vehicle: vehicle,
              zones: [
                zoneJson(
                  vehicle: vehicle,
                  rateSummary: rateSummaryJson(),
                  availability: availabilityJson(),
                ),
                zoneJson(
                  zoneId: 21,
                  name: 'B1 汽車格',
                  spaceType: 'CAR_SHARED',
                  capacity: 120,
                  status: 'UNKNOWN',
                  vehicle: vehicle,
                  rateSummary: rateSummaryJson(
                    comparisonEligible: false,
                    hourly: null,
                    dailyMax: null,
                    parseStatus: 'RAW_ONLY',
                  ),
                  availability: availabilityJson(
                    available: 30,
                    total: 120,
                    freshness: 'STALE',
                    fetchedAt: '2026-10-02T00:01:00Z',
                    sourceId: 10,
                  ),
                ),
              ],
            ),
            lotJson(
              id: 12346,
              name: '未確認機車停車場',
              distanceM: 610,
              lat: 25.0345,
              lng: 121.5661,
              status: 'UNKNOWN',
              vehicle: vehicle,
              zones: [
                zoneJson(
                  zoneId: 30,
                  name: null,
                  spaceType: 'MOTO_SHARED',
                  capacity: null,
                  status: 'UNKNOWN',
                  vehicle: vehicle,
                ),
              ],
              availabilitySummary: unknownSummaryJson(eligibleZoneCount: 0),
              rankingGroup: 1,
              rankingScoreBp: null,
            ),
          ],
      'page': {'next_cursor': nextCursor, 'has_more': hasMore},
    };

Map<String, dynamic> entranceJson({
  int id = 301,
  String? name = '忠孝東路入口',
  double? lat = 25.0333,
  double? lng = 121.5625,
  String access = 'ALLOWED',
}) =>
    {
      'id': id,
      'name': name,
      'location': lat == null || lng == null ? null : {'lat': lat, 'lng': lng},
      'entrance_type': 'VEHICLE',
      'heavy_motorcycle_access': access,
      'notes': null,
      'provenance': sourceJson(
        sourceId: 11,
        sourceUpdatedAt: '2026-10-01T00:00:00Z',
        fetchedAt: '2026-10-02T01:00:00Z',
      ),
    };

/// Detail with tri-state entrances: NOT_ALLOWED first, UNKNOWN second,
/// ALLOWED third, so selection must skip the first two.
Map<String, dynamic> detailFixture({
  int id = 12345,
  String vehicle = 'RED',
  String evaluationAt = fixtureEvaluationAt,
  List<Map<String, dynamic>>? entrances,
  List<Map<String, dynamic>>? zones,
}) =>
    {
      'id': id,
      'name': 'XX地下停車場',
      'vehicle': vehicle,
      'evaluation_at': evaluationAt,
      'location': {'lat': 25.0331, 'lng': 121.5628},
      'zones': zones ??
          [
            zoneJson(
              vehicle: vehicle,
              rateSummary: rateSummaryJson(),
              availability: availabilityJson(),
            ),
            zoneJson(
              zoneId: 22,
              name: '輕型機車區',
              spaceType: 'LIGHT_MOTO_ONLY',
              capacity: 50,
              status: 'NOT_ALLOWED',
              vehicle: vehicle,
            ),
          ],
      'entrances': entrances ??
          [
            entranceJson(id: 300, name: '汽車專用入口', access: 'NOT_ALLOWED'),
            entranceJson(
              id: 302,
              name: '後巷入口',
              lat: 25.0329,
              lng: 121.5631,
              access: 'UNKNOWN',
            ),
            entranceJson(),
          ],
    };
