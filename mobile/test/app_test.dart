import 'package:flutter_test/flutter_test.dart';
import 'package:heavy_parking/app/app.dart';

void main() {
  testWidgets('App renders without crashing', (tester) async {
    await tester.pumpWidget(const HeavyParkingApp());
    expect(find.text('重機停車通'), findsOneWidget);
  });
}
