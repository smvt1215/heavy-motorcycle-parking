import '../../domain/parking.dart';

bool isUnverifiedLot(ParkingLot lot) => lot.isUnverified;

SpaceType markerSpace(ParkingLot lot) {
  final allowed = lot.zones.where(
    (zone) => zone.compatibility.status == CompatibilityStatus.allowed,
  );
  return (allowed.isNotEmpty ? allowed.first : lot.zones.first).spaceType;
}

String markerDescription(ParkingLot lot) => lot.isUnverified
    ? '尚未確認 · ${markerSpace(lot).label}'
    : '可停放 · ${markerSpace(lot).label}';

String distanceLabel(int meters) =>
    meters < 1000 ? '$meters 公尺' : '${(meters / 1000).toStringAsFixed(1)} 公里';

String money(num amount) => amount == amount.roundToDouble()
    ? amount.toInt().toString()
    : amount.toStringAsFixed(2);

String summaryLabel(AvailabilitySummary? summary) {
  if (summary == null) return '空位資料不明';
  if (summary.canShowTotals) {
    return '${summary.status.label} · ${summary.available}/${summary.total} 格';
  }
  final status = summary.status == AvailabilityStatus.available
      ? '上次觀測有空位（目前未確認）'
      : summary.status.label;
  return '$status · ${summary.coverage == Coverage.partial ? '部分涵蓋，總數不明' : '總數不明'}';
}

String summaryFreshness(AvailabilitySummary? summary) {
  if (summary == null) return '更新時間不明';
  // A claimed FRESH aggregate without full provenance is not a fresh total.
  if (summary.freshness == FreshnessStatus.fresh && !summary.canShowTotals) {
    return '整體即時資料未確認';
  }
  return summary.freshness.label;
}

String zoneAvailabilityLabel(ParkingZone zone) {
  final observation = zone.availability;
  if (observation == null) return '空位資料不明';
  if (observation.status == AvailabilityStatus.unknown) return '空位資料不明';
  if (!observation.hasValidCounts) {
    final label = observation.status == AvailabilityStatus.available
        ? '上次觀測：有空位（目前未確認）'
        : observation.status.label;
    return '$label · 數量未確認';
  }
  final counts = observation.available == null
      ? '數量不明'
      : '${observation.available}/${observation.total ?? '?'} 格';
  if (observation.freshness == FreshnessStatus.stale) {
    return '上次觀測：${observation.status.label} · $counts';
  }
  if (zone.hasConfirmedAvailability) return '目前有空位 · $counts';
  return observation.status == AvailabilityStatus.available
      ? '上次觀測：有空位（目前未確認） · $counts'
      : '${observation.status.label} · $counts';
}

String zoneFreshnessLabel(ParkingZone zone) {
  final observation = zone.availability;
  if (observation == null) return '更新時間不明';
  if (observation.freshness == FreshnessStatus.fresh &&
      observation.provenance.fetchedAt == null) {
    return '更新時間未確認';
  }
  return observation.freshness.label;
}

String rateLabel(RateSummary? rate) {
  if (rate == null) return '費率未確認';
  final hourly = rate.confirmedHourlyRateTwd;
  if (hourly != null) {
    return hourly == 0 ? '已確認免費' : 'NT\$${money(hourly)}/時';
  }
  return '${rate.displayText ?? '費率未確認'}（無可比較每小時費率）';
}

String sourceLabel(ParkingSource source) {
  final type = switch (source.sourceType) {
    'GOVERNMENT' => '政府開放資料',
    'OPERATOR' => '停車場業者',
    'COMMUNITY' => '社群回報',
    'MANUAL' => '人工確認',
    _ => '來源類型未確認',
  };
  return '$type #${source.sourceId}${source.sourceRecordId == null ? '' : ' · ${source.sourceRecordId}'}';
}

String taipeiTime(DateTime? instant) {
  if (instant == null) return '未提供';
  final time = instant.toUtc().add(const Duration(hours: 8));
  String pad(int value) => value.toString().padLeft(2, '0');
  return '${time.year}/${pad(time.month)}/${pad(time.day)} ${pad(time.hour)}:${pad(time.minute)}（台灣時間）';
}

List<ParkingZone> displayDetailZones(
  ParkingDetail detail, {
  required bool includeUnknown,
}) =>
    detail.zones
        .where(
          (zone) =>
              zone.spaceType.isSearchable &&
              zone.compatibility.vehicle == detail.vehicle &&
              (zone.compatibility.status == CompatibilityStatus.allowed ||
                  (includeUnknown &&
                      zone.compatibility.status ==
                          CompatibilityStatus.unknown)),
        )
        .toList(growable: false);
