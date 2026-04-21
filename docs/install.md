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

### Uninstall

```bash
sudo rm -rf /Applications/Holons.app
rm -rf ~/Library/Application\ Support/holons
rm -rf ~/Library/Preferences/com.holons.desktop.plist
```

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
