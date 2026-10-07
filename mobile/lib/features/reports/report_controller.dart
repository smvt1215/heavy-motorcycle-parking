import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/photo_picker.dart';
import '../../data/user_repository.dart';
import '../../domain/user.dart';
import '../account/account_controller.dart';

enum ReportSubmitStatus { editing, submitting, submitted, failed }

class ReportFormState {
  const ReportFormState({
    this.status = ReportSubmitStatus.editing,
    this.report,
    this.photoFailed = false,
    this.error,
  });

  final ReportSubmitStatus status;
  final CommunityReport? report;

  /// The report was saved but its photo upload failed.
  final bool photoFailed;
  final UserApiException? error;
}

final reportControllerProvider =
    StateNotifierProvider.autoDispose<ReportController, ReportFormState>(
  (ref) => ReportController(
    ref.watch(userRepositoryProvider),
    ref.read(authControllerProvider.notifier),
  ),
);

class ReportController extends StateNotifier<ReportFormState> {
  ReportController(this._repository, this._auth)
      : super(const ReportFormState());

  final UserRepository _repository;
  final AuthController _auth;

  Future<void> submit({
    required int parkingId,
    required ReportType type,
    int? zoneId,
    String? description,
    PickedPhoto? photo,
  }) async {
    if (state.status == ReportSubmitStatus.submitting) return;
    state = const ReportFormState(status: ReportSubmitStatus.submitting);
    final CommunityReport report;
    try {
      report = await _auth.authorized(
        (token) => _repository.createReport(
          token,
          parkingId: parkingId,
          type: type,
          zoneId: zoneId,
          description: description,
        ),
      );
    } on UserApiException catch (e) {
      if (mounted) {
        state = ReportFormState(status: ReportSubmitStatus.failed, error: e);
      }
      return;
    }
    var photoFailed = false;
    UserApiException? photoError;
    if (photo != null) {
      try {
        await _auth.authorized(
          (token) => _repository.uploadPhoto(
            token,
            report.id,
            photo.bytes,
            photo.filename,
          ),
        );
      } on UserApiException catch (e) {
        photoFailed = true;
        photoError = e;
      }
    }
    if (mounted) {
      state = ReportFormState(
        status: ReportSubmitStatus.submitted,
        report: report,
        photoFailed: photoFailed,
        error: photoError,
      );
    }
  }
}

String userErrorMessage(UserApiException error) => switch (error.code) {
      UserApiException.unauthenticated => '登入已失效，請重新登入。',
      UserApiException.forbidden => '你沒有執行這個操作的權限。',
      UserApiException.network => '網路連線異常，請稍後再試。',
      'PHOTO_TOO_LARGE' => '照片太大，請選擇較小的照片。',
      'INVALID_PHOTO' => '照片格式不支援，請使用 JPEG、PNG 或 WebP。',
      'STORAGE_UNAVAILABLE' => '照片暫時無法上傳，回報內容已保存。',
      'PARKING_NOT_FOUND' => '找不到這個停車場。',
      _ => '操作失敗，請稍後再試。',
    };
