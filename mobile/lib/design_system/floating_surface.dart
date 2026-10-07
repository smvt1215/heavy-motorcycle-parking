import 'dart:ui';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

/// Container for controls floating over the map (search, vehicle, filters).
///
/// On iOS it uses a translucent, blurred material in the spirit of Liquid
/// Glass, which Apple reserves for navigation/search/floating controls rather
/// than content. Increase Contrast or Reduce Motion fall back to an opaque
/// surface, and Android always uses an opaque Material 3 surface with elevation.
class FloatingSurface extends StatelessWidget {
  const FloatingSurface({
    super.key,
    required this.child,
    this.radius = 24,
    this.elevation = 3,
  });

  final Widget child;
  final double radius;
  final double elevation;

  static bool translucent(BuildContext context) {
    final media = MediaQuery.of(context);
    return defaultTargetPlatform == TargetPlatform.iOS &&
        !media.highContrast &&
        !media.disableAnimations;
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final shape = BorderRadius.circular(radius);
    if (!translucent(context)) {
      return Material(
        elevation: elevation,
        color: scheme.surface,
        borderRadius: shape,
        child: child,
      );
    }
    return ClipRRect(
      borderRadius: shape,
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 24, sigmaY: 24),
        child: Material(
          // High enough opacity that text keeps WCAG contrast over the map.
          color: scheme.surface.withValues(alpha: 0.86),
          shape: RoundedRectangleBorder(
            borderRadius: shape,
            side:
                BorderSide(color: scheme.outlineVariant.withValues(alpha: 0.6)),
          ),
          child: child,
        ),
      ),
    );
  }
}
