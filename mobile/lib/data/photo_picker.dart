import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';

class PickedPhoto {
  const PickedPhoto(this.bytes, this.filename);
  final Uint8List bytes;
  final String filename;
}

abstract interface class PhotoPicker {
  /// Null when the rider cancels.
  Future<PickedPhoto?> pick({required bool camera});
}

/// Requests JPEG output so HEIC camera photos are accepted by the backend,
/// which re-encodes and strips metadata again before storage.
class ImagePickerPhotoPicker implements PhotoPicker {
  ImagePickerPhotoPicker([ImagePicker? picker])
      : _picker = picker ?? ImagePicker();
  final ImagePicker _picker;

  @override
  Future<PickedPhoto?> pick({required bool camera}) async {
    final file = await _picker.pickImage(
      source: camera ? ImageSource.camera : ImageSource.gallery,
      maxWidth: 2048,
      maxHeight: 2048,
      imageQuality: 85,
      requestFullMetadata: false,
    );
    if (file == null) return null;
    return PickedPhoto(await file.readAsBytes(), file.name);
  }
}

final photoPickerProvider =
    Provider<PhotoPicker>((ref) => ImagePickerPhotoPicker());
