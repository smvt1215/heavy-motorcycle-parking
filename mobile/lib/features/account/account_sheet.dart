import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/constants.dart';
import '../../domain/parking.dart';
import '../../domain/user.dart';
import '../map/map_controller.dart';
import '../reports/report_controller.dart';
import '../settings/appearance_controller.dart';
import 'account_controller.dart';

/// Account, preferred vehicle and saved parking. Guests can keep using all
/// public parking features without signing in.
class AccountSheet extends ConsumerWidget {
  const AccountSheet({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    final theme = Theme.of(context);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
        child: ListView(
          shrinkWrap: true,
          children: [
            Text(
              '我的帳號',
              style: theme.textTheme.titleLarge
                  ?.copyWith(fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 12),
            if (auth.status == AuthStatus.restoring)
              const LinearProgressIndicator()
            else if (!auth.isSignedIn)
              _GuestSection(auth: auth)
            else
              _SignedInSection(profile: auth.profile!),
            const Divider(height: 32),
            const _AppearanceSection(),
          ],
        ),
      ),
    );
  }
}

class _GuestSection extends ConsumerStatefulWidget {
  const _GuestSection({required this.auth});
  final AuthState auth;

  @override
  ConsumerState<_GuestSection> createState() => _GuestSectionState();
}

class _GuestSectionState extends ConsumerState<_GuestSection> {
  final _subject = TextEditingController(text: 'rider');

  @override
  void dispose() {
    _subject.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = ref.read(authControllerProvider.notifier);
    final auth = widget.auth;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Text('目前以訪客身分使用，仍可搜尋與查看停車資訊。'),
        const Text('登入後可收藏停車場與回報資料錯誤。'),
        if (auth.error case final error?) ...[
          const SizedBox(height: 8),
          Text(
            userErrorMessage(error),
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
          TextButton(onPressed: controller.retry, child: const Text('重試')),
        ],
        const SizedBox(height: 16),
        if (AppConstants.devSignIn) ...[
          TextField(
            controller: _subject,
            decoration: const InputDecoration(
              labelText: '開發者登入代號',
              helperText: '僅限開發環境；正式版將使用 Apple / Google 登入。',
            ),
          ),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: auth.busy
                ? null
                : () => controller.devSignIn(
                      _subject.text.trim(),
                      displayName: _subject.text.trim(),
                    ),
            child: Text(auth.busy ? '登入中…' : '開發者登入'),
          ),
        ] else
          const Text('登入功能即將推出。'),
      ],
    );
  }
}

class _SignedInSection extends ConsumerWidget {
  const _SignedInSection({required this.profile});
  final UserProfile profile;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.read(authControllerProvider.notifier);
    final favorites = ref.watch(favoritesControllerProvider);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const CircleAvatar(child: Icon(Icons.person)),
          title: Text(profile.label),
          subtitle: profile.email == null ? null : Text(profile.email!),
          trailing: TextButton(
            onPressed: auth.signOut,
            child: const Text('登出'),
          ),
        ),
        const SizedBox(height: 8),
        Text('預設車種', style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: 4),
        const Text('只用來預設地圖的車種；每次查詢仍會明確送出所選車種。'),
        const SizedBox(height: 8),
        const Text('普重：普通重型機車；大重：大型重型機車（黃牌／紅牌）'),
        SegmentedButton<VehicleType?>(
          showSelectedIcon: false,
          segments: const [
            ButtonSegment(value: VehicleType.normalHeavy, label: Text('普重')),
            ButtonSegment(value: VehicleType.largeHeavy, label: Text('大重')),
            ButtonSegment(value: null, label: Text('不設定')),
          ],
          selected: {profile.preferredVehicle},
          onSelectionChanged: (selection) async {
            final vehicle = selection.single;
            await auth.setVehicle(vehicle);
            if (vehicle != null) {
              await ref
                  .read(mapControllerProvider.notifier)
                  .setVehicle(vehicle);
            }
          },
        ),
        const Divider(height: 32),
        Text('收藏的停車場', style: Theme.of(context).textTheme.titleSmall),
        if (favorites.loading) const LinearProgressIndicator(),
        if (favorites.error case final error?) Text(userErrorMessage(error)),
        if (!favorites.loading && favorites.items.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: Text('尚未收藏停車場。在停車場詳情點選愛心即可收藏。'),
          ),
        for (final favorite in favorites.items)
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.favorite),
            title: Text(favorite.name),
            onTap: () {
              Navigator.of(context).pop();
              ref.read(mapControllerProvider.notifier).focus(favorite.location);
            },
            trailing: IconButton(
              tooltip: '移除收藏「${favorite.name}」',
              icon: const Icon(Icons.delete_outline),
              onPressed: favorites.pending.contains(favorite.parkingId)
                  ? null
                  : () => ref
                      .read(favoritesControllerProvider.notifier)
                      .toggle(favorite.parkingId),
            ),
          ),
      ],
    );
  }
}

/// System / Light / Dark. The map style follows the resulting theme.
class _AppearanceSection extends ConsumerWidget {
  const _AppearanceSection();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final mode = ref.watch(appearanceControllerProvider);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('外觀', style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: 8),
        SegmentedButton<ThemeMode>(
          showSelectedIcon: false,
          segments: [
            for (final value in ThemeMode.values)
              ButtonSegment(
                value: value,
                label: Text(value.label),
                icon: Icon(
                  switch (value) {
                    ThemeMode.system => Icons.brightness_auto_outlined,
                    ThemeMode.light => Icons.light_mode_outlined,
                    ThemeMode.dark => Icons.dark_mode_outlined,
                  },
                ),
              ),
          ],
          selected: {mode},
          onSelectionChanged: (selection) => ref
              .read(appearanceControllerProvider.notifier)
              .set(selection.single),
        ),
      ],
    );
  }
}
