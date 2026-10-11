#!/bin/sh
# Copy the card from the ha-phase-map-card repository into the integration, which serves it.
# Usage: scripts/sync_card.sh [path/to/ha-phase-map-card]   (default: ../ha-phase-map-card)
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
src=${1:-"$here/../ha-phase-map-card"}/dist/phase-bus-card.js
dst=$here/custom_components/phase_map/frontend/phase-bus-card.js
cp "$src" "$dst"
sha=$(sha256sum "$dst" | cut -d' ' -f1)
version=$(sed -n 's/^const CARD_VERSION = "\(.*\)";$/\1/p' "$dst")
printf '{\n  "card_version": "%s",\n  "sha256": "%s"\n}\n' "$version" "$sha" > "$here/custom_components/phase_map/frontend/card.json"
echo "Bundled phase-bus-card $version ($sha)"
