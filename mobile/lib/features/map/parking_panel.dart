import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/navigation_service.dart';
import '../../domain/parking.dart';
import 'map_controller.dart';
import 'marker_icons.dart';
import 'parking_presentation.dart';

class ParkingPanel extends ConsumerWidget {
  const ParkingPanel({super.key, required this.state});
  final MapState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final controller = ref.read(mapControllerProvider.notifier);
    final lot = state.selectedLot;
    return DraggableScrollableSheet(
      key: ValueKey(lot?.id),
      initialChildSize: lot == null ? 0.26 : 0.52,
      minChildSize: 0.18,
      maxChildSize: 0.88,
      builder: (context, scroll) => Material(
        elevation: 8,
        color: Theme.of(context).colorScheme.surface,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
        clipBehavior: Clip.antiAlias,
        child: ListView(
          controller: scroll,
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
          children: [
            Center(
              child: Container(
                width: 36,
                height: 4,
                margin: const EdgeInsets.only(bottom: 16),
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.outlineVariant,
                  borderRadius: BorderRadius.circular(4),
                ),
              ),
            ),
            if (lot == null) ...[
              Text(
                '附近停車',
                style: Theme.of(context)
                    .textTheme
                    .headlineSmall
                    ?.copyWith(fontWeight: FontWeight.w800),
              ),
              const SizedBox(height: 12),
              const _Legend(),
              const SizedBox(height: 12),
              if (state.isLoading) ...[
                const LinearProgressIndicator(),
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 16),
                  child: Text('正在查詢相容停車區…'),
                ),
              ] else if (state.error != null) ...[
                Text(
                  state.error!.isCursorError
                      ? '查詢已失效，請重新搜尋此區域。'
                      : '無法取得停車資料，請檢查網路後重試。',
                ),
                TextButton.icon(
                  onPressed: controller.search,
                  icon: const Icon(Icons.refresh),
                  label: const Text('重新搜尋'),
                ),
              ] else if (state.items.isEmpty) ...[
                const Text('這個區域沒有符合條件的停車場。'),
                const SizedBox(height: 4),
                const Text('試著移動地圖或調整搜尋條件。'),
              ],
              for (final item in state.items)
                _LotRow(lot: item, onTap: () => controller.selectLot(item)),
              if (state.hasMore)
                TextButton.icon(
                  onPressed: state.isLoadingMore ? null : controller.loadMore,
                  icon: state.isLoadingMore
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.expand_more),
                  label: const Text('載入更多停車場'),
                ),
            ] else ...[
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      lot.name,
                      style: Theme.of(context)
                          .textTheme
                          .headlineSmall
                          ?.copyWith(fontWeight: FontWeight.w800),
                    ),
                  ),
                  IconButton(
                    tooltip: '返回停車列表',
                    onPressed: controller.closeSelection,
                    icon: const Icon(Icons.close),
                  ),
                ],
              ),
              Text(
                '${distanceLabel(lot.distanceM)} · ${state.query.vehicle.label}',
              ),
              const SizedBox(height: 12),
              _Compatibility(status: lot.compatibility),
              const SizedBox(height: 12),
              Text('停車場整體：${summaryLabel(lot.availabilitySummary)}'),
              Text(summaryFreshness(lot.availabilitySummary)),
              for (final source
                  in lot.availabilitySummary?.contributingSources ??
                      <ParkingSource>[])
                _SourceLine(title: '整體空位來源', source: source),
              if (state.detailLoading)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 16),
                  child: LinearProgressIndicator(),
                ),
              if (state.detailError != null) ...[
                const Text('入口及詳細資料載入失敗。'),
                TextButton(
                  onPressed: () => controller.selectLot(lot),
                  child: const Text('重新載入詳細資料'),
                ),
              ],
              const Divider(height: 28),
              Text('符合條件的停車區', style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 8),
              for (final zone in lot.zones) _ZoneFacts(zone: zone),
              if (state.detail case final detail?) ...[
                const Divider(height: 28),
                Text('入口與導航', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 12),
                for (final entrance in detail.entrances)
                  _EntranceFacts(entrance: entrance),
                if (detail.entrances.isEmpty) const Text('未提供入口資料。'),
                const SizedBox(height: 12),
                Text(
                  detail.navigationTarget.isConfirmedEntrance
                      ? '導航至已確認重機入口：${detail.navigationTarget.label}'
                      : '導航至停車場中心位置；尚無已確認可通行的重機入口。',
                ),
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: () => _navigate(
                    context,
                    ref,
                    detail.navigationTarget,
                    NavigationApp.googleMaps,
                  ),
                  icon: const Icon(Icons.navigation_outlined),
                  label: Text(
                    detail.navigationTarget.isConfirmedEntrance
                        ? 'Google Maps 前往入口'
                        : 'Google Maps 前往場址',
                  ),
                ),
                if (Theme.of(context).platform == TargetPlatform.iOS)
                  OutlinedButton.icon(
                    onPressed: () => _navigate(
                      context,
                      ref,
                      detail.navigationTarget,
                      NavigationApp.appleMaps,
                    ),
                    icon: const Icon(Icons.map_outlined),
                    label: const Text('Apple Maps 開啟導航'),
                  ),
                const SizedBox(height: 12),
                Text(
                  '規則／費率評估時間：${taipeiTime(detail.evaluationAt)}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _navigate(
    BuildContext context,
    WidgetRef ref,
    NavigationTarget target,
    NavigationApp app,
  ) async {
    try {
      final opened =
          await ref.read(navigationServiceProvider).open(target, app);
      if (!opened && context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('無法開啟地圖，請稍後再試。')));
      }
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('無法開啟導航。')));
      }
    }
  }
}

class _LotRow extends StatelessWidget {
  const _LotRow({required this.lot, required this.onTap});
  final ParkingLot lot;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => ListTile(
        contentPadding: EdgeInsets.zero,
        leading: Icon(
          lot.isUnverified ? Icons.help_outline : spaceIcon(markerSpace(lot)),
        ),
        title:
            Text(lot.name, style: const TextStyle(fontWeight: FontWeight.w700)),
        subtitle: Text(
          '${markerDescription(lot)} · ${distanceLabel(lot.distanceM)}\n${summaryLabel(lot.availabilitySummary)} · ${summaryFreshness(lot.availabilitySummary)}',
        ),
        isThreeLine: true,
        trailing: const Icon(Icons.chevron_right),
        onTap: onTap,
      );
}

class _Legend extends StatelessWidget {
  const _Legend();

  @override
  Widget build(BuildContext context) => Wrap(
        spacing: 12,
        runSpacing: 6,
        children: [
          for (final type in SpaceType.searchable)
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(spaceIcon(type), size: 16),
                const SizedBox(width: 4),
                Text(type.label, style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.help_outline, size: 16),
              const SizedBox(width: 4),
              Text('尚未確認', style: Theme.of(context).textTheme.bodySmall),
            ],
          ),
        ],
      );
}

class _Compatibility extends StatelessWidget {
  const _Compatibility({required this.status, this.entrance = false});
  final CompatibilityStatus status;
  final bool entrance;

  @override
  Widget build(BuildContext context) {
    final allowed = status == CompatibilityStatus.allowed;
    final unknown = status == CompatibilityStatus.unknown;
    final label = entrance
        ? switch (status) {
            CompatibilityStatus.allowed => '入口可通行',
            CompatibilityStatus.notAllowed => '入口不可通行',
            CompatibilityStatus.unknown => '入口通行尚未確認',
          }
        : unknown
            ? '尚未確認'
            : status.label;
    final color = allowed
        ? Theme.of(context).colorScheme.primary
        : Theme.of(context).colorScheme.error;
    return Semantics(
      label: label,
      child: Row(
        children: [
          Icon(
            allowed
                ? Icons.verified_outlined
                : unknown
                    ? Icons.help_outline
                    : Icons.block,
            color: color,
            size: 20,
          ),
          const SizedBox(width: 6),
          Flexible(
            child: Text(
              label,
              style: TextStyle(color: color, fontWeight: FontWeight.w700),
            ),
          ),
        ],
      ),
    );
  }
}

class _ZoneFacts extends StatelessWidget {
  const _ZoneFacts({required this.zone});
  final ParkingZone zone;

  @override
  Widget build(BuildContext context) {
    final rate = zone.rateSummary;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(spaceIcon(zone.spaceType), size: 24),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '${zone.name ?? zone.spaceType.label} · ${zone.spaceType.label}',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          _Compatibility(status: zone.compatibility.status),
          if (zone.compatibility.reason != null)
            Text(
              zone.compatibility.reason!,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          Text(zoneAvailabilityLabel(zone)),
          Text(
            zoneFreshnessLabel(zone),
            style: Theme.of(context).textTheme.bodySmall,
          ),
          Text(
            rateLabel(rate),
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
          if (rate?.dailyMaxTwd case final cap?) Text('每日上限 NT\$${money(cap)}'),
          if (zone.compatibility.provenance case final source?)
            _SourceLine(title: '停車規則來源', source: source),
          for (final evidence in zone.compatibility.ruleEvidence)
            _SourceLine(title: '停車規則證據', source: evidence.provenance),
          if (rate?.provenance case final source?)
            _SourceLine(title: '費率來源', source: source),
          for (final source in rate?.supportingSources ?? <ParkingSource>[])
            _SourceLine(title: '費率佐證', source: source),
          if (zone.availability?.provenance case final source?)
            _SourceLine(title: '空位來源', source: source),
        ],
      ),
    );
  }
}

class _SourceLine extends StatelessWidget {
  const _SourceLine({required this.title, required this.source});
  final String title;
  final ParkingSource source;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Text(
          '$title：${sourceLabel(source)}\n來源更新：${taipeiTime(source.sourceUpdatedAt)}\n擷取：${taipeiTime(source.fetchedAt)}',
          style: Theme.of(context).textTheme.bodySmall,
        ),
      );
}

class _EntranceFacts extends StatelessWidget {
  const _EntranceFacts({required this.entrance});
  final ParkingEntrance entrance;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              entrance.name ?? '停車場入口',
              style: Theme.of(context).textTheme.titleSmall,
            ),
            _Compatibility(
              status: entrance.heavyMotorcycleAccess,
              entrance: true,
            ),
            if (entrance.notes != null) Text(entrance.notes!),
            if (entrance.location == null) const Text('入口座標未提供'),
            _SourceLine(title: '入口來源', source: entrance.provenance),
          ],
        ),
      );
}
