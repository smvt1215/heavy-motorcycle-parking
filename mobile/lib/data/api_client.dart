import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/constants.dart';

const Duration apiTimeout = Duration(seconds: 15);

/// Shared HTTP client for our own `/api/v1` backend only.
final apiDioProvider = Provider<Dio>((ref) {
  final dio = Dio(
    BaseOptions(
      baseUrl: AppConstants.apiBaseUrl,
      connectTimeout: apiTimeout,
      receiveTimeout: apiTimeout,
    ),
  );
  ref.onDispose(dio.close);
  return dio;
});
