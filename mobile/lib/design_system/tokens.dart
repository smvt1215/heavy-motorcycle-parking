import 'package:flutter/material.dart';

import '../domain/parking.dart';

/// Taiwan's blue parking-sign color (the white "P" on blue). Used as the
/// Material seed and for the app's single brand mark.
const Color parkingSignBlue = Color(0xFF0B5CAD);

/// Class colors reinforce the 普重/大重 labels; 大重 includes both yellow and red plates.
({Color background, Color foreground}) plateColors(VehicleType vehicle) =>
    switch (vehicle) {
      VehicleType.normalHeavy => (
          background: Colors.white,
          foreground: const Color(0xFF1A1A1A),
        ),
      VehicleType.largeHeavy => (
          background: parkingSignBlue,
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
