import 'dart:async';

import 'package:dio/dio.dart';
import 'package:heavy_parking/data/places_repository.dart';
import 'package:heavy_parking/data/recent_searches_store.dart';
import 'package:heavy_parking/domain/parking.dart';
import 'package:heavy_parking/domain/place.dart';

const taipei101Id = 'ChIJH56c2rarQjQRphD9gvC8BhI';
const taipei101 = PlaceDestination(
  placeId: taipei101Id,
  name: '台北101',
  address: '110台灣台北市信義區信義路五段7號',
  location: GeoPoint(25.0339639, 121.5644722),
);
const taipei101Suggestion = PlaceSuggestion(
  placeId: taipei101Id,
  primaryText: '台北101',
  secondaryText: '台灣台北市信義區信義路五段7號',
);

class AutocompleteCall {
  AutocompleteCall(this.input, this.sessionToken, this.bias, this.cancelToken);
  final String input;
  final String sessionToken;
  final GeoPoint? bias;
  final CancelToken? cancelToken;
}

class DetailsCall {
  DetailsCall(this.placeId, this.sessionToken);
  final String placeId;
  final String sessionToken;
}

/// Scriptable Places fake. By default `台北...` inputs return Taipei 101 and
/// anything else returns no suggestions.
class FakePlacesRepository implements PlacesRepository {
  final autocompleteCalls = <AutocompleteCall>[];
  final detailsCalls = <DetailsCall>[];
  PlacesException? autocompleteError;
  PlacesException? detailsError;

  /// When set, autocomplete waits for this completer (to test cancellation).
  Completer<List<PlaceSuggestion>>? pendingAutocomplete;

  @override
  Future<List<PlaceSuggestion>> autocomplete(
    String input, {
    required String sessionToken,
    GeoPoint? bias,
    CancelToken? cancelToken,
  }) async {
    autocompleteCalls
        .add(AutocompleteCall(input, sessionToken, bias, cancelToken));
    final pending = pendingAutocomplete;
    if (pending != null) return pending.future;
    if (autocompleteError case final error?) throw error;
    return input.startsWith('台北') ? const [taipei101Suggestion] : const [];
  }

  @override
  Future<PlaceDestination> details(
    String placeId, {
    required String sessionToken,
    CancelToken? cancelToken,
  }) async {
    detailsCalls.add(DetailsCall(placeId, sessionToken));
    if (detailsError case final error?) throw error;
    return taipei101;
  }
}

class MemoryRecentSearchStore implements RecentSearchStore {
  MemoryRecentSearchStore([this.items = const []]);
  List<RecentDestination> items;
  int saves = 0;

  @override
  Future<List<RecentDestination>> load() async => items;

  @override
  Future<void> save(List<RecentDestination> items) async {
    saves++;
    this.items = items;
  }
}
