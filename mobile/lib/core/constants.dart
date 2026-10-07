import 'package:flutter/foundation.dart';

class AppConstants {
  AppConstants._();
  static const String appName = '重機停車通';
  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000/api/v1',
  );

  /// Shows the development sign-in that calls the DEV-only backend session
  /// endpoint. Production builds need an identity-provider sign-in instead.
  static const bool devSignIn =
      bool.fromEnvironment('DEV_SIGN_IN', defaultValue: kDebugMode);
}
