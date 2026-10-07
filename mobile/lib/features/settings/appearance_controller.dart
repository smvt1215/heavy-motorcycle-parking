import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Device-local appearance preference: follow the system, or force light/dark.
abstract interface class AppearanceStore {
  Future<ThemeMode?> load();

  Future<void> save(ThemeMode mode);
}

class SharedPreferencesAppearanceStore implements AppearanceStore {
  SharedPreferencesAppearanceStore([this._injected]);

  static const String key = 'appearance.theme_mode.v1';
  SharedPreferencesAsync? _injected;

  // Created on first use so a missing platform implementation surfaces as a
  // load/save failure (handled by the controller), not a provider crash.
  SharedPreferencesAsync get _preferences =>
      _injected ??= SharedPreferencesAsync();

  @override
  Future<ThemeMode?> load() async {
    final value = await _preferences.getString(key);
    return ThemeMode.values.where((mode) => mode.name == value).firstOrNull;
  }

  @override
  Future<void> save(ThemeMode mode) => _preferences.setString(key, mode.name);
}

final appearanceStoreProvider =
    Provider<AppearanceStore>((ref) => SharedPreferencesAppearanceStore());

final appearanceControllerProvider =
    StateNotifierProvider<AppearanceController, ThemeMode>(
  (ref) => AppearanceController(ref.watch(appearanceStoreProvider))..restore(),
);

class AppearanceController extends StateNotifier<ThemeMode> {
  AppearanceController(this._store) : super(ThemeMode.system);

  final AppearanceStore _store;
  bool _changed = false;

  Future<void> restore() async {
    try {
      final saved = await _store.load();
      // A choice made while loading wins over the stored one.
      if (mounted && !_changed && saved != null) state = saved;
    } catch (_) {
      // Storage unavailable: keep following the system appearance.
    }
  }

  Future<void> set(ThemeMode mode) async {
    _changed = true;
    state = mode;
    try {
      await _store.save(mode);
    } catch (_) {
      // The choice still applies for this session.
    }
  }
}

extension ThemeModeLabel on ThemeMode {
  String get label => switch (this) {
        ThemeMode.system => '跟隨系統',
        ThemeMode.light => '淺色',
        ThemeMode.dark => '深色',
      };
}
