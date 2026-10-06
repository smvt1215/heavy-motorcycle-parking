import 'dart:async';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/places_repository.dart';
import 'package:heavy_parking/domain/parking.dart';
import 'package:heavy_parking/domain/place.dart';
import 'package:heavy_parking/features/search/destination_search_controller.dart';

import '../fixtures/places.dart';

void main() {
  late FakePlacesRepository places;
  late MemoryRecentSearchStore store;
  late int tokens;
  final now = DateTime.utc(2026, 10, 6, 8);

  setUp(() {
    places = FakePlacesRepository();
    store = MemoryRecentSearchStore();
    tokens = 0;
  });

  DestinationSearchController build() => DestinationSearchController(
        places,
        store,
        bias: const GeoPoint(25.04, 121.55),
        sessionTokens: () => 'session-${++tokens}',
        clock: () => now,
      );

  void type(DestinationSearchController controller, String text) {
    for (var i = 1; i <= text.length; i++) {
      controller.queryChanged(text.substring(0, i));
    }
  }

  test('searching 台北101 debounces keystrokes into one Places request', () {
    fakeAsync((async) {
      final controller = build();
      type(controller, '台北101');
      expect(controller.state.status, DestinationSearchStatus.loading);
      async.elapse(destinationSearchDebounce - const Duration(milliseconds: 1));
      expect(places.autocompleteCalls, isEmpty);
      async.elapse(const Duration(milliseconds: 1));
      async.flushMicrotasks();

      expect(places.autocompleteCalls, hasLength(1));
      final call = places.autocompleteCalls.single;
      expect(call.input, '台北101');
      expect(call.bias, const GeoPoint(25.04, 121.55));
      expect(controller.state.status, DestinationSearchStatus.results);
      expect(controller.state.suggestions.single.placeId, taipei101Id);
      controller.dispose();
    });
  });

  test('autocomplete and details share one session; next search is new', () {
    fakeAsync((async) {
      final controller = build();
      type(controller, '台北');
      async.elapse(destinationSearchDebounce);
      controller.queryChanged('台北101');
      async.elapse(destinationSearchDebounce);
      async.flushMicrotasks();
      expect(
        places.autocompleteCalls.map((c) => c.sessionToken),
        ['session-1', 'session-1'],
      );

      PlaceDestination? selected;
      controller.select(taipei101Suggestion).then((d) => selected = d);
      async.flushMicrotasks();
      expect(selected?.location, taipei101.location);
      expect(places.detailsCalls.single.sessionToken, 'session-1');
      expect(store.items.single.placeId, taipei101Id);
      expect(controller.state.recents.single.name, '台北101');

      controller.queryChanged('台北車站');
      async.elapse(destinationSearchDebounce);
      async.flushMicrotasks();
      expect(places.autocompleteCalls.last.sessionToken, 'session-2');
      controller.dispose();
    });
  });

  test('short input shows recent history and sends no request', () {
    fakeAsync((async) {
      final controller = build();
      controller.queryChanged(' 台 ');
      async.elapse(const Duration(seconds: 2));
      expect(places.autocompleteCalls, isEmpty);
      expect(controller.state.status, DestinationSearchStatus.idle);
      controller.dispose();
    });
  });

  test('no result is an explicit empty state', () {
    fakeAsync((async) {
      final controller = build();
      controller.queryChanged('查無此地zz');
      async.elapse(destinationSearchDebounce);
      async.flushMicrotasks();
      expect(controller.state.status, DestinationSearchStatus.empty);
      expect(controller.state.suggestions, isEmpty);
      controller.dispose();
    });
  });

  test('API error is surfaced and retry reuses the open session', () {
    fakeAsync((async) {
      final controller = build();
      places.autocompleteError = const PlacesException(
        code: PlacesException.network,
        message: 'offline',
      );
      controller.queryChanged('台北101');
      async.elapse(destinationSearchDebounce);
      async.flushMicrotasks();
      expect(controller.state.status, DestinationSearchStatus.error);
      expect(
        destinationErrorMessage(controller.state.error!),
        contains('網路'),
      );

      places.autocompleteError = null;
      controller.retry();
      async.flushMicrotasks();
      expect(controller.state.status, DestinationSearchStatus.results);
      expect(places.autocompleteCalls, hasLength(2));
      expect(
        places.autocompleteCalls.map((c) => c.sessionToken).toSet(),
        {'session-1'},
      );
      controller.dispose();
    });
  });

  test('details failure keeps suggestions and reports a selection error', () {
    fakeAsync((async) {
      final controller = build();
      controller.queryChanged('台北101');
      async.elapse(destinationSearchDebounce);
      async.flushMicrotasks();
      places.detailsError = const PlacesException(
        code: PlacesException.notFound,
        message: 'gone',
      );
      PlaceDestination? selected = taipei101;
      controller.select(taipei101Suggestion).then((d) => selected = d);
      async.flushMicrotasks();
      expect(selected, isNull);
      expect(controller.state.selectionError?.code, PlacesException.notFound);
      expect(controller.state.suggestions, isNotEmpty);
      expect(controller.state.isResolving, isFalse);
      expect(store.items, isEmpty);
      controller.dispose();
    });
  });

  test('cancellation aborts in-flight requests and ignores late results', () {
    fakeAsync((async) {
      final controller = build();
      final pending = places.pendingAutocomplete = Completer();
      controller.queryChanged('台北101');
      async.elapse(destinationSearchDebounce);
      final call = places.autocompleteCalls.single;
      expect(call.cancelToken!.isCancelled, isFalse);

      controller.cancel();
      expect(call.cancelToken!.isCancelled, isTrue);
      pending.complete(const [taipei101Suggestion]);
      async.flushMicrotasks();
      expect(controller.state.status, DestinationSearchStatus.idle);
      expect(controller.state.suggestions, isEmpty);

      places.pendingAutocomplete = null;
      controller.queryChanged('台北車站');
      async.elapse(destinationSearchDebounce);
      async.flushMicrotasks();
      expect(places.autocompleteCalls.last.sessionToken, 'session-2');
      controller.dispose();
    });
  });

  test('a newer keystroke supersedes an in-flight older request', () {
    fakeAsync((async) {
      final controller = build();
      final pending = places.pendingAutocomplete = Completer();
      controller.queryChanged('台北');
      async.elapse(destinationSearchDebounce);
      final first = places.autocompleteCalls.single;
      places.pendingAutocomplete = null;
      controller.queryChanged('查無此地');
      expect(first.cancelToken!.isCancelled, isTrue);
      async.elapse(destinationSearchDebounce);
      pending.complete(const [taipei101Suggestion]);
      async.flushMicrotasks();
      expect(controller.state.query, '查無此地');
      expect(controller.state.status, DestinationSearchStatus.empty);
      controller.dispose();
    });
  });

  test('cancelled transport error is not shown as a failure', () {
    fakeAsync((async) {
      final controller = build();
      places.autocompleteError = const PlacesException(
        code: PlacesException.cancelled,
        message: 'cancelled',
      );
      controller.queryChanged('台北101');
      async.elapse(destinationSearchDebounce);
      async.flushMicrotasks();
      expect(controller.state.status, isNot(DestinationSearchStatus.error));
      controller.dispose();
    });
  });

  test('disposing mid-debounce never calls Places', () {
    fakeAsync((async) {
      final controller = build();
      controller.queryChanged('台北101');
      controller.dispose();
      async.elapse(const Duration(seconds: 1));
      expect(places.autocompleteCalls, isEmpty);
    });
  });

  test('fresh recent uses cached coordinates without a Places call', () {
    fakeAsync((async) {
      store.items = [
        RecentDestination.fromDestination(
          taipei101,
          now.subtract(const Duration(days: 2)),
        ),
      ];
      final controller = build();
      async.flushMicrotasks();
      PlaceDestination? selected;
      controller
          .selectRecent(controller.state.recents.single)
          .then((d) => selected = d);
      async.flushMicrotasks();
      expect(selected?.location, taipei101.location);
      expect(places.detailsCalls, isEmpty);
      expect(store.items.single.searchedAt, now);
      controller.dispose();
    });
  });

  test('expired recent re-resolves its place ID in a new session', () {
    fakeAsync((async) {
      store.items = [
        RecentDestination(
          placeId: taipei101Id,
          name: '台北101',
          searchedAt: DateTime.utc(2026, 8, 1),
        ),
      ];
      final controller = build();
      async.flushMicrotasks();
      PlaceDestination? selected;
      controller
          .selectRecent(controller.state.recents.single)
          .then((d) => selected = d);
      async.flushMicrotasks();
      expect(selected?.placeId, taipei101Id);
      expect(places.detailsCalls.single.sessionToken, 'session-1');
      expect(store.items.single.locationAt(now), taipei101.location);
      controller.dispose();
    });
  });

  test('recent entries can be removed and cleared', () {
    fakeAsync((async) {
      store.items = [
        RecentDestination(placeId: 'A', name: 'A', searchedAt: now),
        RecentDestination(placeId: 'B', name: 'B', searchedAt: now),
      ];
      final controller = build();
      async.flushMicrotasks();
      controller.removeRecent('A');
      async.flushMicrotasks();
      expect(store.items.map((e) => e.placeId), ['B']);
      controller.clearRecents();
      async.flushMicrotasks();
      expect(store.items, isEmpty);
      expect(controller.state.recents, isEmpty);
      controller.dispose();
    });
  });

  test('session tokens are random UUIDv4 strings', () {
    final a = newPlacesSessionToken();
    final b = newPlacesSessionToken();
    final uuid = RegExp(
      r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    );
    expect(uuid.hasMatch(a), isTrue);
    expect(a, isNot(b));
  });
}
