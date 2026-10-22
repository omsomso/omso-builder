#!/bin/bash
# Build omso Builder.app (SwiftUI) into build/, link it from the project folder and self-test it.
# Builds no products and installs nothing.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
app="$project_dir/build/omso Builder.app"
cache="$project_dir/build/swift-module-cache"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" "$cache"
xcrun swiftc -parse-as-library -O -target "$(uname -m)-apple-macosx13.0" -module-cache-path "$cache" \
    "$project_dir/app/OmsoBuilder.swift" "$project_dir/app/BuildRunner.swift" \
    "$project_dir/app/SourcePicker.swift" "$project_dir/app/ReadyBuilds.swift" -o "$app/Contents/MacOS/OmsoBuilder"
# The icon is drawn by app/icon/make_icon.swift; redraw it only when that file changes.
icon_source="$project_dir/app/icon/make_icon.swift"
icon="$project_dir/build/AppIcon.icns"
if [[ ! -s "$icon" || "$icon_source" -nt "$icon" ]]; then
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    xcrun swiftc -O -target "$(uname -m)-apple-macosx13.0" -module-cache-path "$cache" "$icon_source" -o "$work/make-icon"
    "$work/make-icon" "$work/icon-1024.png"
    mkdir "$work/AppIcon.iconset"
    for size in 16 32 128 256 512; do
        sips -z "$size" "$size" "$work/icon-1024.png" --out "$work/AppIcon.iconset/icon_${size}x${size}.png" >/dev/null
        double=$((size * 2))
        sips -z "$double" "$double" "$work/icon-1024.png" --out "$work/AppIcon.iconset/icon_${size}x${size}@2x.png" >/dev/null
    done
    iconutil -c icns "$work/AppIcon.iconset" -o "$icon"
fi
cp "$icon" "$app/Contents/Resources/AppIcon.icns"
/usr/bin/python3 - "$app" "$project_dir" <<'PY'
from pathlib import Path
import plistlib
import sys
app, root = map(Path, sys.argv[1:])
info = dict(CFBundleExecutable='OmsoBuilder', CFBundleIdentifier='com.omso.tools.builder',
            CFBundleName='omso Builder', CFBundleDisplayName='omso Builder', CFBundlePackageType='APPL',
            CFBundleVersion='1', CFBundleShortVersionString='1.0', CFBundleIconFile='AppIcon',
            LSMinimumSystemVersion='13.0', NSHighResolutionCapable=True)
(app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
(app / 'Contents/Resources/project-path.txt').write_text(str(root) + '\n')
link = root / app.name
if link.is_symlink():
    link.unlink()
elif link.exists():
    raise SystemExit('Refusing to replace a real app: ' + str(link))
link.symlink_to(app)
print('App:', link)
PY
codesign --force --sign - "$app"
codesign --verify --deep --strict "$app"
# Finder and the Dock cache bundle icons; a fresh modification date makes them reread it.
touch "$app"
"$app/Contents/MacOS/OmsoBuilder" --self-test
