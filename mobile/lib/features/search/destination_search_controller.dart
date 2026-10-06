import 'dart:async';
import 'dart:math';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/api_client.dart';
import '../../data/places_repository.dart';
import '../../data/recent_searches_store.dart';
import '../../domain/parking.dart';
import '../../domain/place.dart';
import '../map/map_controller.dart';

/// Quiet period after the last keystroke before Autocomplete is requested.
const Duration destinationSearchDebounce = Duration(milliseconds: 350);

/// Shorter inputs show recent searches instead of requesting Places.
const int destinationSearchMinLength = 2;

final placesRepositoryProvider = Provider<PlacesRepository>(
  (ref) => DioPlacesRepository(ref.watch(apiDioProvider)),
);

final recentSearchStoreProvider = Provider<RecentSearchStore>(
  (ref) => SharedPreferencesRecentSearchStore(),
);

/// One controller per opened search screen, so each opening starts a new
/// Places session and abandons nothing billable when closed.
final destinationSearchControllerProvider = StateNotifierProvider.autoDispose<
    DestinationSearchController, DestinationSearchState>(
  (ref) => DestinationSearchController(
    ref.watch(placesRepositoryProvider),
    ref.watch(recentSearchStoreProvider),
    bias: ref.read(mapControllerProvider).query.center,
  ),
);

enum DestinationSearchStatus { idle, loading, results, empty, error }

const Object _unset = Object();

class DestinationSearchState {
  const DestinationSearchState({
    this.query = '',
    this.status = DestinationSearchStatus.idle,
    this.suggestions = const [],
    this.error,
    this.recents = const [],
    this.resolvingPlaceId,
    this.selectionError,
  });

  /// Trimmed text the current suggestions/status refer to.
  final String query;
  final DestinationSearchStatus status;
  final List<PlaceSuggestion> suggestions;
  final PlacesException? error;
  final List<RecentDestination> recents;

  /// Place being resolved to coordinates; selection inputs are disabled.
  final String? resolvingPlaceId;
  final PlacesException? selectionError;

  bool get isResolving => resolvingPlaceId != null;

  DestinationSearchState copyWith({
    String? query,
    DestinationSearchStatus? status,
    List<PlaceSuggestion>? suggestions,
    Object? error = _unset,
    List<RecentDestination>? recents,
    Object? resolvingPlaceId = _unset,
    Object? selectionError = _unset,
  }) =>
      DestinationSearchState(
        query: query ?? this.query,
        status: status ?? this.status,
        suggestions: suggestions ?? this.suggestions,
        error:
            identical(error, _unset) ? this.error : error as PlacesException?,
        recents: recents ?? this.recents,
        resolvingPlaceId: identical(resolvingPlaceId, _unset)
            ? this.resolvingPlaceId
            : resolvingPlaceId as String?,
        selectionError: identical(selectionError, _unset)
            ? this.selectionError
            : selectionError as PlacesException?,
      );
}

/// Random UUIDv4 used as a Places Autocomplete session token.
String newPlacesSessionToken([Random? random]) {
  final rng = random ?? Random.secure();
  final bytes = List<int>.generate(16, (_) => rng.nextInt(256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
      '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
}

class DestinationSearchController
    extends StateNotifier<DestinationSearchState> {
  DestinationSearchController(
    this._places,
    this._recentStore, {
    this.bias,
    Duration debounce = destinationSearchDebounce,
    String Function()? sessionTokens,
    DateTime Function()? clock,
  })  : _debounceDuration = debounce,
        _newSessionToken = sessionTokens ?? newPlacesSessionToken,
        _clock = clock ?? DateTime.now,
        super(const DestinationSearchState()) {
    _recentsReady = _loadRecents();
  }

  final PlacesRepository _places;
  final RecentSearchStore _recentStore;

  /// Optional location bias (the map's searched center); never a filter.
  final GeoPoint? bias;
  final Duration _debounceDuration;
  final String Function() _newSessionToken;
  final DateTime Function() _clock;

  late final Future<void> _recentsReady;
  Timer? _debounce;
  CancelToken? _inFlight;

  /// Bumped on every new input/selection/cancel; stale responses are dropped.
  int _generation = 0;

  /// Current billed Autocomplete session. Created lazily by the first
  /// Autocomplete request and ended by the details request or cancellation.
  String? _sessionToken;

  /// Called on every keystroke. Requests are debounced, and any pending or
  /// in-flight request for older text is cancelled.
  void queryChanged(String text) {
    final query = text.trim();
    if (query == state.query &&
        state.status != DestinationSearchStatus.idle &&
        state.status != DestinationSearchStatus.error) {
      return;
    }
    _cancelPending();
    if (query.length < destinationSearchMinLength) {
      state = state.copyWith(
        query: query,
        status: DestinationSearchStatus.idle,
        suggestions: const [],
        error: null,
        selectionError: null,
      );
      return;
    }
    final generation = _generation;
    state = state.copyWith(
      query: query,
      status: DestinationSearchStatus.loading,
      error: null,
      selectionError: null,
    );
    _debounce = Timer(_debounceDuration, () => _fetch(query, generation));
  }

  /// Immediately repeats the current query after an error.
  Future<void> retry() async {
    final query = state.query;
    if (query.length < destinationSearchMinLength) return;
    _cancelPending();
    state = state.copyWith(
      status: DestinationSearchStatus.loading,
      error: null,
      selectionError: null,
    );
    await _fetch(query, _generation);
  }

  Future<void> _fetch(String query, int generation) async {
    if (!_isCurrent(generation)) return;
    final token = _sessionToken ??= _newSessionToken();
    final cancel = _inFlight = CancelToken();
    try {
      final suggestions = await _places.autocomplete(
        query,
        sessionToken: token,
        bias: bias,
        cancelToken: cancel,
      );
      if (!_isCurrent(generation)) return;
      state = state.copyWith(
        status: suggestions.isEmpty
            ? DestinationSearchStatus.empty
            : DestinationSearchStatus.results,
        suggestions: List.unmodifiable(suggestions),
        error: null,
      );
    } catch (e) {
      if (!_isCurrent(generation)) return;
      final error = _placesError(e);
      if (error.isCancelled) return;
      state = state.copyWith(
        status: DestinationSearchStatus.error,
        suggestions: const [],
        error: error,
      );
    } finally {
      if (identical(_inFlight, cancel)) _inFlight = null;
    }
  }

  /// Resolves [suggestion] to coordinates, ending the Places session.
  /// Returns null when cancelled, superseded or failed.
  Future<PlaceDestination?> select(PlaceSuggestion suggestion) {
    final token = _sessionToken ?? _newSessionToken();
    return _resolve(suggestion.placeId, token);
  }

  /// Uses cached coordinates within the 30-day limit; otherwise re-resolves
  /// the stored place ID in a fresh session.
  Future<PlaceDestination?> selectRecent(RecentDestination recent) async {
    final cached = recent.destinationAt(_clock());
    if (cached == null) {
      return _resolve(recent.placeId, _newSessionToken());
    }
    _cancelPending();
    _sessionToken = null;
    await _remember(recent.touched(_clock()));
    return cached;
  }

  Future<PlaceDestination?> _resolve(String placeId, String token) async {
    _cancelPending();
    // A details request terminates the session whether or not it succeeds.
    _sessionToken = null;
    final generation = _generation;
    final cancel = _inFlight = CancelToken();
    state = state.copyWith(resolvingPlaceId: placeId, selectionError: null);
    try {
      final destination = await _places.details(
        placeId,
        sessionToken: token,
        cancelToken: cancel,
      );
      if (!_isCurrent(generation)) return null;
      state = state.copyWith(resolvingPlaceId: null);
      await _remember(
        RecentDestination.fromDestination(destination, _clock()),
      );
      return destination;
    } catch (e) {
      if (!_isCurrent(generation)) return null;
      final error = _placesError(e);
      state = state.copyWith(
        resolvingPlaceId: null,
        selectionError: error.isCancelled ? null : error,
      );
      return null;
    } finally {
      if (identical(_inFlight, cancel)) _inFlight = null;
    }
  }

  /// Abandons the current input, pending debounce and in-flight request.
  void cancel() {
    _cancelPending();
    _sessionToken = null;
    state = state.copyWith(
      query: '',
      status: DestinationSearchStatus.idle,
      suggestions: const [],
      error: null,
      resolvingPlaceId: null,
      selectionError: null,
    );
  }

  Future<void> removeRecent(String placeId) async {
    await _recentsReady;
    await _persist(
      List.unmodifiable(state.recents.where((r) => r.placeId != placeId)),
    );
  }

  Future<void> clearRecents() async {
    await _recentsReady;
    await _persist(const []);
  }

  Future<void> _loadRecents() async {
    try {
      final recents = await _recentStore.load();
      if (mounted) state = state.copyWith(recents: recents);
    } catch (_) {
      // History is a convenience; search works without it.
    }
  }

  Future<void> _remember(RecentDestination entry) async {
    await _recentsReady;
    await _persist(upsertRecent(state.recents, entry));
  }

  Future<void> _persist(List<RecentDestination> recents) async {
    if (mounted) state = state.copyWith(recents: recents);
    try {
      await _recentStore.save(recents);
    } catch (_) {
      // Keep the in-memory history when the device store is unavailable.
    }
  }

  void _cancelPending() {
    _generation++;
    _debounce?.cancel();
    _debounce = null;
    _inFlight?.cancel();
    _inFlight = null;
  }

  bool _isCurrent(int generation) => mounted && generation == _generation;

  static PlacesException _placesError(Object error) => error is PlacesException
      ? error
      : const PlacesException(
          code: PlacesException.unknown,
          message: 'Unable to search destinations.',
        );

  @override
  void dispose() {
    _cancelPending();
    _sessionToken = null;
    super.dispose();
  }
}

/// User-facing Traditional Chinese error text.
String destinationErrorMessage(PlacesException error) => switch (error.code) {
      PlacesException.network => '網路連線異常，請檢查連線後重試。',
      PlacesException.unavailable => '目的地搜尋暫時無法使用，仍可移動地圖搜尋此區域。',
      PlacesException.rateLimited => '搜尋太頻繁，請稍候再試。',
      PlacesException.notFound => '找不到這個地點，請重新搜尋。',
      _ => '目的地搜尋失敗，請稍後再試。',
    };
