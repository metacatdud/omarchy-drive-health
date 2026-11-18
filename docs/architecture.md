# Omarchy integration findings

Inspected the installed Omarchy 4.5.0-3 plugin validator, plugin-add command,
Panel.qml, BarIconButton.qml, KeyboardPanel.qml, PanelKeyCatcher.qml,
Translations.qml, and the first-party monitor and battery plugins.

## Native contract

1. A Git repository's root `manifest.json` declares `schemaVersion: 1`, a
   unique non-reserved ID, version, kinds, and relative entry points.
   `omarchy.*` belongs to first-party plugins. This prototype uses
   `community.drive-health`; a publisher can adopt their own stable namespace.
2. A `bar-widget` exports `entryPoints.barWidget`. The shell supplies `bar`
   and inline `settings`. `Panel` already exposes these, setting defaults,
   open/close state, IPC, and neighboring-panel switching.
3. `BarIconButton` supplies the shared icon geometry and orientation behavior.
   `KeyboardPanel` supplies the anchored popup, motion, dismissal, and mirroring.
   `PanelKeyCatcher` provides shared keyboard semantics. Subclass handlers should
   use its signals rather than override its `Keys.onPressed` dispatcher.
4. `qs.Commons.Style` and `Color` supply live theme/font/spacing values.
   `Translations` loads plugin-owned catalogs with English fallback.
5. Installation is through `omarchy plugin add <git-url> --enable`; updates
   use `omarchy plugin update <id>`. User plugins live outside packaged Omarchy
   source and survive Omarchy updates. The plugin manager validates manifests
   but does not install a system collector or its dependencies.

## Process boundary

```text
systemd timer (hourly + startup/catch-up)
  -> root-owned Python collector
     -> smartctl --scan --json, then smartctl --all --json per discovered SSD
     -> SQLite drive registry, hourly snapshots, daily write spans
     -> atomic sanitized JSON cache

Omarchy bar (unprivileged)
  -> Python report reader (1 min; also on popup open)
     -> cache + user's optional warranty metadata
     -> QML plain-text rows and progress bars
```

The collector is packaged separately because giving the desktop raw-device
privileges, invoking authentication on every poll, or allowing root to run
an auto-updated user plugin script would be poor boundaries. Backend updates
are explicit package updates; plugin Git updates cannot replace the installed
root-owned collector. Cache schema version 1 is the interface between them.

## Why the data is portable

There are no hard-coded drive names or endurance ratings. Scanning runs on
every collection and IDs use hardware identity. Warranty values are optional
user metadata, never baked into QML or the collector. Standard NVMe fields are
used directly; ATA counter interpretation is intentionally conservative.
Additional vendor adapters should include representative fixtures and unit
tests establishing the units before reporting a percentage.

References for counter interpretation:

- [NVMe specification](https://www.nvmexpress.org/wp-content/uploads/NVM-Express-1_4c-2021.06.28-Ratified.pdf)
- [smartmontools drive database](https://github.com/smartmontools/smartmontools/blob/main/drivedb/drivedb.h)
- [Samsung SMART application note](https://download.semiconductor.samsung.com/resources/others/SSD_Application_Note_SMART_final.pdf)

## Next steps beyond the prototype

Live NVMe and Samsung SATA collection is verified. Test additional SATA vendors, add supported
vendor fixtures, test all bar orientations and languages, then add deduplicated
notifications. Hourly snapshots and daily write spans are now retained locally. The current 90% wear threshold
is a presentation policy, not a manufacturer failure criterion. Warranty/TBW
consumption must never become a claim that a disk will fail on a given date.
