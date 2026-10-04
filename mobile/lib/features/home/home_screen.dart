import 'package:flutter/material.dart';

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('重機停車通'),
      ),
      body: const Center(
        child: Text('Heavy Motorcycle Parking'),
      ),
    );
  }
}
