#!/bin/bash
# Compile Onyx's app icon into Assets.car: a Liquid Glass icon for macOS 26 and later,
# and the transparent gem for everything before it.
#   ./launcher/icon/compile.sh <fill> <out-dir>      fill: none | system-light | system-dark
#
# Needs Xcode 26 (actool reads .icon only from 26 on), so it runs on GitHub's macos-26
# runner (.github/workflows/icon.yml), not on a macOS 14 Mac. Its Assets.car is committed
# as launcher/icon/Assets.car, and build-app.sh copies that in.
#
# Tahoe draws every app icon as a rounded square; an icon without one sits in a grey
# square macOS adds. Older macOS reads Assets.car before CFBundleIconFile, and a car
# compiled for older targets carries actool's flattened copy of the .icon, tile and all,
# which then replaces the transparent gem there. A classic AppIcon.appiconset of the same
# name doesn't stop it, and neither does MIN_TARGET=26.0: both tried 2026-09-23 on
# macOS 14, and both showed the tile. Shipping this car means the tile on every macOS.
set -euo pipefail

FILL="${1:?fill: none | system-light | system-dark}"
OUT="${2:?out dir}"
DIR="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
mkdir -p "$OUT"

swiftc -O "$DIR/../make-icon.swift" -o "$WORK/make-icon"
"$WORK/make-icon" "$WORK/gem.png" "$DIR/../onyx-gem.png"

# The classic set: the same art as AppIcon.icns.
SET="$WORK/Assets.xcassets/AppIcon.appiconset"
mkdir -p "$SET"
printf '{"info":{"author":"xcode","version":1}}\n' > "$WORK/Assets.xcassets/Contents.json"
IMAGES=""
for s in 16 32 128 256 512; do
  for k in 1 2; do
    px=$((s * k)); name="icon_${s}x${s}@${k}x.png"
    sips -z $px $px "$WORK/gem.png" --out "$SET/$name" >/dev/null
    IMAGES="$IMAGES{\"idiom\":\"mac\",\"size\":\"${s}x${s}\",\"scale\":\"${k}x\",\"filename\":\"$name\"},"
  done
done
printf '{"images":[%s],"info":{"author":"xcode","version":1}}\n' "${IMAGES%,}" > "$SET/Contents.json"

# The Liquid Glass icon: the gem, unglazed, on the chosen fill.
ICON="$WORK/AppIcon.icon"
mkdir -p "$ICON/Assets"
cp "$WORK/gem.png" "$ICON/Assets/gem.png"
cat > "$ICON/icon.json" <<JSON
{
  "fill" : "$FILL",
  "groups" : [
    {
      "layers" : [ { "image-name" : "gem.png", "name" : "gem", "glass" : false } ],
      "shadow" : { "kind" : "neutral", "opacity" : 0.5 },
      "specular" : true,
      "translucency" : { "enabled" : false, "value" : 0.5 }
    }
  ],
  "supported-platforms" : { "squares" : [ "macOS" ] }
}
JSON

xcrun actool "$WORK/Assets.xcassets" "$ICON" \
  --compile "$OUT" --platform macosx --target-device mac \
  --minimum-deployment-target "${MIN_TARGET:-13.0}" --app-icon AppIcon \
  --output-partial-info-plist "$OUT/partial-info.plist" \
  --output-format human-readable-text --notices --warnings --errors
xcrun assetutil --info "$OUT/Assets.car" > "$OUT/assetutil.json"

# Previews of the Tahoe renditions. ictool can't composite Clear (it needs a live background).
ICTOOL="$(dirname "$(xcode-select -p)")/Applications/Icon Composer.app/Contents/Executables/ictool"
if [ -x "$ICTOOL" ]; then
  for r in Default Dark TintedLight TintedDark; do
    "$ICTOOL" "$ICON" --export-image --output-file "$OUT/preview-$r.png" \
      --platform macOS --rendition "$r" --width 512 --height 512 --scale 1 || true
  done
fi
cp -R "$ICON" "$OUT/"
echo "✓ $OUT/Assets.car ($FILL)"
