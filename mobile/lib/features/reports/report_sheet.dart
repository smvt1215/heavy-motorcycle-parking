import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/photo_picker.dart';
import '../../domain/parking.dart';
import '../../domain/user.dart';
import 'report_controller.dart';

/// Community report form. Reports are reviewed separately and never change
/// the official parking information shown in the app.
class ReportSheet extends ConsumerStatefulWidget {
  const ReportSheet({super.key, required this.lot, this.zones = const []});
  final ParkingLot lot;
  final List<ParkingZone> zones;

  @override
  ConsumerState<ReportSheet> createState() => _ReportSheetState();
}

class _ReportSheetState extends ConsumerState<ReportSheet> {
  ReportType _type = ReportType.parkingAllowed;
  int? _zoneId;
  PickedPhoto? _photo;
  final _description = TextEditingController();

  String? _pickError;

  @override
  void initState() {
    super.initState();
    final recovered = ref.read(recoveredPhotoProvider);
    if (recovered != null) {
      _photo = recovered;
      // Consume it once; it belongs to this new report draft now.
      WidgetsBinding.instance.addPostFrameCallback(
        (_) => ref.read(recoveredPhotoProvider.notifier).state = null,
      );
    }
  }

  @override
  void dispose() {
    _description.dispose();
    super.dispose();
  }

  Future<void> _pick(bool camera) async {
    setState(() => _pickError = null);
    try {
      final photo = await ref.read(photoPickerProvider).pick(camera: camera);
      if (photo != null && mounted) setState(() => _photo = photo);
    } on PhotoPickFailure catch (e) {
      if (mounted) setState(() => _pickError = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(reportControllerProvider);
    final submitting = state.status == ReportSubmitStatus.submitting;
    if (state.status == ReportSubmitStatus.submitted) {
      return _Submitted(state: state);
    }
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.fromLTRB(
          20,
          0,
          20,
          20 + MediaQuery.viewInsetsOf(context).bottom,
        ),
        child: ListView(
          shrinkWrap: true,
          children: [
            Text(
              '回報「${widget.lot.name}」',
              style: Theme.of(context)
                  .textTheme
                  .titleLarge
                  ?.copyWith(fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 4),
            const Text('回報會由人工審核，不會直接改變官方停車資料。'),
            const SizedBox(height: 16),
            DropdownButtonFormField<ReportType>(
              initialValue: _type,
              decoration: const InputDecoration(labelText: '回報類型'),
              items: [
                for (final type in ReportType.values)
                  DropdownMenuItem(value: type, child: Text(type.label)),
              ],
              onChanged: submitting
                  ? null
                  : (value) => setState(() => _type = value ?? _type),
            ),
            if (widget.zones.isNotEmpty)
              DropdownButtonFormField<int?>(
                initialValue: _zoneId,
                decoration: const InputDecoration(labelText: '停車區（選填）'),
                items: [
                  const DropdownMenuItem(value: null, child: Text('整個停車場')),
                  for (final zone in widget.zones)
                    DropdownMenuItem(
                      value: zone.zoneId,
                      child: Text(zone.name ?? zone.spaceType.label),
                    ),
                ],
                onChanged: submitting
                    ? null
                    : (value) => setState(() => _zoneId = value),
              ),
            TextField(
              controller: _description,
              enabled: !submitting,
              maxLength: 1000,
              maxLines: 3,
              decoration: const InputDecoration(
                labelText: '說明（選填）',
                hintText: '例如：B2 有重機專用格，入口在民生東路側',
              ),
            ),
            Row(
              children: [
                Expanded(
                  child: Text(
                    _photo == null
                        ? '可附一張照片佐證（選填）'
                        : '已選擇照片：${_photo!.filename}',
                  ),
                ),
                IconButton(
                  tooltip: '拍照',
                  onPressed: submitting ? null : () => _pick(true),
                  icon: const Icon(Icons.photo_camera_outlined),
                ),
                IconButton(
                  tooltip: '從相簿選擇',
                  onPressed: submitting ? null : () => _pick(false),
                  icon: const Icon(Icons.photo_library_outlined),
                ),
                if (_photo != null)
                  IconButton(
                    tooltip: '移除照片',
                    onPressed:
                        submitting ? null : () => setState(() => _photo = null),
                    icon: const Icon(Icons.close),
                  ),
              ],
            ),
            const Text('照片上傳時會移除拍攝位置等中繼資料。'),
            if (_pickError case final message?)
              Text(
                message,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            if (state.error case final error?) ...[
              const SizedBox(height: 8),
              Text(
                userErrorMessage(error),
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
            const SizedBox(height: 16),
            FilledButton(
              onPressed: submitting
                  ? null
                  : () => ref.read(reportControllerProvider.notifier).submit(
                        parkingId: widget.lot.id,
                        type: _type,
                        zoneId: _zoneId,
                        description: _description.text,
                        photo: _photo,
                      ),
              child: Text(submitting ? '送出中…' : '送出回報'),
            ),
          ],
        ),
      ),
    );
  }
}

class _Submitted extends StatelessWidget {
  const _Submitted({required this.state});
  final ReportFormState state;

  @override
  Widget build(BuildContext context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Icon(Icons.check_circle_outline, size: 40),
              const SizedBox(height: 8),
              const Text('感謝回報！審核前不會影響官方資料。', textAlign: TextAlign.center),
              if (state.photoFailed) ...[
                const SizedBox(height: 8),
                Text(
                  state.error == null
                      ? '照片上傳失敗，回報內容已保存。'
                      : userErrorMessage(state.error!),
                  textAlign: TextAlign.center,
                ),
              ],
              const SizedBox(height: 16),
              FilledButton(
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('完成'),
              ),
            ],
          ),
        ),
      );
}
