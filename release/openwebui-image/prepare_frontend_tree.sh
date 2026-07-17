#!/bin/sh
# Assemble the patched Open WebUI frontend source tree for the Memorist image.
#
# Inputs (all pinned by source-pin.json):
#   $1  path to the verified open_webui sdist tarball
#   $2  output directory for the assembled tree
#   $3  directory containing this script's siblings (patches/, frontend-overlay/)
#   $4  path to the Memorist UI TypeScript sources (open-webui-integration/memorist/ui)
#
# The script is deliberately fail-closed: any hash mismatch, missing overlay
# file, or patch hunk failure aborts the build instead of producing a
# silently unpatched frontend.
set -eu

SDIST="$1"
OUT="$2"
IMAGE_DIR="$3"
MEMORIST_UI="$4"

EXPECTED_SHA256="$(sed -n 's/.*"sha256": "\([0-9a-f]\{64\}\)".*/\1/p' "$IMAGE_DIR/source-pin.json" | head -n 1)"
EXTRACTED_ROOT="$(sed -n 's/.*"extracted_root": "\([^"]*\)".*/\1/p' "$IMAGE_DIR/source-pin.json")"

echo "verifying source snapshot hash..."
echo "$EXPECTED_SHA256  $SDIST" | sha256sum -c -

rm -rf "$OUT"
mkdir -p "$OUT"
tar -xzf "$SDIST" -C "$OUT" --strip-components=1 "$EXTRACTED_ROOT"

echo "copying Memorist UI modules into src/lib/memorist..."
mkdir -p "$OUT/src/lib/memorist"
cp "$MEMORIST_UI"/*.ts "$OUT/src/lib/memorist/"

echo "copying frontend overlay..."
cp -R "$IMAGE_DIR/frontend-overlay/src/." "$OUT/src/"

echo "applying integration patches..."
for patch_file in "$IMAGE_DIR"/patches/*.patch; do
    echo "  $(basename "$patch_file")"
    patch -p1 -d "$OUT" --forward --fuzz=0 < "$patch_file"
done

echo "verifying integration markers survived assembly..."
grep -q "MemoristComposerToggle" "$OUT/src/lib/components/chat/MessageInput.svelte"
grep -q "MemoristMessageDisclosure" "$OUT/src/lib/components/chat/Messages/ResponseMessage.svelte"
grep -q "memorist" "$OUT/src/lib/components/admin/Settings.svelte"
test -f "$OUT/src/routes/(app)/settings/memorist/+layout.svelte"
test -f "$OUT/src/lib/memorist/surfaces.ts"

echo "frontend tree assembled at $OUT"
