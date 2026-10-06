class AppConstants {
  AppConstants._();
  static const String appName = '重機停車通';
  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000/api/v1',
  );
}
