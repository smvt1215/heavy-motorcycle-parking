import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/recent_searches_store.dart';
import 'package:heavy_parking/domain/parking.dart';
import 'package:heavy_parking/domain/place.dart';

void main() {
  final now = DateTime.utc(2026, 10, 6, 8);

  test('suggestion and destination parse backend wire shapes', () {
    final suggestion = PlaceSuggestion.fromJson({
      'place_id': 'ChIJ_abc-1',
      'primary_text': ' 台北101 ',
      'secondary_text': null,
    });
    expect(suggestion.primaryText, '台北101');
    expect(suggestion.secondaryText, isNull);

    final destination = PlaceDestination.fromJson({
      'place_id': 'ChIJ_abc-1',
      'name': '台北101',
      'address': '信義路五段7號',
      'location': {'lat': 25.03, 'lng': 121.56},
      'attribution': 'GOOGLE',
    });
    expect(destination.location, const GeoPoint(25.03, 121.56));
  });

  test('rejects unsafe place IDs and untrustworthy coordinates', () {
    expect(
      () => PlaceSuggestion.fromJson({
        'place_id': '../parking/1',
        'primary_text': 'x',
      }),
      throwsFormatException,
    );
    expect(
      () => PlaceDestination.fromJson({
        'place_id': 'A',
        'name': 'x',
        'location': {'lat': 120, 'lng': 121},
      }),
      throwsFormatException,
    );
    expect(
      () => PlaceDestination.fromJson({'place_id': 'A', 'name': 'x'}),
      throwsFormatException,
    );
  });

  test('recent coordinates expire after the 30-day cache limit', () {
    final recent = RecentDestination(
      placeId: 'A',
      name: '台北101',
      location: const GeoPoint(25.03, 121.56),
      resolvedAt: now,
      searchedAt: now,
    );
    expect(recent.locationAt(now.add(const Duration(days: 29))), isNotNull);
    expect(recent.locationAt(now.add(placeLocationCacheLimit)), isNull);
    expect(recent.locationAt(now.subtract(const Duration(minutes: 1))), isNull);

    final expired = recent.expiredAt(now.add(const Duration(days: 31)));
    expect(expired.location, isNull);
    expect(expired.toJson().containsKey('lat'), isFalse);
    expect(expired.placeId, 'A');
  });

  test('recent JSON round-trips and history de-duplicates by place', () {
    final a = RecentDestination(
      placeId: 'A',
      name: 'A',
      address: '地址',
      location: const GeoPoint(25, 121),
      resolvedAt: now,
      searchedAt: now,
    );
    final parsed = RecentDestination.fromJson(a.toJson());
    expect(parsed.location, a.location);
    expect(parsed.resolvedAt, now);
    expect(parsed.address, '地址');

    var items = <RecentDestination>[];
    for (var i = 0; i < maxRecentDestinations + 3; i++) {
      items = upsertRecent(
        items,
        RecentDestination(placeId: 'P$i', name: 'P$i', searchedAt: now),
      );
    }
    expect(items, hasLength(maxRecentDestinations));
    items = upsertRecent(items, a);
    items = upsertRecent(items, a.touched(now));
    expect(items.where((e) => e.placeId == 'A'), hasLength(1));
    expect(items.first.placeId, 'A');
  });
}
