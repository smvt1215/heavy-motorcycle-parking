import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/recent_searches_store.dart';
import 'package:heavy_parking/domain/parking.dart';
import 'package:heavy_parking/domain/place.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:shared_preferences_platform_interface/in_memory_shared_preferences_async.dart';
import 'package:shared_preferences_platform_interface/shared_preferences_async_platform_interface.dart';

void main() {
  late DateTime now;
  late SharedPreferencesRecentSearchStore store;

  setUp(() {
    SharedPreferencesAsyncPlatform.instance =
        InMemorySharedPreferencesAsync.empty();
    now = DateTime.utc(2026, 10, 6);
    store = SharedPreferencesRecentSearchStore(clock: () => now);
  });

  RecentDestination entry(String id) => RecentDestination(
        placeId: id,
        name: id,
        location: const GeoPoint(25.03, 121.56),
        resolvedAt: now,
        searchedAt: now,
      );

  test('persists history locally and reloads it in order', () async {
    expect(await store.load(), isEmpty);
    await store.save([entry('B'), entry('A')]);
    final loaded = await store.load();
    expect(loaded.map((e) => e.placeId), ['B', 'A']);
    expect(loaded.first.locationAt(now), const GeoPoint(25.03, 121.56));
  });

  test('drops coordinates older than 30 days but keeps the place', () async {
    await store.save([entry('A')]);
    now = now.add(const Duration(days: 31));
    final loaded = await store.load();
    expect(loaded.single.placeId, 'A');
    expect(loaded.single.location, isNull);
  });

  test('corrupt storage is ignored entry by entry', () async {
    final prefs = SharedPreferencesAsync();
    await prefs.setString(
      SharedPreferencesRecentSearchStore.key,
      '[{"place_id":"bad id"},{"place_id":"A","name":"A","searched_at":"2026-10-06T00:00:00Z"}]',
    );
    expect((await store.load()).map((e) => e.placeId), ['A']);
    await prefs.setString(SharedPreferencesRecentSearchStore.key, '{oops');
    expect(await store.load(), isEmpty);
  });
}
