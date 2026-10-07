import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/api_client.dart';
import '../../data/token_store.dart';
import '../../data/user_repository.dart';
import '../../domain/parking.dart';
import '../../domain/user.dart';

final userRepositoryProvider = Provider<UserRepository>(
  (ref) => DioUserRepository(ref.watch(apiDioProvider)),
);

final tokenStoreProvider = Provider<TokenStore>((ref) => SecureTokenStore());

final authControllerProvider = StateNotifierProvider<AuthController, AuthState>(
  (ref) => AuthController(
    ref.watch(userRepositoryProvider),
    ref.watch(tokenStoreProvider),
  )..restore(),
);

final favoritesControllerProvider =
    StateNotifierProvider<FavoritesController, FavoritesState>((ref) {
  final controller = FavoritesController(
    ref.watch(userRepositoryProvider),
    ref.read(authControllerProvider.notifier),
  );
  ref.listen<bool>(
    authControllerProvider.select((state) => state.isSignedIn),
    (previous, signedIn) => signedIn ? controller.load() : controller.clear(),
    fireImmediately: true,
  );
  return controller;
});

enum AuthStatus { restoring, guest, signedIn }

const Object _unset = Object();

class AuthState {
  const AuthState({
    this.status = AuthStatus.restoring,
    this.profile,
    this.busy = false,
    this.error,
  });

  final AuthStatus status;
  final UserProfile? profile;
  final bool busy;
  final UserApiException? error;

  bool get isSignedIn => status == AuthStatus.signedIn && profile != null;

  AuthState copyWith({
    AuthStatus? status,
    Object? profile = _unset,
    bool? busy,
    Object? error = _unset,
  }) =>
      AuthState(
        status: status ?? this.status,
        profile:
            identical(profile, _unset) ? this.profile : profile as UserProfile?,
        busy: busy ?? this.busy,
        error:
            identical(error, _unset) ? this.error : error as UserApiException?,
      );
}

/// Owns the access token. Other controllers call [authorized]; any 401 ends
/// the local session so the app falls back to guest mode.
class AuthController extends StateNotifier<AuthState> {
  AuthController(this._repository, this._store) : super(const AuthState());

  final UserRepository _repository;
  final TokenStore _store;
  String? _token;
  int _vehicleGeneration = 0;

  Future<void> restore() async {
    final token = await _readToken();
    if (!mounted) return;
    if (token == null) {
      state = const AuthState(status: AuthStatus.guest);
      return;
    }
    _token = token;
    await _loadProfile();
  }

  Future<void> devSignIn(String subject, {String? displayName}) async {
    state = state.copyWith(busy: true, error: null);
    try {
      final session =
          await _repository.devSession(subject, displayName: displayName);
      _token = session.accessToken;
      await _store.write(session.accessToken);
      await _loadProfile();
    } on UserApiException catch (e) {
      if (mounted) state = state.copyWith(busy: false, error: e);
    }
  }

  Future<void> signOut() async {
    final token = _token;
    await _expire();
    if (token != null) {
      try {
        await _repository.logout(token);
      } on UserApiException {
        // Local sign-out already happened; the token also expires server-side.
      }
    }
  }

  /// Only the latest request for the still-active token may update the
  /// profile; out-of-order or previous-session responses are dropped.
  Future<void> setVehicle(VehicleType? vehicle) async {
    final generation = ++_vehicleGeneration;
    final token = _token;
    final profile = await authorized((token) {
      return _repository.setVehicle(token, vehicle);
    });
    if (!mounted ||
        generation != _vehicleGeneration ||
        token == null ||
        !identical(token, _token)) {
      return;
    }
    state = state.copyWith(profile: profile);
  }

  /// Runs [call] with the current token. Throws `UNAUTHENTICATED` for guests and
  /// signs out locally when the backend rejects the token.
  Future<T> authorized<T>(Future<T> Function(String token) call) async {
    final token = _token;
    if (token == null || !state.isSignedIn) {
      throw const UserApiException(
        code: UserApiException.unauthenticated,
        message: 'Sign in required',
      );
    }
    try {
      return await call(token);
    } on UserApiException catch (e) {
      if (e.isUnauthenticated && identical(token, _token)) await _expire();
      rethrow;
    }
  }

  Future<void> _loadProfile() async {
    final token = _token!;
    try {
      final profile = await _repository.me(token);
      if (!mounted || !identical(token, _token)) return;
      state = AuthState(status: AuthStatus.signedIn, profile: profile);
    } on UserApiException catch (e) {
      if (!mounted) return;
      if (e.isUnauthenticated) {
        await _expire();
      } else {
        // Keep the stored token for a later retry, but act as a guest now.
        state = AuthState(status: AuthStatus.guest, error: e);
      }
    }
  }

  Future<void> retry() async {
    if (_token == null) return;
    state = state.copyWith(busy: true, error: null);
    await _loadProfile();
  }

  Future<void> _expire() async {
    _token = null;
    await _store.clear();
    if (mounted) state = const AuthState(status: AuthStatus.guest);
  }

  Future<String?> _readToken() async {
    try {
      return await _store.read();
    } catch (_) {
      return null;
    }
  }
}

class FavoritesState {
  const FavoritesState({
    this.items = const [],
    this.loading = false,
    this.pending = const {},
    this.error,
  });

  final List<FavoriteParking> items;
  final bool loading;

  /// Parking IDs with an add/remove request in flight.
  final Set<int> pending;
  final UserApiException? error;

  bool contains(int parkingId) => items.any((f) => f.parkingId == parkingId);
}

class FavoritesController extends StateNotifier<FavoritesState> {
  FavoritesController(this._repository, this._auth)
      : super(const FavoritesState());

  final UserRepository _repository;
  final AuthController _auth;
  int _generation = 0;

  Future<void> load() async {
    final generation = ++_generation;
    state = FavoritesState(
      items: state.items,
      loading: true,
      pending: state.pending,
    );
    try {
      final items = await _auth.authorized(_repository.favorites);
      if (!mounted || generation != _generation) return;
      state = FavoritesState(items: List.unmodifiable(items));
    } on UserApiException catch (e) {
      if (!mounted || generation != _generation) return;
      state = FavoritesState(items: state.items, error: e);
    }
  }

  void clear() {
    _generation++;
    state = const FavoritesState();
  }

  /// Adds or removes [parkingId]. Returns false when the request failed.
  Future<bool> toggle(int parkingId) async {
    if (state.pending.contains(parkingId)) return false;
    final remove = state.contains(parkingId);
    state = FavoritesState(
      items: state.items,
      pending: {...state.pending, parkingId},
    );
    try {
      await _auth.authorized(
        (token) => remove
            ? _repository.removeFavorite(token, parkingId)
            : _repository.addFavorite(token, parkingId),
      );
      if (!mounted) return true;
      state = FavoritesState(
        items: state.items,
        pending: {...state.pending}..remove(parkingId),
      );
      await load();
      return true;
    } on UserApiException catch (e) {
      if (!mounted) return false;
      // A missing favorite on remove means the server already matches.
      final settled = remove && e.code == 'FAVORITE_NOT_FOUND';
      state = FavoritesState(
        items: state.items,
        pending: {...state.pending}..remove(parkingId),
        error: settled ? null : e,
      );
      if (settled) await load();
      return settled;
    }
  }
}
