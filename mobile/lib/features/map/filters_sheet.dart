import 'package:flutter/material.dart';

import '../../domain/parking.dart';

class FiltersSheet extends StatefulWidget {
  const FiltersSheet({super.key, required this.query});
  final ParkingQuery query;

  @override
  State<FiltersSheet> createState() => _FiltersSheetState();
}

class _FiltersSheetState extends State<FiltersSheet> {
  late int _radius = widget.query.radius;
  late SpaceType? _space = widget.query.spaceType;
  late bool _unknown = widget.query.includeUnknown;
  late bool _cap = widget.query.dailyMaxRequired;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('選擇停車條件', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 24),
            DropdownButtonFormField<SpaceType?>(
              initialValue: _space,
              decoration: const InputDecoration(labelText: '可停放車格'),
              items: [
                const DropdownMenuItem(value: null, child: Text('所有相容車格')),
                for (final type in SpaceType.forVehicle(widget.query.vehicle))
                  DropdownMenuItem(value: type, child: Text(type.label)),
              ],
              onChanged: (value) => setState(() => _space = value),
            ),
            const SizedBox(height: 20),
            Text('搜尋半徑', style: Theme.of(context).textTheme.titleSmall),
            Wrap(
              spacing: 8,
              children: [
                for (final radius in [500, 1000, 3000, 5000])
                  ChoiceChip(
                    label:
                        Text(radius < 1000 ? '500 公尺' : '${radius ~/ 1000} 公里'),
                    selected: _radius == radius,
                    onSelected: (_) => setState(() => _radius = radius),
                  ),
              ],
            ),
            SwitchListTile.adaptive(
              contentPadding: EdgeInsets.zero,
              title: const Text('需有已確認每日上限'),
              value: _cap,
              onChanged: (value) => setState(() => _cap = value),
            ),
            SwitchListTile.adaptive(
              contentPadding: EdgeInsets.zero,
              title: const Text('顯示未確認停車位置'),
              subtitle: const Text('標示「尚未確認」，請先確認現場停車規定。'),
              value: _unknown,
              onChanged: (value) => setState(() => _unknown = value),
            ),
            const SizedBox(height: 20),
            FilledButton(
              onPressed: () => Navigator.pop(
                context,
                widget.query.copyWith(
                  radius: _radius,
                  spaceType: _space,
                  dailyMaxRequired: _cap,
                  includeUnknown: _unknown,
                ),
              ),
              child: const Text('套用條件'),
            ),
          ],
        ),
      ),
    );
  }
}
