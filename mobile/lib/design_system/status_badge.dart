import 'package:flutter/material.dart';

import '../domain/parking.dart';

/// Compatibility shown by shape + icon + text, with color only reinforcing it:
/// confirmed = filled pill with a check, unverified = outlined pill with a
/// question mark, not allowed = outlined pill with a block icon.
class StatusBadge extends StatelessWidget {
  const StatusBadge({super.key, required this.status, required this.label});

  final CompatibilityStatus status;
  final String label;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final highContrast = MediaQuery.highContrastOf(context);
    final (IconData icon, Color fill, Color ink, Color? border) =
        switch (status) {
      CompatibilityStatus.allowed => (
          Icons.verified,
          scheme.primaryContainer,
          scheme.onPrimaryContainer,
          highContrast ? scheme.onPrimaryContainer : null,
        ),
      CompatibilityStatus.unknown => (
          Icons.help_outline,
          Colors.transparent,
          scheme.onSurface,
          scheme.outline,
        ),
      CompatibilityStatus.notAllowed => (
          Icons.block,
          Colors.transparent,
          scheme.error,
          scheme.error,
        ),
    };
    return Semantics(
      label: label,
      excludeSemantics: true,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: fill,
          borderRadius: BorderRadius.circular(999),
          border: border == null
              ? null
              : Border.all(color: border, width: highContrast ? 2 : 1.2),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 16, color: ink),
            const SizedBox(width: 4),
            Flexible(
              child: Text(
                label,
                style: Theme.of(context)
                    .textTheme
                    .labelLarge
                    ?.copyWith(color: ink, fontWeight: FontWeight.w700),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
