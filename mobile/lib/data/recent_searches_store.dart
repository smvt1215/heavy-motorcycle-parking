import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../domain/place.dart';

const int maxRecentDestinations = 8;

/// Device-local destination history; never synced to the backend.
abstract interface class RecentSearchStore {
  Future<List<RecentDestination>> load();

  Future<void> save(List<RecentDestination> items);
}

class SharedPreferencesRecentSearchStore implements RecentSearchStore {
  SharedPreferencesRecentSearchStore({
    SharedPreferencesAsync? preferences,
    DateTime Function()? clock,
  })  : _preferences = preferences ?? SharedPreferencesAsync(),
        _clock = clock ?? DateTime.now;

  static const String key = 'destination_search.recent.v1';

  final SharedPreferencesAsync _preferences;
  final DateTime Function() _clock;

  @override
  Future<List<RecentDestination>> load() async {
    final raw = await _preferences.getString(key);
    if (raw == null) return const [];
    final now = _clock();
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! List) return const [];
      final items = <RecentDestination>[];
      for (final item in decoded) {
        try {
          items.add(
            RecentDestination.fromJson(Map<String, dynamic>.from(item as Map))
                .expiredAt(now),
          );
        } on Object {
          // Skip a corrupt entry; keep the rest of the history.
        }
      }
      return List.unmodifiable(items.take(maxRecentDestinations));
    } on FormatException {
      return const [];
    }
  }

  @override
  Future<void> save(List<RecentDestination> items) {
    final now = _clock();
    return _preferences.setString(
      key,
      jsonEncode([
        for (final item in items.take(maxRecentDestinations))
          item.expiredAt(now).toJson(),
      ]),
    );
  }
}

/// Moves [entry] to the front, replacing an older entry with the same place.
List<RecentDestination> upsertRecent(
  List<RecentDestination> items,
  RecentDestination entry,
) =>
    List.unmodifiable(
      [
        entry,
        ...items.where((item) => item.placeId != entry.placeId),
      ].take(maxRecentDestinations),
    );
