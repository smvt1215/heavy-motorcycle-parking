import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/constants.dart';
import '../../data/parking_repository.dart';
import '../../domain/parking.dart';

const Duration apiTimeout = Duration(seconds: 15);

final parkingRepositoryProvider = Provider<ParkingRepository>((ref) {
  final dio = Dio(
    BaseOptions(
      baseUrl: AppConstants.apiBaseUrl,
      connectTimeout: apiTimeout,
      receiveTimeout: apiTimeout,
    ),
  );
  ref.onDispose(dio.close);
  return DioParkingRepository(dio);
});

/// Persistent (not autoDispose) so vehicle and filters survive rebuilds and
/// navigation away from the map.
final mapControllerProvider = StateNotifierProvider<MapController, MapState>(
  (ref) => MapController(ref.watch(parkingRepositoryProvider)),
);

const GeoPoint defaultMapCenter = GeoPoint(25.033, 121.5654);

const Object _unset = Object();

class MapState {
  const MapState({
    required this.query,
    this.items = const [],
    this.isLoading = false,
    this.isLoadingMore = false,
    this.error,
    this.needsAreaSearch = false,
    this.selectedLot,
    this.detail,
    this.detailLoading = false,
    this.detailError,
    this.evaluationAt,
    this.nextCursor,
    this.hasMore = false,
    this.sortVersion,
    this.hasSearched = false,
  });

  /// Committed query; its center is the last searched center.
  final ParkingQuery query;

  /// Server-ordered results after the defensive presentation filter.
  final List<ParkingLot> items;
  final bool isLoading;
  final bool isLoadingMore;
  final ParkingApiException? error;

  /// Camera settled away from the searched center; show `搜尋此區域`.
  final bool needsAreaSearch;
  final ParkingLot? selectedLot;
  final ParkingDetail? detail;
  final bool detailLoading;
  final ParkingApiException? detailError;

  /// Evaluation instant pinned by the first nearby page.
  final DateTime? evaluationAt;
  final String? nextCursor;
  final bool hasMore;
  final int? sortVersion;
  final bool hasSearched;

  MapState copyWith({
    ParkingQuery? query,
    List<ParkingLot>? items,
    bool? isLoading,
    bool? isLoadingMore,
    Object? error = _unset,
    bool? needsAreaSearch,
    Object? selectedLot = _unset,
    Object? detail = _unset,
    bool? detailLoading,
    Object? detailError = _unset,
    Object? evaluationAt = _unset,
    Object? nextCursor = _unset,
    bool? hasMore,
    Object? sortVersion = _unset,
    bool? hasSearched,
  }) =>
      MapState(
        query: query ?? this.query,
        items: items ?? this.items,
        isLoading: isLoading ?? this.isLoading,
        isLoadingMore: isLoadingMore ?? this.isLoadingMore,
        error: identical(error, _unset)
            ? this.error
            : error as ParkingApiException?,
        needsAreaSearch: needsAreaSearch ?? this.needsAreaSearch,
        selectedLot: identical(selectedLot, _unset)
            ? this.selectedLot
            : selectedLot as ParkingLot?,
        detail:
            identical(detail, _unset) ? this.detail : detail as ParkingDetail?,
        detailLoading: detailLoading ?? this.detailLoading,
        detailError: identical(detailError, _unset)
            ? this.detailError
            : detailError as ParkingApiException?,
        evaluationAt: identical(evaluationAt, _unset)
            ? this.evaluationAt
            : evaluationAt as DateTime?,
        nextCursor: identical(nextCursor, _unset)
            ? this.nextCursor
            : nextCursor as String?,
        hasMore: hasMore ?? this.hasMore,
        sortVersion: identical(sortVersion, _unset)
            ? this.sortVersion
            : sortVersion as int?,
        hasSearched: hasSearched ?? this.hasSearched,
      );
}

class MapController extends StateNotifier<MapState> {
  MapController(this._repository, {ParkingQuery? initialQuery})
      : super(
          MapState(
            query: initialQuery ?? const ParkingQuery(center: defaultMapCenter),
          ),
        );

  final ParkingRepository _repository;

  /// Bumped whenever a new result set is requested; stale responses with an
  /// older generation are dropped.
  int _searchGeneration = 0;
  int _detailGeneration = 0;

  /// Exact query used for the current page-1 request; continuations reuse it.
  ParkingQuery? _pageQuery;

  /// Latest camera center reported by the map; never triggers a request.
  GeoPoint? _cameraCenter;

  /// Explicit `搜尋此區域`/initial search at the latest camera center.
  Future<void> search() {
    final center = _cameraCenter ?? state.query.center;
    return _runSearch(
      state.query.copyWith(center: center),
      clearAreaPrompt: true,
    );
  }

  /// Called on every camera frame; only records the position.
  void cameraMoved(GeoPoint center) {
    _cameraCenter = center;
  }

  void cameraIdle() {
    final center = _cameraCenter;
    if (center == null) return;
    final moved = center != state.query.center;
    if (moved != state.needsAreaSearch) {
      state = state.copyWith(needsAreaSearch: moved);
    }
  }

  Future<void> setVehicle(VehicleType vehicle) async {
    if (vehicle == state.query.vehicle) return;
    await _runSearch(state.query.copyWith(vehicle: vehicle));
  }

  /// Applies filters from [filters]; the searched center is kept and an
  /// uncommitted camera position is ignored.
  Future<void> setFilters(ParkingQuery filters) async {
    final next = filters.copyWith(center: state.query.center);
    next.validate();
    await _runSearch(next);
  }

  Future<void> selectLot(ParkingLot lot) async {
    final generation = ++_detailGeneration;
    final vehicle = _pageQuery?.vehicle ?? state.query.vehicle;
    final at = state.evaluationAt;
    state = state.copyWith(
      selectedLot: lot,
      detail: null,
      detailLoading: true,
      detailError: null,
    );
    try {
      final detail = await _repository.detail(lot.id, vehicle, at: at);
      if (!_isCurrentDetail(generation)) return;
      if (detail.id != lot.id ||
          detail.vehicle != vehicle ||
          (at != null && !detail.evaluationAt.isAtSameMomentAs(at))) {
        throw const ParkingApiException(
          code: ParkingApiException.invalidResponse,
          message: 'Detail does not match the selected parking query.',
        );
      }
      state = state.copyWith(detail: detail, detailLoading: false);
    } catch (e) {
      if (!_isCurrentDetail(generation)) return;
      state = state.copyWith(detailError: _apiError(e), detailLoading: false);
    }
  }

  void closeSelection() {
    _detailGeneration++;
    state = state.copyWith(
      selectedLot: null,
      detail: null,
      detailLoading: false,
      detailError: null,
    );
  }

  Future<void> loadMore() async {
    final query = _pageQuery;
    final cursor = state.nextCursor;
    if (query == null ||
        cursor == null ||
        !state.hasMore ||
        state.isLoading ||
        state.isLoadingMore) {
      return;
    }
    final generation = _searchGeneration;
    state = state.copyWith(isLoadingMore: true, error: null);
    try {
      final page = await _repository.nearby(query, cursor: cursor);
      if (!_isCurrentSearch(generation)) return;
      final evaluationAt = state.evaluationAt;
      if (evaluationAt == null ||
          !page.evaluationAt.isAtSameMomentAs(evaluationAt) ||
          page.sortVersion != state.sortVersion) {
        throw const ParkingApiException(
          code: ParkingApiException.invalidResponse,
          message: 'Continuation does not match the original search snapshot.',
        );
      }
      final seen = {for (final lot in state.items) lot.id};
      final appended =
          _visible(page.items, query).where((lot) => seen.add(lot.id)).toList();
      state = state.copyWith(
        items: List.unmodifiable([...state.items, ...appended]),
        isLoadingMore: false,
        nextCursor: page.nextCursor,
        hasMore: page.hasMore && page.nextCursor != null,
      );
    } catch (e) {
      if (!_isCurrentSearch(generation)) return;
      final error = _apiError(e);
      final stopPaging = error.isCursorError ||
          error.code == ParkingApiException.invalidResponse;
      // Never restart page one on a bad cursor; keep loaded items and stop
      // paging until the user explicitly searches again.
      state = state.copyWith(
        isLoadingMore: false,
        error: error,
        nextCursor: stopPaging ? null : cursor,
        hasMore: !stopPaging,
      );
    }
  }

  Future<void> _runSearch(
    ParkingQuery query, {
    bool clearAreaPrompt = false,
  }) async {
    query.validate();
    final generation = ++_searchGeneration;
    _detailGeneration++;
    _pageQuery = query;
    state = state.copyWith(
      query: query,
      items: const [],
      isLoading: true,
      isLoadingMore: false,
      error: null,
      needsAreaSearch: clearAreaPrompt ? false : state.needsAreaSearch,
      selectedLot: null,
      detail: null,
      detailLoading: false,
      detailError: null,
      evaluationAt: null,
      nextCursor: null,
      hasMore: false,
      sortVersion: null,
      hasSearched: true,
    );
    try {
      final page = await _repository.nearby(query);
      if (!_isCurrentSearch(generation)) return;
      state = state.copyWith(
        items: List.unmodifiable(_visible(page.items, query)),
        isLoading: false,
        evaluationAt: page.evaluationAt,
        nextCursor: page.nextCursor,
        hasMore: page.hasMore && page.nextCursor != null,
        sortVersion: page.sortVersion,
      );
    } catch (e) {
      if (!_isCurrentSearch(generation)) return;
      state = state.copyWith(isLoading: false, error: _apiError(e));
    }
  }

  static ParkingApiException _apiError(Object error) =>
      error is ParkingApiException
          ? error
          : const ParkingApiException(
              code: ParkingApiException.unknown,
              message: 'Unable to load parking data.',
            );

  static List<ParkingLot> _visible(List<ParkingLot> lots, ParkingQuery query) =>
      [
        for (final lot in lots)
          if (lot.visibleFor(query) case final visible?) visible,
      ];

  bool _isCurrentSearch(int generation) =>
      mounted && generation == _searchGeneration;

  bool _isCurrentDetail(int generation) =>
      mounted && generation == _detailGeneration;

  @override
  void dispose() {
    _searchGeneration++;
    _detailGeneration++;
    super.dispose();
  }
}
