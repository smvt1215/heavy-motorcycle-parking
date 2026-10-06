import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'router.dart';

class HeavyParkingApp extends StatelessWidget {
  const HeavyParkingApp({super.key, this.router});
  final GoRouter? router;

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: '重機停車通',
      theme: ThemeData(
        colorSchemeSeed: const Color(0xFF087D77),
        useMaterial3: true,
        brightness: Brightness.light,
        scaffoldBackgroundColor: const Color(0xFFF4F7FA),
        filledButtonTheme: FilledButtonThemeData(
          style: FilledButton.styleFrom(minimumSize: const Size(48, 48)),
        ),
      ),
      darkTheme: ThemeData(
        colorSchemeSeed: const Color(0xFF087D77),
        useMaterial3: true,
        brightness: Brightness.dark,
        filledButtonTheme: FilledButtonThemeData(
          style: FilledButton.styleFrom(minimumSize: const Size(48, 48)),
        ),
      ),
      routerConfig: router ?? appRouter,
      debugShowCheckedModeBanner: false,
    );
  }
}
