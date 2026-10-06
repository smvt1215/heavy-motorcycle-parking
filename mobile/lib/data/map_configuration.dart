import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Check native initialization before creating a GoogleMap. In particular,
/// iOS raises a native exception if the SDK was never given its key.
final mapConfigurationProvider = FutureProvider<bool>((ref) async {
  if (kIsWeb ||
      (defaultTargetPlatform != TargetPlatform.android &&
          defaultTargetPlatform != TargetPlatform.iOS)) {
    return false;
  }
  try {
    return await const MethodChannel('tw.heavyparking/config')
            .invokeMethod<bool>('mapsConfigured') ??
        false;
  } on PlatformException {
    return false;
  } on MissingPluginException {
    return false;
  }
});
