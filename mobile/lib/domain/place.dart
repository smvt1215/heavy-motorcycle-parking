import 'parking.dart';

/// Google Places geocoding results, proxied by our backend.
///
/// These types only describe *where* a destination is. They never carry
/// parking legality, rates or availability; those come from `/parking/*`.

/// Google-licensed coordinates may be cached for at most 30 days. Older recent
/// searches keep their place ID and label and are re-resolved on selection.
const Duration placeLocationCacheLimit = Duration(days: 30);

final RegExp _placeIdPattern = RegExp(r'^[A-Za-z0-9_-]{1,512}$');

String _placeId(Object? value) {
  if (value is! String || !_placeIdPattern.hasMatch(value)) {
    throw const FormatException('Invalid place_id');
  }
  return value;
}

String _text(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('Missing $key');
  }
  return value.trim();
}

String? _optionalText(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value == null) return null;
  if (value is! String) throw FormatException('Invalid $key');
  return value.trim().isEmpty ? null : value.trim();
}

class PlaceSuggestion {
  const PlaceSuggestion({
    required this.placeId,
    required this.primaryText,
    this.secondaryText,
  });

  factory PlaceSuggestion.fromJson(Map<String, dynamic> json) =>
      PlaceSuggestion(
        placeId: _placeId(json['place_id']),
        primaryText: _text(json, 'primary_text'),
        secondaryText: _optionalText(json, 'secondary_text'),
      );

  final String placeId;
  final String primaryText;
  final String? secondaryText;
}

class PlaceDestination {
  const PlaceDestination({
    required this.placeId,
    required this.name,
    required this.location,
    this.address,
  });

  factory PlaceDestination.fromJson(Map<String, dynamic> json) {
    final location = json['location'];
    if (location is! Map) throw const FormatException('Missing location');
    final point = GeoPoint.fromJson(Map<String, dynamic>.from(location));
    if (!point.isValid) throw const FormatException('Invalid location');
    return PlaceDestination(
      placeId: _placeId(json['place_id']),
      name: _text(json, 'name'),
      address: _optionalText(json, 'address'),
      location: point,
    );
  }

  final String placeId;
  final String name;
  final String? address;
  final GeoPoint location;
}

/// Locally stored destination history entry.
class RecentDestination {
  const RecentDestination({
    required this.placeId,
    required this.name,
    required this.searchedAt,
    this.address,
    this.location,
    this.resolvedAt,
  });

  factory RecentDestination.fromDestination(
    PlaceDestination destination,
    DateTime now,
  ) =>
      RecentDestination(
        placeId: destination.placeId,
        name: destination.name,
        address: destination.address,
        location: destination.location,
        resolvedAt: now,
        searchedAt: now,
      );

  factory RecentDestination.fromJson(Map<String, dynamic> json) {
    final lat = json['lat'];
    final lng = json['lng'];
    final resolvedAt = json['resolved_at'];
    GeoPoint? location;
    if (lat is num && lng is num && resolvedAt is String) {
      final point = GeoPoint(lat.toDouble(), lng.toDouble());
      if (point.isValid) location = point;
    }
    final searchedAt = json['searched_at'];
    if (searchedAt is! String) throw const FormatException('searched_at');
    return RecentDestination(
      placeId: _placeId(json['place_id']),
      name: _text(json, 'name'),
      address: _optionalText(json, 'address'),
      location: location,
      resolvedAt:
          location == null ? null : DateTime.parse(resolvedAt as String),
      searchedAt: DateTime.parse(searchedAt),
    );
  }

  final String placeId;
  final String name;
  final String? address;

  /// Cached coordinates; null when expired or never resolved.
  final GeoPoint? location;
  final DateTime? resolvedAt;
  final DateTime searchedAt;

  /// Usable coordinates at [now]; null means re-resolve through the backend.
  GeoPoint? locationAt(DateTime now) {
    final resolved = resolvedAt;
    if (location == null || resolved == null) return null;
    final age = now.difference(resolved);
    if (age.isNegative || age >= placeLocationCacheLimit) return null;
    return location;
  }

  /// Drops cached coordinates that exceed the cache limit at [now].
  RecentDestination expiredAt(DateTime now) => locationAt(now) != null
      ? this
      : RecentDestination(
          placeId: placeId,
          name: name,
          address: address,
          searchedAt: searchedAt,
        );

  PlaceDestination? destinationAt(DateTime now) {
    final point = locationAt(now);
    return point == null
        ? null
        : PlaceDestination(
            placeId: placeId,
            name: name,
            address: address,
            location: point,
          );
  }

  RecentDestination touched(DateTime now) => RecentDestination(
        placeId: placeId,
        name: name,
        address: address,
        location: location,
        resolvedAt: resolvedAt,
        searchedAt: now,
      );

  Map<String, dynamic> toJson() => {
        'place_id': placeId,
        'name': name,
        if (address != null) 'address': address,
        if (location != null && resolvedAt != null) ...{
          'lat': location!.lat,
          'lng': location!.lng,
          'resolved_at': resolvedAt!.toUtc().toIso8601String(),
        },
        'searched_at': searchedAt.toUtc().toIso8601String(),
      };
}
