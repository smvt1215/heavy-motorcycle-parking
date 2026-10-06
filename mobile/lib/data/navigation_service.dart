import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../domain/parking.dart';

enum NavigationApp { googleMaps, appleMaps }

Uri navigationUri(NavigationTarget target, NavigationApp app) {
  final coordinates = '${target.location.lat},${target.location.lng}';
  return switch (app) {
    NavigationApp.googleMaps => Uri.https('www.google.com', '/maps/dir/', {
        'api': '1',
        'destination': coordinates,
        'travelmode': 'driving',
      }),
    NavigationApp.appleMaps => Uri.https('maps.apple.com', '/', {
        'daddr': coordinates,
        'dirflg': 'd',
      }),
  };
}

abstract interface class NavigationService {
  Future<bool> open(NavigationTarget target, NavigationApp app);
}

class ExternalNavigationService implements NavigationService {
  @override
  Future<bool> open(NavigationTarget target, NavigationApp app) => launchUrl(
        navigationUri(target, app),
        mode: LaunchMode.externalApplication,
      );
}

final navigationServiceProvider = Provider<NavigationService>(
  (ref) => ExternalNavigationService(),
);
