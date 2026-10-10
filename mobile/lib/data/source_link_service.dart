import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../domain/parking.dart';

Uri? sourceUri(ParkingSource source) {
  final uri = Uri.tryParse(source.sourceUrl ?? '');
  if (uri == null ||
      uri.scheme != 'https' ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty) {
    return null;
  }
  return uri;
}

abstract interface class SourceLinkService {
  Future<bool> open(Uri uri);
}

class ExternalSourceLinkService implements SourceLinkService {
  @override
  Future<bool> open(Uri uri) async {
    try {
      return await launchUrl(uri, mode: LaunchMode.externalApplication);
    } catch (_) {
      return false;
    }
  }
}

final sourceLinkServiceProvider = Provider<SourceLinkService>(
  (ref) => ExternalSourceLinkService(),
);
