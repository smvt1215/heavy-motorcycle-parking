import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/user_repository.dart';
import 'package:heavy_parking/domain/parking.dart';
import 'package:heavy_parking/domain/user.dart';
import 'package:heavy_parking/features/account/account_controller.dart';

import '../fixtures/user.dart';

void main() {
  late FakeUserRepository repo;
  late MemoryTokenStore store;

  ProviderContainer container() {
    final c = ProviderContainer(
      overrides: [
        userRepositoryProvider.overrideWithValue(repo),
        tokenStoreProvider.overrideWithValue(store),
      ],
    );
    addTearDown(c.dispose);
    return c;
  }

  Future<void> settle() => Future<void>.delayed(Duration.zero);

  setUp(() {
    repo = FakeUserRepository();
    store = MemoryTokenStore();
  });

  test('without a stored token the app is a guest', () async {
    final c = container();
    c.read(authControllerProvider);
    await settle();
    expect(c.read(authControllerProvider).status, AuthStatus.guest);
    expect(repo.calls, isEmpty);
  });

  test('a stored valid token restores the signed-in profile', () async {
    store.token = validToken;
    final c = container();
    c.read(authControllerProvider);
    await settle();
    final auth = c.read(authControllerProvider);
    expect(auth.isSignedIn, isTrue);
    expect(auth.profile!.id, 7);
    expect(repo.calls, ['me:$validToken']);
  });

  test('a rejected stored token is cleared and the app falls back to guest',
      () async {
    store.token = 'hmp_revoked';
    final c = container();
    c.read(authControllerProvider);
    await settle();
    expect(c.read(authControllerProvider).status, AuthStatus.guest);
    expect(store.token, isNull);
  });

  test('a network error keeps the token for retry', () async {
    store.token = validToken;
    final failing = _OfflineRepository();
    final c = ProviderContainer(
      overrides: [
        userRepositoryProvider.overrideWithValue(failing),
        tokenStoreProvider.overrideWithValue(store),
      ],
    );
    addTearDown(c.dispose);
    c.read(authControllerProvider);
    await settle();
    expect(c.read(authControllerProvider).status, AuthStatus.guest);
    expect(c.read(authControllerProvider).error?.code, 'NETWORK_ERROR');
    expect(store.token, validToken);
  });

  test('dev sign-in persists the token; sign-out clears and revokes it',
      () async {
    final c = container();
    final auth = c.read(authControllerProvider.notifier);
    await settle();
    await auth.devSignIn('alice', displayName: 'alice');
    expect(store.token, 'hmp_alice');
    expect(c.read(authControllerProvider).isSignedIn, isTrue);

    await auth.signOut();
    expect(store.token, isNull);
    expect(c.read(authControllerProvider).status, AuthStatus.guest);
    expect(repo.calls, contains('logout:hmp_alice'));
  });

  test('a 401 on any protected call ends the local session', () async {
    store.token = validToken;
    final c = container();
    c.read(authControllerProvider);
    await settle();
    repo.tokens.clear(); // revoked server-side
    await expectLater(
      c
          .read(authControllerProvider.notifier)
          .setVehicle(VehicleType.largeHeavy),
      throwsA(isA<UserApiException>()),
    );
    expect(c.read(authControllerProvider).status, AuthStatus.guest);
    expect(store.token, isNull);
  });

  test('vehicle preference is saved through the profile API', () async {
    store.token = validToken;
    final c = container();
    c.read(authControllerProvider);
    await settle();
    await c
        .read(authControllerProvider.notifier)
        .setVehicle(VehicleType.normalHeavy);
    expect(repo.preferred, VehicleType.normalHeavy);
    expect(
      c.read(authControllerProvider).profile!.preferredVehicle,
      VehicleType.normalHeavy,
    );
  });

  test('stale vehicle responses never overwrite the latest choice', () async {
    store.token = validToken;
    final slow = _ControlledVehicleRepository();
    final c = ProviderContainer(
      overrides: [
        userRepositoryProvider.overrideWithValue(slow),
        tokenStoreProvider.overrideWithValue(store),
      ],
    );
    addTearDown(c.dispose);
    c.read(authControllerProvider);
    await settle();
    final auth = c.read(authControllerProvider.notifier);
    final first = auth.setVehicle(VehicleType.normalHeavy);
    final second = auth.setVehicle(VehicleType.largeHeavy);
    slow.complete(1, VehicleType.largeHeavy);
    await second;
    slow.complete(0, VehicleType.normalHeavy);
    await first;
    expect(
      c.read(authControllerProvider).profile!.preferredVehicle,
      VehicleType.largeHeavy,
    );
  });

  test('a response from a signed-out session is discarded', () async {
    store.token = validToken;
    final slow = _ControlledVehicleRepository();
    final c = ProviderContainer(
      overrides: [
        userRepositoryProvider.overrideWithValue(slow),
        tokenStoreProvider.overrideWithValue(store),
      ],
    );
    addTearDown(c.dispose);
    c.read(authControllerProvider);
    await settle();
    final auth = c.read(authControllerProvider.notifier);
    final pending = auth.setVehicle(VehicleType.normalHeavy);
    await auth.signOut();
    await auth.devSignIn('bob');
    slow.complete(0, VehicleType.normalHeavy);
    await pending;
    expect(c.read(authControllerProvider).profile!.preferredVehicle, isNull);
  });

  group('favorites', () {
    test('create, list and remove follow the signed-in user', () async {
      store.token = validToken;
      final c = container();
      c.read(favoritesControllerProvider);
      await settle();
      final favorites = c.read(favoritesControllerProvider.notifier);

      expect(await favorites.toggle(12), isTrue);
      expect(c.read(favoritesControllerProvider).contains(12), isTrue);
      expect(
        c.read(favoritesControllerProvider).items.single.name,
        'Lot 12',
      );
      expect(await favorites.toggle(12), isTrue);
      expect(c.read(favoritesControllerProvider).items, isEmpty);
      expect(repo.calls.where((c) => c.startsWith('add')), ['add:12']);
      expect(repo.calls.where((c) => c.startsWith('remove')), ['remove:12']);
    });

    test('guests cannot change favorites', () async {
      final c = container();
      c.read(favoritesControllerProvider);
      await settle();
      expect(
        await c.read(favoritesControllerProvider.notifier).toggle(12),
        isFalse,
      );
      expect(
        c.read(favoritesControllerProvider).error?.code,
        UserApiException.unauthenticated,
      );
      expect(repo.calls, isEmpty);
    });

    test('signing out clears loaded favorites', () async {
      store.token = validToken;
      repo.favoriteIds.add(3);
      final c = container();
      c.read(favoritesControllerProvider);
      await settle();
      await settle();
      expect(c.read(favoritesControllerProvider).items, hasLength(1));
      await c.read(authControllerProvider.notifier).signOut();
      await settle();
      expect(c.read(favoritesControllerProvider).items, isEmpty);
    });

    test('server errors keep the previous list and report failure', () async {
      store.token = validToken;
      repo.favoriteError = const UserApiException(
        code: 'NETWORK_ERROR',
        message: 'offline',
      );
      final c = container();
      c.read(favoritesControllerProvider);
      await settle();
      expect(
        await c.read(favoritesControllerProvider.notifier).toggle(5),
        isFalse,
      );
      final state = c.read(favoritesControllerProvider);
      expect(state.contains(5), isFalse);
      expect(state.pending, isEmpty);
      expect(state.error?.code, 'NETWORK_ERROR');
    });
  });
}

class _OfflineRepository extends FakeUserRepository {
  @override
  Future<Never> me(String token) async => throw const UserApiException(
        code: UserApiException.network,
        message: 'offline',
      );
}

class _ControlledVehicleRepository extends FakeUserRepository {
  final _pending = <Completer<UserProfile>>[];

  @override
  Future<UserProfile> setVehicle(String token, VehicleType? vehicle) {
    final completer = Completer<UserProfile>();
    _pending.add(completer);
    return completer.future;
  }

  void complete(int index, VehicleType vehicle) => _pending[index].complete(
        UserProfile(id: 7, role: 'USER', preferredVehicle: vehicle),
      );
}
