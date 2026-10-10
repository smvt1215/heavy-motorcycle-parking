import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/domain/parking.dart';

import '../fixtures/parking.dart';

ParkingZone _zone({
  Map<String, dynamic>? availability,
  String status = 'ALLOWED',
}) =>
    ParkingZone.fromJson(zoneJson(status: status, availability: availability));

AvailabilitySummary _summary(Map<String, dynamic> json) =>
    AvailabilitySummary.fromJson(json);

void main() {
  group('enums', () {
    test('wire values and Traditional Chinese labels', () {
      expect(VehicleType.normalHeavy.wireValue, 'NORMAL_HEAVY');
      expect(VehicleType.largeHeavy.label, '大重');
      expect(SpaceType.lightMotoOnly.wireValue, 'LIGHT_MOTO_ONLY');
      expect(SpaceType.searchable, isNot(contains(SpaceType.lightMotoOnly)));
      expect(CompatibilityStatus.unknown.label, '未確認');
      expect(FreshnessStatus.stale.label, '可能已過時');
    });

    test('unrecognized wire values are rejected, not coerced', () {
      expect(
        () => CompatibilityStatus.fromWire('MAYBE'),
        throwsFormatException,
      );
      expect(() => AvailabilityStatus.fromWire('STALE'), throwsFormatException);
      for (final legacy in ['GREEN', 'WHITE', 'YELLOW', 'RED', 'CAR']) {
        expect(() => VehicleType.fromWire(legacy), throwsFormatException);
      }
    });
  });

  test('normal class may search conventional motorcycle spaces', () {
    const normal = ParkingQuery(
      center: GeoPoint(25.03, 121.56),
      vehicle: VehicleType.normalHeavy,
      spaceType: SpaceType.lightMotoOnly,
    );
    expect(normal.toQueryParameters()['vehicle'], 'NORMAL_HEAVY');
    expect(normal.toQueryParameters()['space_type'], 'LIGHT_MOTO_ONLY');
    expect(
      () => normal.copyWith(vehicle: VehicleType.largeHeavy).validate(),
      throwsArgumentError,
    );
    expect(VehicleType.largeHeavy.formalName, contains('黃牌／紅牌'));
  });

  group('nearby page parsing', () {
    test('parses backend JSON with provenance, zones and server aggregates',
        () {
      final page = NearbyParkingPage.fromJson(
        nearbyFixture(nextCursor: 'opaque', hasMore: true),
      );
      expect(page.evaluationAt, DateTime.utc(2026, 10, 2, 9, 30));
      expect(page.sortVersion, 1);
      expect(page.nextCursor, 'opaque');
      expect(page.hasMore, isTrue);
      expect(page.items, hasLength(2));

      final lot = page.items.first;
      expect(lot.id, 12345);
      expect(lot.distanceM, 420);
      expect(lot.location, const GeoPoint(25.0331, 121.5628));
      expect(lot.compatibility, CompatibilityStatus.allowed);
      expect(lot.compatibilityVehicle, VehicleType.largeHeavy);
      expect(lot.rankingGroup, 0);
      expect(lot.rankingScoreBp, 7420);

      final heavy = lot.zones[0];
      expect(heavy.spaceType, SpaceType.heavyOnly);
      expect(heavy.compatibility.reason, 'explicit_vehicle_permission');
      expect(heavy.compatibility.confidence, 1.0);
      expect(heavy.compatibility.provenance!.sourceId, 4);
      expect(heavy.compatibility.ruleEvidence.single.ruleId, 501);
      expect(heavy.rateSummary!.confirmedHourlyRateTwd, 20);
      expect(heavy.rateSummary!.provenance!.sourceId, 7);
      expect(heavy.availability!.provenance.sourceId, 9);
      expect(
        heavy.availability!.provenance.fetchedAt,
        DateTime.utc(2026, 10, 2, 2, 1),
      );
      expect(heavy.hasConfirmedAvailability, isTrue);

      final unknownCar = lot.zones[1];
      expect(unknownCar.compatibility.status, CompatibilityStatus.unknown);
      expect(unknownCar.rateSummary!.confirmedHourlyRateTwd, isNull);
      // Stale observation keeps its observed status; freshness is separate.
      expect(unknownCar.availability!.status, AvailabilityStatus.available);
      expect(unknownCar.availability!.freshness, FreshnessStatus.stale);
      expect(unknownCar.availability!.isStale, isTrue);
      expect(unknownCar.hasConfirmedAvailability, isFalse);

      final unknownLot = page.items[1];
      expect(unknownLot.isUnverified, isTrue);
      expect(unknownLot.rankingScoreBp, isNull);
      expect(unknownLot.zones.single.name, isNull);
      expect(unknownLot.zones.single.capacity, isNull);
      expect(unknownLot.zones.single.availability, isNull);
      expect(unknownLot.availabilitySummary!.coverage, Coverage.none);
    });

    test('missing required base zone field is a format error', () {
      final json = zoneJson()..remove('availability');
      expect(() => ParkingZone.fromJson(json), throwsFormatException);
    });

    test('nullable provenance on compatibility without a single winner', () {
      final json = zoneJson();
      json['compatibility'] = compatibilityJson(
        status: 'UNKNOWN',
        confidence: null,
        withProvenance: false,
      );
      final zone = ParkingZone.fromJson(json);
      expect(zone.compatibility.provenance, isNull);
      expect(zone.compatibility.confidence, isNull);
      expect(zone.compatibility.ruleEvidence, isEmpty);
    });
  });

  group('ParkingZone.hasConfirmedAvailability', () {
    test('fresh AVAILABLE with integer counts and fetched_at qualifies', () {
      expect(
        _zone(availability: availabilityJson()).hasConfirmedAvailability,
        isTrue,
      );
      expect(
        _zone(availability: availabilityJson(total: null))
            .hasConfirmedAvailability,
        isTrue,
      );
    });

    final rejected = <String, Map<String, dynamic>?>{
      'missing realtime': null,
      'STALE freshness': availabilityJson(freshness: 'STALE'),
      'UNKNOWN freshness': availabilityJson(freshness: 'UNKNOWN'),
      'missing fetched_at': availabilityJson(fetchedAt: null),
      'zero available': availabilityJson(available: 0),
      'null available': availabilityJson(available: null),
      'non-integer available': availabilityJson(available: 2.5),
      'integral double available': availabilityJson(available: 3.0),
      'negative total': availabilityJson(total: -1),
      'available > total': availabilityJson(available: 21, total: 20),
      'FULL status': availabilityJson(status: 'FULL', available: 0),
      'UNKNOWN status': availabilityJson(status: 'UNKNOWN', available: null),
      'CLOSED status': availabilityJson(status: 'CLOSED', available: 0),
    };
    rejected.forEach((name, availability) {
      test('rejects $name', () {
        expect(
          _zone(availability: availability).hasConfirmedAvailability,
          isFalse,
        );
      });
    });

    test('rejects non-ALLOWED zones even with fresh availability', () {
      for (final status in ['UNKNOWN', 'NOT_ALLOWED']) {
        expect(
          _zone(status: status, availability: availabilityJson())
              .hasConfirmedAvailability,
          isFalse,
        );
      }
    });

    test('stale AVAILABLE keeps AVAILABLE as observed status', () {
      final a = _zone(availability: availabilityJson(freshness: 'STALE'))
          .availability!;
      expect(a.status, AvailabilityStatus.available);
      expect(a.freshness, FreshnessStatus.stale);
    });
  });

  group('AvailabilitySummary', () {
    test('server aggregate status is preserved for every combination', () {
      // Expected server rollups: AVAILABLE+FULL, AVAILABLE+CLOSED => AVAILABLE;
      // FULL+CLOSED, FULL+FULL => FULL; CLOSED+CLOSED => CLOSED.
      final cases = {
        'AVAILABLE': AvailabilityStatus.available,
        'FULL': AvailabilityStatus.full,
        'CLOSED': AvailabilityStatus.closed,
        'UNKNOWN': AvailabilityStatus.unknown,
      };
      cases.forEach((wire, expected) {
        final summary = _summary(
          summaryJson(
            status: wire,
            available: wire == 'AVAILABLE' ? 3 : 0,
            total: 40,
            eligibleZoneCount: 2,
            freshRealtimeZoneCount: 2,
          ),
        );
        expect(summary.status, expected);
      });
    });

    test('COMPLETE fresh aggregate can show totals', () {
      final summary = _summary(summaryJson(status: 'FULL', available: 0));
      expect(summary.canShowTotals, isTrue);
      expect(summary.status, AvailabilityStatus.full);
      expect(_summary(summaryJson()).canShowTotals, isTrue);
      expect(
        _summary(summaryJson(status: 'CLOSED', available: 0)).canShowTotals,
        isTrue,
      );
    });

    test('status/count contradictions never show confirmed totals', () {
      final contradictions = [
        summaryJson(status: 'UNKNOWN'),
        summaryJson(status: 'AVAILABLE', available: 0),
        summaryJson(status: 'FULL', available: 3),
        summaryJson(status: 'CLOSED', available: 3),
      ];
      for (final json in contradictions) {
        expect(_summary(json).canShowTotals, isFalse, reason: '$json');
      }
    });

    test('PARTIAL and NONE never show totals', () {
      for (final coverage in ['PARTIAL', 'NONE']) {
        final summary = _summary(
          unknownSummaryJson(
            coverage: coverage,
            eligibleZoneCount: 2,
            freshRealtimeZoneCount: coverage == 'PARTIAL' ? 1 : 0,
          ),
        );
        expect(summary.status, AvailabilityStatus.unknown);
        expect(summary.canShowTotals, isFalse);
      }
      // Even if a misbehaving server sent numbers with PARTIAL.
      expect(
        _summary(summaryJson(coverage: 'PARTIAL')).canShowTotals,
        isFalse,
      );
    });

    test('COMPLETE without fresh, fetched provenance cannot show totals', () {
      expect(_summary(summaryJson(freshness: 'STALE')).canShowTotals, isFalse);
      expect(
        _summary(summaryJson(oldestFetchedAt: null)).canShowTotals,
        isFalse,
      );
      expect(
        _summary(summaryJson(contributingSources: const [])).canShowTotals,
        isFalse,
      );
      expect(
        _summary(
          summaryJson(
            contributingSources: [
              sourceJson(),
              sourceJson(sourceId: 5, fetchedAt: null),
            ],
          ),
        ).canShowTotals,
        isFalse,
      );
      expect(_summary(summaryJson(total: null)).canShowTotals, isFalse);
      expect(
        _summary(summaryJson(eligibleZoneCount: 0, freshRealtimeZoneCount: 0))
            .canShowTotals,
        isFalse,
      );
      expect(
        _summary(summaryJson(eligibleZoneCount: 2, freshRealtimeZoneCount: 1))
            .canShowTotals,
        isFalse,
      );
      expect(
        _summary(summaryJson(available: 30, total: 20)).canShowTotals,
        isFalse,
      );
    });
  });

  group('ParkingDetail.navigationTarget', () {
    test('parses tri-state entrances and selects the ALLOWED one', () {
      final detail = ParkingDetail.fromJson(detailFixture());
      expect(detail.vehicle, VehicleType.largeHeavy);
      expect(detail.evaluationAt, DateTime.utc(2026, 10, 2, 9, 30));
      expect(
        detail.entrances.map((e) => e.heavyMotorcycleAccess),
        [
          CompatibilityStatus.notAllowed,
          CompatibilityStatus.unknown,
          CompatibilityStatus.allowed,
        ],
      );
      expect(detail.entrances.first.provenance.sourceId, 11);
      expect(detail.unverifiedEntrances.single.id, 302);

      final target = detail.navigationTarget;
      expect(target.isConfirmedEntrance, isTrue);
      expect(target.entrance!.id, 301);
      expect(target.location, const GeoPoint(25.0333, 121.5625));
      expect(target.label, '忠孝東路入口');
    });

    test('UNKNOWN and NOT_ALLOWED entrances fall back to labeled lot center',
        () {
      final detail = ParkingDetail.fromJson(
        detailFixture(
          entrances: [
            entranceJson(id: 1, access: 'NOT_ALLOWED'),
            entranceJson(id: 2, access: 'UNKNOWN'),
          ],
        ),
      );
      final target = detail.navigationTarget;
      expect(target.isConfirmedEntrance, isFalse);
      expect(target.entrance, isNull);
      expect(target.location, detail.location);
      expect(target.label, ParkingDetail.lotCenterFallbackLabel);
    });

    test('ALLOWED entrance without coordinates is skipped', () {
      final detail = ParkingDetail.fromJson(
        detailFixture(
          entrances: [
            entranceJson(id: 1, lat: null, lng: null),
            entranceJson(id: 2, name: null),
          ],
        ),
      );
      final target = detail.navigationTarget;
      expect(target.entrance!.id, 2);
      expect(target.label, ParkingDetail.confirmedEntranceFallbackLabel);
    });

    test('no entrances falls back to lot center', () {
      final detail = ParkingDetail.fromJson(detailFixture(entrances: const []));
      expect(detail.navigationTarget.isConfirmedEntrance, isFalse);
    });
  });

  group('ParkingQuery', () {
    const center = GeoPoint(25.033, 121.5654);

    test('defaults emit only required parameters', () {
      expect(const ParkingQuery(center: center).toQueryParameters(), {
        'lat': 25.033,
        'lng': 121.5654,
        'vehicle': 'LARGE_HEAVY',
        'radius': 1000,
      });
    });

    test('all filters map to backend parameter names', () {
      final query = ParkingQuery(
        center: center,
        vehicle: VehicleType.normalHeavy,
        radius: 3000,
        spaceType: SpaceType.motoShared,
        availableOnly: true,
        hourlyRateMaxTwd: 30,
        dailyMaxRequired: true,
        includeUnknown: true,
        at: DateTime.parse('2026-10-02T17:30:00+08:00'),
        limit: 50,
      );
      expect(query.toQueryParameters(), {
        'lat': 25.033,
        'lng': 121.5654,
        'vehicle': 'NORMAL_HEAVY',
        'radius': 3000,
        'space_type': 'MOTO_SHARED',
        'available_only': true,
        'hourly_rate_max_twd': 30,
        'daily_max_required': true,
        'include_unknown': true,
        'at': '2026-10-02T09:30:00.000Z',
        'limit': 50,
      });
    });

    test('LIGHT_MOTO_ONLY is never a search filter', () {
      const query =
          ParkingQuery(center: center, spaceType: SpaceType.lightMotoOnly);
      expect(query.toQueryParameters, throwsArgumentError);
    });

    test('nonfinite or out-of-range center is rejected', () {
      for (final point in const [
        GeoPoint(double.nan, 121.5),
        GeoPoint(25, double.infinity),
        GeoPoint(91, 121.5),
        GeoPoint(25, -181),
      ]) {
        expect(point.isValid, isFalse);
        expect(
          ParkingQuery(center: point).toQueryParameters,
          throwsArgumentError,
        );
      }
      expect(center.isValid, isTrue);
    });

    test('invalid radius or rate is rejected', () {
      expect(
        const ParkingQuery(center: center, radius: 6000).toQueryParameters,
        throwsArgumentError,
      );
      expect(
        const ParkingQuery(center: center, hourlyRateMaxTwd: -1)
            .toQueryParameters,
        throwsArgumentError,
      );
    });

    test('copyWith keeps omitted values and clears explicit nulls', () {
      const query = ParkingQuery(
        center: center,
        spaceType: SpaceType.heavyOnly,
        hourlyRateMaxTwd: 20,
      );
      expect(query.copyWith(radius: 500).spaceType, SpaceType.heavyOnly);
      expect(query.copyWith(radius: 500).hourlyRateMaxTwd, 20);
      final cleared = query.copyWith(spaceType: null, hourlyRateMaxTwd: null);
      expect(cleared.spaceType, isNull);
      expect(cleared.hourlyRateMaxTwd, isNull);
      expect(cleared, const ParkingQuery(center: center));
    });
  });

  group('ParkingLot.visibleFor', () {
    const center = GeoPoint(25.033, 121.5654);
    final page = NearbyParkingPage.fromJson(nearbyFixture());

    test('drops UNKNOWN zones and unknown-only lots by default', () {
      const query = ParkingQuery(center: center);
      final allowed = page.items[0].visibleFor(query)!;
      expect(allowed.zones.map((z) => z.zoneId), [20]);
      // Server aggregate and ranking are preserved, not recomputed.
      expect(
        allowed.availabilitySummary,
        same(page.items[0].availabilitySummary),
      );
      expect(allowed.rankingScoreBp, 7420);
      expect(page.items[1].visibleFor(query), isNull);
    });

    test('keeps UNKNOWN zones when include_unknown is requested', () {
      const query = ParkingQuery(center: center, includeUnknown: true);
      expect(page.items[0].visibleFor(query), same(page.items[0]));
      expect(page.items[1].visibleFor(query), same(page.items[1]));
    });

    test('drops NOT_ALLOWED, LIGHT_MOTO_ONLY and wrong-vehicle zones', () {
      final lot = ParkingLot.fromJson(
        lotJson(
          zones: [
            zoneJson(),
            zoneJson(zoneId: 21, status: 'NOT_ALLOWED'),
            zoneJson(zoneId: 22, spaceType: 'LIGHT_MOTO_ONLY'),
            zoneJson(zoneId: 23, vehicle: 'NORMAL_HEAVY'),
          ],
        ),
      );
      const query = ParkingQuery(center: center, includeUnknown: true);
      expect(lot.visibleFor(query)!.zones.map((z) => z.zoneId), [20]);
    });

    test('drops a lot whose vehicle differs from the query', () {
      const query =
          ParkingQuery(center: center, vehicle: VehicleType.normalHeavy);
      expect(page.items[0].visibleFor(query), isNull);
    });
  });
}
