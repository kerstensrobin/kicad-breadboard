#!/usr/bin/env bash
# Build a KiCad PCM-compatible release ZIP and update metadata.json with its hashes/sizes.
#
# Usage: bash build_release.sh [VERSION]
#   VERSION defaults to the version in metadata.json.
#
# After running:
#   1. Tag the commit:  git tag v<VERSION> && git push origin v<VERSION>
#   2. Create a GitHub release for that tag and upload the generated ZIP.
#   3. Verify the download_url in metadata.json matches the uploaded asset URL.
#   4. Submit metadata.json + resources/icon.png to the KiCad addons repo on GitLab:
#      https://gitlab.com/kicad/addons/metadata

set -euo pipefail
cd "$(dirname "$0")"

VERSION="${1:-$(python3 -c "import json; d=json.load(open('metadata.json')); print(d['versions'][0]['version'])")}"
OUTZIP="kicad-breadboard-${VERSION}.zip"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "Building v${VERSION} → ${OUTZIP}"

mkdir -p "$TMPDIR/plugins" "$TMPDIR/resources"

# Render SVG icon to 64x64 PNG (PCM requirement)
if command -v inkscape &>/dev/null; then
    inkscape --export-type=png --export-width=64 --export-height=64 \
        --export-filename="$TMPDIR/resources/icon.png" \
        plugins/breadboard/resources/icon.svg 2>/dev/null
elif command -v rsvg-convert &>/dev/null; then
    rsvg-convert -w 64 -h 64 plugins/breadboard/resources/icon.svg \
        -o "$TMPDIR/resources/icon.png"
else
    cp plugins/breadboard/resources/icon.png "$TMPDIR/resources/icon.png"
    echo "Warning: no SVG renderer found; icon copied as-is (should be 64x64)"
fi

# Copy plugin tree, stripping dev/cache artefacts. PCM requires __init__.py to
# sit directly inside the package's plugins/ dir, not a second level down, so
# we flatten breadboard/'s contents up rather than copying the folder itself.
cp -r plugins/breadboard/. "$TMPDIR/plugins/"
find "$TMPDIR/plugins" -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$TMPDIR/plugins" -name '*.pyc' -delete 2>/dev/null || true

# The metadata.json bundled *inside* the package must describe only its own
# version (PCM rejects a packaged copy with more than one version entry), and
# must omit the hash/size fields (which describe the outer zip and would go
# stale on every rebuild otherwise).
python3 - <<EOF
import json

with open("metadata.json") as f:
    m = json.load(f)

for v in m["versions"]:
    if v["version"] == "$VERSION":
        pkg_version = v
        break
else:
    raise SystemExit(f"Version $VERSION not found in metadata.json versions array")

pkg_metadata = dict(m)
pkg_metadata["versions"] = [{k: val for k, val in pkg_version.items()
                             if k not in ("download_sha256", "download_size", "install_size")}]

with open("$TMPDIR/metadata.json", "w") as f:
    json.dump(pkg_metadata, f, indent=2)
    f.write("\n")
EOF

# Build the ZIP with paths relative to TMPDIR root
python3 -c "
import zipfile, os, sys
out = sys.argv[1]
src = sys.argv[2]
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(src):
        for fname in files:
            full = os.path.join(root, fname)
            arcname = os.path.relpath(full, src)
            zf.write(full, arcname)
" "$OUTZIP" "$TMPDIR"

SHA256=$(sha256sum "$OUTZIP" | awk '{print $1}')
DOWNLOAD_SIZE=$(stat -c%s "$OUTZIP")
INSTALL_SIZE=$(du -sb "$TMPDIR" | awk '{print $1}')

# Patch metadata.json in-place
python3 - <<EOF
import json

with open("metadata.json") as f:
    m = json.load(f)

for v in m["versions"]:
    if v["version"] == "$VERSION":
        v["download_sha256"] = "$SHA256"
        v["download_size"] = $DOWNLOAD_SIZE
        v["install_size"] = $INSTALL_SIZE
        break
else:
    raise SystemExit(f"Version $VERSION not found in metadata.json versions array")

with open("metadata.json", "w") as f:
    json.dump(m, f, indent=2)
    f.write("\n")
EOF

echo ""
echo "Done."
echo "  ZIP:     $OUTZIP"
echo "  SHA-256: $SHA256"
echo "  Size:    $DOWNLOAD_SIZE bytes (ZIP) / $INSTALL_SIZE bytes (installed)"
echo ""
echo "metadata.json patched with the above values."
echo "Next steps:"
echo "  git add metadata.json $OUTZIP"
echo "  git tag v${VERSION} && git push origin v${VERSION}"
echo "  # Upload $OUTZIP to the GitHub release for v${VERSION}"
