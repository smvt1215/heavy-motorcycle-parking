import 'package:flutter/material.dart';
import 'router.dart';

class HeavyParkingApp extends StatelessWidget {
  const HeavyParkingApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: '重機停車通',
      theme: ThemeData(
        colorSchemeSeed: Colors.orange,
        useMaterial3: true,
        brightness: Brightness.light,
      ),
      darkTheme: ThemeData(
        colorSchemeSeed: Colors.orange,
        useMaterial3: true,
        brightness: Brightness.dark,
      ),
      routerConfig: appRouter,
      debugShowCheckedModeBanner: false,
    );
  }
}
