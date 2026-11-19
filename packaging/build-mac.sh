#!/usr/bin/env bash
set -euo pipefail
version="$(python -c 'from convert_project import VERSION; print(VERSION)')"
architecture="$(uname -m)"
export PYTHONPATH="$PWD"
mkdir -p build/icon.iconset artifacts
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" assets/ace-daw-icon.png --out "build/icon.iconset/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" assets/ace-daw-icon.png --out "build/icon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns build/icon.iconset -o build/icon.icns
python -m PyInstaller --clean --noconfirm --windowed --onedir --name 'ACE-DAW Transfer' \
  --osx-bundle-identifier audio.qing.ace-daw-transfer --icon build/icon.icns \
  --add-data 'templates:templates' --add-data 'assets:assets' \
  --copy-metadata tkinterdnd2 --collect-all zstandard --collect-all cbor2 app.py
bundle="dist/ACE-DAW Transfer.app"
plist="$bundle/Contents/Info.plist"
for field in CFBundleVersion CFBundleShortVersionString; do
  /usr/libexec/PlistBuddy -c "Set :$field $version" "$plist" || /usr/libexec/PlistBuddy -c "Add :$field string $version" "$plist"
done
codesign --force --sign - "$bundle"
codesign --verify --deep --strict "$bundle"
lipo -verify_arch "$architecture" "$bundle/Contents/MacOS/ACE-DAW Transfer"
python packaging/smoke.py "$bundle/Contents/MacOS/ACE-DAW Transfer"
mkdir -p build/payload
ditto "$bundle" "build/payload/ACE-DAW Transfer.app"
pkgbuild --root build/payload --install-location /Applications \
  --identifier "audio.qing.ace-daw-transfer.$architecture" --version "$version" "artifacts/$architecture.pkg"

