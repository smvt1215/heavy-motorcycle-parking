import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';

import '../../domain/parking.dart';

/// Icons and silhouettes identify space type independently of semantic color.
IconData spaceIcon(SpaceType type) => switch (type) {
      SpaceType.heavyOnly => Icons.sports_motorsports,
      SpaceType.motoShared => Icons.two_wheeler,
      SpaceType.carShared => Icons.directions_car,
      SpaceType.lightMotoOnly => Icons.moped,
    };

class MarkerIcons {
  MarkerIcons._(this._icons);
  final Map<(SpaceType, bool), BitmapDescriptor> _icons;

  BitmapDescriptor forSpace(SpaceType type, {required bool unverified}) =>
      _icons[(type, unverified)]!;

  static Future<MarkerIcons> create() async {
    final icons = <(SpaceType, bool), BitmapDescriptor>{};
    for (final type in SpaceType.values) {
      for (final unknown in [false, true]) {
        icons[(type, unknown)] = await _draw(type, unknown);
      }
    }
    return MarkerIcons._(icons);
  }

  static Future<BitmapDescriptor> _draw(SpaceType type, bool unknown) async {
    const size = 112.0;
    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder);
    final shape = Path();
    if (unknown) {
      shape
        ..moveTo(56, 6)
        ..lineTo(106, 98)
        ..lineTo(6, 98)
        ..close();
    } else {
      switch (type) {
        case SpaceType.heavyOnly:
          shape.addOval(const Rect.fromLTWH(8, 8, 96, 96));
        case SpaceType.motoShared:
          shape
            ..moveTo(56, 4)
            ..lineTo(108, 56)
            ..lineTo(56, 108)
            ..lineTo(4, 56)
            ..close();
        case SpaceType.carShared:
          shape.addRRect(
            RRect.fromRectAndRadius(
              const Rect.fromLTWH(6, 16, 100, 80),
              const Radius.circular(14),
            ),
          );
        case SpaceType.lightMotoOnly:
          shape.addRRect(
            RRect.fromRectAndRadius(
              const Rect.fromLTWH(14, 6, 84, 100),
              const Radius.circular(10),
            ),
          );
      }
    }
    canvas.drawShadow(shape, Colors.black54, 4, true);
    canvas.drawPath(
      shape,
      Paint()
        ..color = unknown ? const Color(0xFF805719) : const Color(0xFF087D77),
    );
    canvas.drawPath(
      shape,
      Paint()
        ..color = Colors.white
        ..style = PaintingStyle.stroke
        ..strokeWidth = 4,
    );
    final glyph = spaceIcon(type);
    final painter = TextPainter(
      text: TextSpan(
        text: String.fromCharCode(glyph.codePoint),
        style: TextStyle(
          fontFamily: glyph.fontFamily,
          package: glyph.fontPackage,
          fontSize: 48,
          color: Colors.white,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    painter.paint(canvas, Offset((size - painter.width) / 2, 31));
    if (unknown) {
      final question = TextPainter(
        text: const TextSpan(
          text: '?',
          style: TextStyle(
            fontSize: 22,
            fontWeight: FontWeight.w800,
            color: Colors.white,
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      question.paint(canvas, const Offset(78, 74));
      question.dispose();
    }
    final picture = recorder.endRecording();
    final bitmap = await picture.toImage(size.toInt(), size.toInt());
    final bytes = await bitmap.toByteData(format: ui.ImageByteFormat.png);
    bitmap.dispose();
    picture.dispose();
    painter.dispose();
    return BitmapDescriptor.bytes(
      bytes!.buffer.asUint8List(),
      width: 44,
      height: 44,
    );
  }
}
