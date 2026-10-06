import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/data/map_configuration.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('tw.heavyparking/config');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

  setUp(() => debugDefaultTargetPlatformOverride = TargetPlatform.android);
  tearDown(() {
    debugDefaultTargetPlatformOverride = null;
    messenger.setMockMethodCallHandler(channel, null);
  });

  for (final platform in [TargetPlatform.android, TargetPlatform.iOS]) {
    test('$platform checks native SDK initialization before displaying a map',
        () async {
      debugDefaultTargetPlatformOverride = platform;
      final calls = <String>[];
      messenger.setMockMethodCallHandler(channel, (call) async {
        calls.add(call.method);
        return true;
      });
      final container = ProviderContainer();
      addTearDown(container.dispose);
      expect(await container.read(mapConfigurationProvider.future), isTrue);
      expect(calls, ['mapsConfigured']);
    });
  }

  test('missing key keeps native map creation disabled', () async {
    messenger.setMockMethodCallHandler(channel, (_) async => false);
    final container = ProviderContainer();
    addTearDown(container.dispose);
    expect(await container.read(mapConfigurationProvider.future), isFalse);
  });

  test('missing native configuration channel degrades safely', () async {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    expect(await container.read(mapConfigurationProvider.future), isFalse);
  });

  test('native configuration failure degrades safely', () async {
    messenger.setMockMethodCallHandler(channel, (_) async {
      throw PlatformException(code: 'UNAVAILABLE');
    });
    final container = ProviderContainer();
    addTearDown(container.dispose);
    expect(await container.read(mapConfigurationProvider.future), isFalse);
  });
}
