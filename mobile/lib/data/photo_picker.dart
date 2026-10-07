import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';

class PickedPhoto {
  const PickedPhoto(this.bytes, this.filename);
  final Uint8List bytes;
  final String filename;
}

class PhotoPickFailure implements Exception {
  const PhotoPickFailure(this.message);
  final String message;
}

abstract interface class PhotoPicker {
  /// Null when the rider cancels. Throws [PhotoPickFailure] when access is
  /// denied or the platform picker fails.
  Future<PickedPhoto?> pick({required bool camera});

  /// Android may destroy the activity while the camera/gallery is open; the
  /// result is then delivered to the next launch. Null when nothing was lost.
  Future<PickedPhoto?> recoverLost();
}

/// Requests JPEG output where the platform supports it. Some Android galleries
/// still return HEIC; the backend accepts HEIC and re-encodes every photo to a
/// metadata-free JPEG before storage.
class ImagePickerPhotoPicker implements PhotoPicker {
  ImagePickerPhotoPicker([ImagePicker? picker])
      : _picker = picker ?? ImagePicker();
  final ImagePicker _picker;

  @override
  Future<PickedPhoto?> pick({required bool camera}) async {
    try {
      final file = await _picker.pickImage(
        source: camera ? ImageSource.camera : ImageSource.gallery,
        maxWidth: 2048,
        maxHeight: 2048,
        imageQuality: 85,
        requestFullMetadata: false,
      );
      if (file == null) return null;
      return PickedPhoto(await file.readAsBytes(), file.name);
    } on PlatformException catch (e) {
      throw PhotoPickFailure(_message(e.code));
    } on Exception {
      throw const PhotoPickFailure('無法取得照片，請稍後再試。');
    }
  }

  @override
  Future<PickedPhoto?> recoverLost() async {
    if (defaultTargetPlatform != TargetPlatform.android) return null;
    try {
      final response = await _picker.retrieveLostData();
      final file = response.isEmpty ? null : response.file;
      if (file == null) return null;
      return PickedPhoto(await file.readAsBytes(), file.name);
    } on Exception {
      return null;
    }
  }

  static String _message(String code) => switch (code) {
        'camera_access_denied' => '相機權限已關閉，請至系統設定開啟。',
        'photo_access_denied' => '相簿權限已關閉，請至系統設定開啟。',
        _ => '無法取得照片，請稍後再試。',
      };
}

final photoPickerProvider =
    Provider<PhotoPicker>((ref) => ImagePickerPhotoPicker());

/// A photo recovered after Android recreated the app; the next report sheet
/// offers it so captured evidence is not silently lost.
final recoveredPhotoProvider = StateProvider<PickedPhoto?>((ref) => null);
