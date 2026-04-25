# Installing Holons Desktop

## macOS

### First time

1. Download `Holons_1.0.0_aarch64.dmg` (or `_x86_64` for Intel Macs)
   from the latest GitHub release.
2. Double-click the `.dmg` → drag `Holons.app` into `Applications`.
3. **Right-click `Holons.app` → Open** (don't double-click the first
   time). macOS will say _"Apple could not verify Holons is free of
   malware."_ Click **Open**.
4. If the above doesn't give you an **Open** button and you see
   _"Holons.app is damaged and can't be opened"_, your Mac has
   flagged the download with the quarantine attribute. Strip it:

   ```bash
   sudo xattr -cr /Applications/Holons.app
   ```

   Then double-click to launch normally.

### Why the warnings?

Holons is built with an **ad-hoc code signature** — it's cryptographically
sealed so macOS knows the binary hasn't been tampered with after
download, but it's not signed by an Apple Developer ID. That means:

- Gatekeeper will always prompt on first open.
- After the first "Open" confirmation, macOS remembers the decision —
  subsequent launches are one double-click.

We'll move to Apple Developer ID + notarization (one-click install,
no warnings) once there's meaningful adoption. In the meantime the
right-click-Open flow is the standard friction path for OSS Mac apps.

### Upgrading to a new version

Your data is stored **outside the app bundle**, at
`~/.agent_company/data.db`. Replacing `Holons.app` never touches that
directory, so upgrades are non-destructive by default:

1. **Recommended**: open Holons → Settings → Personal → Backup & export
   → **Download backup (.db)** before installing the new version.
2. Quit the running app (Cmd-Q).
3. Drag the new `Holons.app` into `Applications`, overwriting the old one.
4. Re-open. On startup, schema migrations run automatically
   (they're additive — `ALTER TABLE ADD COLUMN IF NOT EXISTS`, never
   destructive), so new features light up without losing existing rows.

**If something goes wrong after upgrade**, restore the backup:

1. Quit Holons completely.
2. Copy your `holons-backup-*.db` to `~/.agent_company/data.db`
   (overwrite).
3. Remove WAL sidecars: `rm -f ~/.agent_company/data.db-shm ~/.agent_company/data.db-wal`
4. Reopen Holons.

### Downgrade

Generally supported — the schema only adds columns/tables, never drops
them. But if you opened your DB with a much newer version and then
tried to run a much older one, unknown columns will be ignored and
some features just won't surface. Restore from a backup taken before
you upgraded if anything looks off.

### Uninstall

Drag `/Applications/Holons.app` to Trash, plus optionally clear the
support data the app leaves behind on macOS:

```bash
# 1. App bundle
rm -rf /Applications/Holons.app

# 2. Tauri-managed support data (window state, tray store, webview cache)
rm -rf ~/Library/Application\ Support/com.holons.desktop
rm -rf ~/Library/Caches/com.holons.desktop
rm -rf ~/Library/WebKit/com.holons.desktop
rm    ~/Library/Preferences/com.holons.desktop.plist 2>/dev/null

# 3. Personal-mode SQLite data + first-run marker (your agents,
#    threads, runs). Optional — keep it if you'll reinstall and want
#    your demo team back without re-seeding.
rm -rf ~/.agent_company
```

To re-trigger first-run setup without losing the SQLite data, just
delete the marker file: `rm ~/.agent_company/.seeded`.

## Windows

1. Download `Holons_1.0.0_x64_en-US.msi`.
2. Run the installer. Windows SmartScreen will warn "unrecognized
   publisher" — click **More info** → **Run anyway**.
3. Launch Holons from Start.

## Linux

AppImage is available:

```bash
chmod +x Holons_1.0.0_amd64.AppImage
./Holons_1.0.0_amd64.AppImage
```

Or for Debian/Ubuntu there's a `.deb`:

```bash
sudo dpkg -i holons_1.0.0_amd64.deb
```

## Build from source

```bash
git clone https://github.com/jhk482001/Holons.git
cd Holons

# Sidecar (Python backend bundled as a standalone binary)
bash build/build_sidecar.sh

# Desktop app
cd desktop
npm install
npm run tauri -- build
```

Artifacts land in `desktop/src-tauri/target/release/bundle/`:
- macOS: `macos/Holons.app` + `macos/*.dmg`
- Windows: `msi/*.msi`
- Linux: `appimage/*.AppImage`, `deb/*.deb`

## Troubleshooting

**macOS: "Holons.app is damaged"** — see first-time install step 4.
If `xattr -cr` doesn't help, the download may actually be corrupted —
re-download and check the SHA in the release notes.

**macOS: app opens but shows a blank window** — the embedded Python
backend (sidecar) hasn't started. Check Console.app for
`agent-company-backend` messages. On ARM Macs, make sure you
downloaded the `aarch64` build, not `x86_64`.

**Windows: "Windows protected your PC"** — SmartScreen false positive
for unsigned binaries. Click **More info** → **Run anyway**.

**Linux: AppImage won't run** — you may need `libfuse2`:
```bash
sudo apt install libfuse2
```
