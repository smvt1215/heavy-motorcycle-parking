import 'package:flutter/material.dart';

import '../domain/parking.dart';

/// Taiwan's blue parking-sign color (the white "P" on blue). Used as the
/// Material seed and for the app's single brand mark.
const Color parkingSignBlue = Color(0xFF0B5CAD);

/// License-plate colors for the vehicle selector. They are always paired with
/// the 黃牌/紅牌 label, never used alone to convey the selection.
({Color background, Color foreground}) plateColors(VehicleType vehicle) =>
    switch (vehicle) {
      VehicleType.yellow => (
          background: const Color(0xFFF2B705),
          foreground: const Color(0xFF1A1A1A),
        ),
      VehicleType.red => (
          background: const Color(0xFFC8102E),
          foreground: Colors.white,
        ),
    };

/// Numbers that change in place (distance, spaces, prices) keep a fixed width.
const List<FontFeature> tabularFigures = [FontFeature.tabularFigures()];

/// The square "P" brand mark, scaled with text so it grows with Dynamic Type.
class ParkingMark extends StatelessWidget {
  const ParkingMark({super.key, this.size = 28});
  final double size;

  @override
  Widget build(BuildContext context) {
    final scaled =
        MediaQuery.textScalerOf(context).scale(size).clamp(size, size * 1.6);
    return ExcludeSemantics(
      child: Container(
        width: scaled,
        height: scaled,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: parkingSignBlue,
          borderRadius: BorderRadius.circular(scaled * 0.22),
        ),
        child: Text(
          'P',
          textScaler: TextScaler.noScaling,
          style: TextStyle(
            color: Colors.white,
            fontWeight: FontWeight.w900,
            fontSize: scaled * 0.62,
            height: 1,
          ),
        ),
      ),
    );
  }
}
