import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';

import '../../data/location_service.dart';
import '../../data/map_configuration.dart';
import '../../domain/parking.dart';
import '../../domain/place.dart';
import '../search/destination_search_screen.dart';
import 'filters_sheet.dart';
import 'map_controller.dart';
import 'map_styles.dart';
import 'marker_icons.dart';
import 'parking_panel.dart';
import 'parking_presentation.dart';

typedef MapCanvasBuilder = Widget Function(
  BuildContext,
  MapCanvasConfiguration,
);

class MapCanvasConfiguration {
  const MapCanvasConfiguration({
    required this.center,
    required this.items,
    required this.style,
    required this.myLocationEnabled,
    required this.onCameraMove,
    required this.onCameraIdle,
    required this.onSelect,
    this.destination,
  });
  final GeoPoint center;
  final List<ParkingLot> items;
  final String style;
  final bool myLocationEnabled;
  final ValueChanged<GeoPoint> onCameraMove;
  final VoidCallback onCameraIdle;
  final ValueChanged<ParkingLot> onSelect;

  /// Places destination marker; it is not a parking result.
  final PlaceDestination? destination;
}

class MapScreen extends ConsumerStatefulWidget {
  const MapScreen({super.key, this.mapBuilder, this.autoSearch = true});
  final MapCanvasBuilder? mapBuilder;
  final bool autoSearch;

  @override
  ConsumerState<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends ConsumerState<MapScreen> {
  GoogleMapController? _map;
  MarkerIcons? _icons;
  bool _locationGranted = false;
  bool _locating = false;
  static const _clusterId = ClusterManagerId('parking');

  @override
  void initState() {
    super.initState();
    if (widget.mapBuilder == null) {
      unawaited(
        MarkerIcons.create().then((icons) {
          if (mounted) setState(() => _icons = icons);
        }),
      );
    }
    if (widget.autoSearch) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && !ref.read(mapControllerProvider).hasSearched) {
          unawaited(ref.read(mapControllerProvider.notifier).search());
        }
      });
    }
  }

  @override
  void dispose() {
    _map?.dispose();
    super.dispose();
  }

  Future<void> _locate() async {
    setState(() => _locating = true);
    try {
      final point = await ref.read(locationServiceProvider).locate();
      if (!mounted) return;
      setState(() => _locationGranted = true);
      final map = _map;
      if (map == null) {
        _message('地圖尚未就緒，請稍後再定位。');
        return;
      }
      await map
          .animateCamera(CameraUpdate.newLatLng(LatLng(point.lat, point.lng)));
    } on LocationFailure catch (error) {
      if (mounted) _message(error.message);
    } catch (_) {
      if (mounted) _message('目前無法取得位置，請移動地圖後搜尋此區域。');
    } finally {
      if (mounted) setState(() => _locating = false);
    }
  }

  Future<void> _searchDestination() async {
    final destination = await Navigator.of(context).push<PlaceDestination>(
      MaterialPageRoute(
        fullscreenDialog: true,
        builder: (_) => const DestinationSearchScreen(),
      ),
    );
    if (destination == null || !mounted) return;
    await ref
        .read(mapControllerProvider.notifier)
        .searchDestination(destination);
  }

  void _moveCamera(PlaceDestination? previous, PlaceDestination? next) {
    if (next == null || identical(previous, next)) return;
    unawaited(
      _map?.animateCamera(
        CameraUpdate.newLatLngZoom(
          LatLng(next.location.lat, next.location.lng),
          16,
        ),
      ),
    );
  }

  void _message(String message) => ScaffoldMessenger.of(context)
      .showSnackBar(SnackBar(content: Text(message)));

  Future<void> _filters(ParkingQuery query) async {
    final selected = await showModalBottomSheet<ParkingQuery>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => FiltersSheet(query: query),
    );
    if (selected != null && mounted) {
      ref.read(mapControllerProvider.notifier).setFilters(selected);
    }
  }

  @override
  Widget build(BuildContext context) {
    ref.listen<PlaceDestination?>(
      mapControllerProvider.select((state) => state.destination),
      _moveCamera,
    );
    final state = ref.watch(mapControllerProvider);
    final controller = ref.read(mapControllerProvider.notifier);
    final dark = Theme.of(context).brightness == Brightness.dark;
    final config = MapCanvasConfiguration(
      center: state.query.center,
      items: state.items,
      style: dark ? darkMapStyle : lightMapStyle,
      myLocationEnabled: _locationGranted,
      onCameraMove: controller.cameraMoved,
      onCameraIdle: controller.cameraIdle,
      onSelect: controller.selectLot,
      destination: state.destination,
    );
    return Scaffold(
      body: Stack(
        children: [
          Positioned.fill(
            child: widget.mapBuilder?.call(context, config) ??
                ref.watch(mapConfigurationProvider).when(
                      data: (ready) =>
                          ready ? _googleMap(config) : const _MapUnavailable(),
                      loading: () =>
                          const Center(child: CircularProgressIndicator()),
                      error: (_, stack) => const _MapUnavailable(),
                    ),
          ),
          SafeArea(
            bottom: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Material(
                    elevation: 3,
                    borderRadius: BorderRadius.circular(24),
                    color: Theme.of(context).colorScheme.surface,
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(18, 8, 8, 8),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(
                              '重機停車通',
                              style: Theme.of(context)
                                  .textTheme
                                  .titleLarge
                                  ?.copyWith(fontWeight: FontWeight.w800),
                            ),
                          ),
                          SegmentedButton<VehicleType>(
                            showSelectedIcon: false,
                            segments: const [
                              ButtonSegment(
                                value: VehicleType.yellow,
                                label: Text('黃牌'),
                              ),
                              ButtonSegment(
                                value: VehicleType.red,
                                label: Text('紅牌'),
                              ),
                            ],
                            selected: {state.query.vehicle},
                            onSelectionChanged: (selection) =>
                                controller.setVehicle(selection.single),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  _DestinationBar(
                    destination: state.destination,
                    onSearch: _searchDestination,
                    onClear: controller.clearDestination,
                  ),
                  const SizedBox(height: 8),
                  SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: Row(
                      children: [
                        FilterChip(
                          avatar: const Icon(Icons.local_parking, size: 18),
                          label: const Text('目前有空位'),
                          selected: state.query.availableOnly,
                          onSelected: (value) => controller.setFilters(
                            state.query.copyWith(availableOnly: value),
                          ),
                        ),
                        const SizedBox(width: 8),
                        PopupMenuButton<num>(
                          tooltip: '已確認每小時費率',
                          onSelected: (value) => controller.setFilters(
                            state.query.copyWith(
                              hourlyRateMaxTwd: value < 0 ? null : value,
                            ),
                          ),
                          itemBuilder: (_) => const [
                            PopupMenuItem(value: -1, child: Text('不限已確認費率')),
                            PopupMenuItem(value: 0, child: Text('已確認免費')),
                            PopupMenuItem(
                              value: 50,
                              child: Text('每小時 ≤ NT\$50'),
                            ),
                            PopupMenuItem(
                              value: 100,
                              child: Text('每小時 ≤ NT\$100'),
                            ),
                          ],
                          child: Chip(
                            avatar:
                                const Icon(Icons.payments_outlined, size: 18),
                            label: Text(
                              state.query.hourlyRateMaxTwd == null
                                  ? '每小時費率'
                                  : '≤ NT\$${state.query.hourlyRateMaxTwd!.toInt()}/時',
                            ),
                          ),
                        ),
                        const SizedBox(width: 8),
                        ActionChip(
                          avatar: const Icon(Icons.tune, size: 18),
                          label: const Text('更多條件'),
                          onPressed: () => _filters(state.query),
                        ),
                      ],
                    ),
                  ),
                  if (state.needsAreaSearch)
                    Center(
                      child: Padding(
                        padding: const EdgeInsets.only(top: 12),
                        child: FilledButton.icon(
                          onPressed: state.isLoading ? null : controller.search,
                          icon: const Icon(Icons.search),
                          label: const Text('搜尋此區域'),
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
          if (state.selectedLot == null)
            Positioned(
              right: 16,
              bottom: MediaQuery.sizeOf(context).height * 0.28 + 20,
              child: FloatingActionButton.small(
                heroTag: 'my-location',
                tooltip: '定位我的位置',
                onPressed: _locating ? null : _locate,
                child: _locating
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.my_location),
              ),
            ),
          ParkingPanel(state: state),
        ],
      ),
    );
  }

  Widget _googleMap(MapCanvasConfiguration config) {
    return GoogleMap(
      initialCameraPosition: CameraPosition(
        target: LatLng(config.center.lat, config.center.lng),
        zoom: 15,
      ),
      style: config.style,
      myLocationEnabled: config.myLocationEnabled,
      myLocationButtonEnabled: false,
      zoomControlsEnabled: false,
      mapToolbarEnabled: false,
      padding: EdgeInsets.only(
        bottom: MediaQuery.sizeOf(context).height * 0.28,
        top: 220,
      ),
      onMapCreated: (map) => _map = map,
      onCameraMove: (position) => config.onCameraMove(
        GeoPoint(position.target.latitude, position.target.longitude),
      ),
      onCameraIdle: config.onCameraIdle,
      onTap: (_) => ref.read(mapControllerProvider.notifier).closeSelection(),
      clusterManagers: {
        ClusterManager(
          clusterManagerId: _clusterId,
          onClusterTap: (cluster) => _map
              ?.animateCamera(CameraUpdate.newLatLngBounds(cluster.bounds, 48)),
        ),
      },
      markers: {
        if (config.destination case final destination?)
          Marker(
            markerId: const MarkerId('destination'),
            position:
                LatLng(destination.location.lat, destination.location.lng),
            icon: BitmapDescriptor.defaultMarkerWithHue(
              BitmapDescriptor.hueAzure,
            ),
            zIndexInt: 1,
            infoWindow: InfoWindow(
              title: destination.name,
              snippet: '搜尋目的地（非停車位置）',
            ),
          ),
        if (_icons != null)
          for (final lot in config.items)
            Marker(
              markerId: MarkerId('parking-${lot.id}'),
              clusterManagerId: _clusterId,
              position: LatLng(lot.location.lat, lot.location.lng),
              icon: _icons!
                  .forSpace(markerSpace(lot), unverified: isUnverifiedLot(lot)),
              infoWindow:
                  InfoWindow(title: lot.name, snippet: markerDescription(lot)),
              onTap: () => config.onSelect(lot),
            ),
      },
    );
  }
}

class _DestinationBar extends StatelessWidget {
  const _DestinationBar({
    required this.destination,
    required this.onSearch,
    required this.onClear,
  });
  final PlaceDestination? destination;
  final VoidCallback onSearch;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final selected = destination;
    return Material(
      elevation: 2,
      color: scheme.surface,
      borderRadius: BorderRadius.circular(24),
      child: Row(
        children: [
          Expanded(
            child: Semantics(
              button: true,
              label: selected == null ? '搜尋目的地' : '目的地：${selected.name}，重新搜尋',
              excludeSemantics: true,
              child: InkWell(
                borderRadius: BorderRadius.circular(24),
                onTap: onSearch,
                child: ConstrainedBox(
                  constraints: const BoxConstraints(minHeight: 48),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Row(
                      children: [
                        Icon(
                          selected == null ? Icons.search : Icons.flag,
                          color: scheme.primary,
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Text(
                            selected?.name ?? '搜尋目的地',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: selected == null
                                ? TextStyle(color: scheme.onSurfaceVariant)
                                : null,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
          if (selected != null)
            IconButton(
              tooltip: '清除目的地',
              icon: const Icon(Icons.close),
              onPressed: onClear,
            ),
        ],
      ),
    );
  }
}

class _MapUnavailable extends StatelessWidget {
  const _MapUnavailable();

  @override
  Widget build(BuildContext context) => ColoredBox(
        color: Theme.of(context).colorScheme.surfaceContainer,
        child: const Center(
          child: Padding(
            padding: EdgeInsets.all(24),
            child: Text('地圖尚未就緒\n仍可查看附近停車資訊', textAlign: TextAlign.center),
          ),
        ),
      );
}
