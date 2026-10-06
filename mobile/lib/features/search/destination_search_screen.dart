import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../domain/place.dart';
import 'destination_search_controller.dart';

/// Full-screen destination search. Pops with the chosen [PlaceDestination];
/// popping without a result abandons the Places session.
class DestinationSearchScreen extends ConsumerStatefulWidget {
  const DestinationSearchScreen({super.key});

  @override
  ConsumerState<DestinationSearchScreen> createState() =>
      _DestinationSearchScreenState();
}

class _DestinationSearchScreenState
    extends ConsumerState<DestinationSearchScreen> {
  final _text = TextEditingController();

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  void _finish(PlaceDestination? destination) {
    if (destination != null && mounted) {
      Navigator.of(context).pop(destination);
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(destinationSearchControllerProvider);
    final controller = ref.read(destinationSearchControllerProvider.notifier);
    final showRecents = state.query.length < destinationSearchMinLength;
    return PopScope(
      // Stop debounce timers and in-flight requests as soon as the route
      // starts closing, not after its exit animation.
      onPopInvokedWithResult: (didPop, _) {
        if (didPop) controller.cancel();
      },
      child: Scaffold(
        appBar: AppBar(
          titleSpacing: 0,
          leading:
              BackButton(onPressed: () => Navigator.of(context).maybePop()),
          title: TextField(
            controller: _text,
            autofocus: true,
            enabled: !state.isResolving,
            textInputAction: TextInputAction.search,
            decoration: const InputDecoration(
              hintText: '搜尋地址或地點，例如：台北101',
              border: InputBorder.none,
            ),
            onChanged: controller.queryChanged,
            onSubmitted: (_) {
              if (state.status == DestinationSearchStatus.results &&
                  state.suggestions.isNotEmpty) {
                controller.select(state.suggestions.first).then(_finish);
              }
            },
          ),
          actions: [
            if (_text.text.isNotEmpty || state.query.isNotEmpty)
              IconButton(
                tooltip: '清除搜尋文字',
                icon: const Icon(Icons.close),
                onPressed: state.isResolving
                    ? null
                    : () {
                        _text.clear();
                        controller.cancel();
                      },
              ),
          ],
          bottom: state.status == DestinationSearchStatus.loading ||
                  state.isResolving
              ? const PreferredSize(
                  preferredSize: Size.fromHeight(2),
                  child: LinearProgressIndicator(minHeight: 2),
                )
              : null,
        ),
        body: SafeArea(
          top: false,
          child: Column(
            children: [
              if (state.selectionError case final error?)
                _Banner(
                  message: destinationErrorMessage(error),
                  icon: Icons.error_outline,
                ),
              Expanded(
                child: showRecents
                    ? _RecentList(state: state, onSelected: _finish)
                    : _Suggestions(state: state, onSelected: _finish),
              ),
              const _Attribution(),
            ],
          ),
        ),
      ),
    );
  }
}

class _Suggestions extends ConsumerWidget {
  const _Suggestions({required this.state, required this.onSelected});
  final DestinationSearchState state;
  final ValueChanged<PlaceDestination?> onSelected;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final controller = ref.read(destinationSearchControllerProvider.notifier);
    switch (state.status) {
      case DestinationSearchStatus.loading when state.suggestions.isEmpty:
      case DestinationSearchStatus.idle:
        return const _Message(icon: Icons.search, text: '搜尋中…');
      case DestinationSearchStatus.empty:
        return _Message(
          icon: Icons.travel_explore,
          text: '找不到「${state.query}」相關地點\n請改用完整地址或其他關鍵字。',
        );
      case DestinationSearchStatus.error:
        return _Message(
          icon: Icons.cloud_off,
          text: destinationErrorMessage(state.error!),
          action: FilledButton.icon(
            onPressed: controller.retry,
            icon: const Icon(Icons.refresh),
            label: const Text('重試'),
          ),
        );
      case DestinationSearchStatus.loading:
      case DestinationSearchStatus.results:
        return ListView.separated(
          itemCount: state.suggestions.length,
          separatorBuilder: (_, index) => const Divider(height: 1),
          itemBuilder: (context, index) {
            final suggestion = state.suggestions[index];
            return ListTile(
              minTileHeight: 56,
              leading: state.resolvingPlaceId == suggestion.placeId
                  ? const SizedBox.square(
                      dimension: 24,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.place_outlined),
              title: Text(suggestion.primaryText),
              subtitle: suggestion.secondaryText == null
                  ? null
                  : Text(suggestion.secondaryText!),
              enabled: !state.isResolving,
              onTap: () => controller.select(suggestion).then(onSelected),
            );
          },
        );
    }
  }
}

class _RecentList extends ConsumerWidget {
  const _RecentList({required this.state, required this.onSelected});
  final DestinationSearchState state;
  final ValueChanged<PlaceDestination?> onSelected;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final controller = ref.read(destinationSearchControllerProvider.notifier);
    if (state.recents.isEmpty) {
      return const _Message(
        icon: Icons.search,
        text: '輸入目的地，查詢附近可停重機的停車位置。',
      );
    }
    return ListView(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 4, 0),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  '最近搜尋',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
              ),
              TextButton(
                onPressed: state.isResolving ? null : controller.clearRecents,
                child: const Text('清除全部'),
              ),
            ],
          ),
        ),
        for (final recent in state.recents)
          ListTile(
            minTileHeight: 56,
            leading: state.resolvingPlaceId == recent.placeId
                ? const SizedBox.square(
                    dimension: 24,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.history),
            title: Text(recent.name),
            subtitle: recent.address == null ? null : Text(recent.address!),
            enabled: !state.isResolving,
            onTap: () => controller.selectRecent(recent).then(onSelected),
            trailing: IconButton(
              tooltip: '移除「${recent.name}」',
              icon: const Icon(Icons.close),
              onPressed: state.isResolving
                  ? null
                  : () => controller.removeRecent(recent.placeId),
            ),
          ),
      ],
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({required this.icon, required this.text, this.action});
  final IconData icon;
  final String text;
  final Widget? action;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 40, color: Theme.of(context).hintColor),
              const SizedBox(height: 12),
              Text(text, textAlign: TextAlign.center),
              if (action != null) ...[const SizedBox(height: 16), action!],
            ],
          ),
        ),
      );
}

class _Banner extends StatelessWidget {
  const _Banner({required this.message, required this.icon});
  final String message;
  final IconData icon;

  @override
  Widget build(BuildContext context) => Material(
        color: Theme.of(context).colorScheme.errorContainer,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          child: Row(
            children: [
              Icon(icon, color: Theme.of(context).colorScheme.onErrorContainer),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  message,
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.onErrorContainer,
                  ),
                ),
              ),
            ],
          ),
        ),
      );
}

/// Google attribution plus the boundary between geocoding and parking facts.
class _Attribution extends StatelessWidget {
  const _Attribution();

  @override
  Widget build(BuildContext context) {
    final style = Theme.of(context).textTheme.bodySmall;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('地點搜尋由 Google 提供', style: style),
          Text(
            '僅用於定位目的地；停車合法性、費率與空位皆由本服務資料判斷。',
            style: style,
          ),
        ],
      ),
    );
  }
}
