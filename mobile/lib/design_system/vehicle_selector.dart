import 'package:flutter/cupertino.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../domain/parking.dart';
import 'theme.dart';
import 'tokens.dart';

/// YELLOW/RED plate selector. iOS uses the native sliding segmented control;
/// Android uses a Material 3 segmented button. Each option shows a plate-colored
/// swatch *and* its label, and the selected state is announced by semantics.
class VehicleSelector extends StatelessWidget {
  const VehicleSelector({
    super.key,
    required this.selected,
    required this.onChanged,
  });

  final VehicleType selected;
  final ValueChanged<VehicleType> onChanged;

  @override
  Widget build(BuildContext context) {
    if (defaultTargetPlatform == TargetPlatform.iOS) {
      return ConstrainedBox(
        constraints: const BoxConstraints(minHeight: minTouchTarget),
        child: CupertinoSlidingSegmentedControl<VehicleType>(
          groupValue: selected,
          onValueChanged: (value) {
            if (value != null) onChanged(value);
          },
          children: {
            for (final vehicle in VehicleType.values)
              vehicle: _Label(vehicle: vehicle, selected: vehicle == selected),
          },
        ),
      );
    }
    return SegmentedButton<VehicleType>(
      showSelectedIcon: false,
      segments: [
        for (final vehicle in VehicleType.values)
          ButtonSegment(
            value: vehicle,
            label: _Label(vehicle: vehicle, selected: vehicle == selected),
          ),
      ],
      selected: {selected},
      onSelectionChanged: (selection) => onChanged(selection.single),
    );
  }
}

class _Label extends StatelessWidget {
  const _Label({required this.vehicle, required this.selected});
  final VehicleType vehicle;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final plate = plateColors(vehicle);
    return Tooltip(
      message: vehicle.formalName,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 10),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            // A tiny plate: color reinforces the label, never replaces it.
            Container(
              width: 14,
              height: 10,
              decoration: BoxDecoration(
                color: plate.background,
                borderRadius: BorderRadius.circular(2),
                border:
                    Border.all(color: plate.foreground.withValues(alpha: 0.5)),
              ),
            ),
            const SizedBox(width: 6),
            Text(
              vehicle.label,
              semanticsLabel: '${vehicle.label}，${vehicle.formalName}',
              style: TextStyle(
                fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
