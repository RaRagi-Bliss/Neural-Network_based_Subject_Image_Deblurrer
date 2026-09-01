#!/usr/bin/env bash
# =============================================================================
#  run_pipeline.sh  —  Launch the Blender rendering pipeline headlessly
#  Works on: macOS (dev machine) and headless Linux (production/cluster)
#  Usage:
#    ./run_pipeline.sh                          # uses config.json defaults
#    ./run_pipeline.sh --count 50               # override num renders
#    ./run_pipeline.sh --scene my_scene.blend   # use a specific .blend file
#    ./run_pipeline.sh --config my_cfg.json     # use a different config
#    ./run_pipeline.sh --outdir /data/renders   # override output directory
#
#  Override the Blender binary location without editing this file:
#    BLENDER_BIN=/custom/path/to/blender ./run_pipeline.sh --count 50
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Locate Blender ───────────────────────────────────────────────────────────
BLENDER_BIN="${BLENDER_BIN:-}"

if [ -z "$BLENDER_BIN" ]; then
    if command -v blender &>/dev/null; then
        # Already on PATH — typical for a Linux tarball extracted into
        # ~/blender/ with a symlink, or a system package.
        BLENDER_BIN="blender"
    elif [ -f "/Applications/Blender.app/Contents/MacOS/Blender" ]; then
        # macOS default install location (standard .dmg install)
        BLENDER_BIN="/Applications/Blender.app/Contents/MacOS/Blender"
    elif [ -f "$HOME/Library/Application Support/Steam/steamapps/common/Blender/Blender.app/Contents/MacOS/Blender" ]; then
        # macOS install via Steam
        BLENDER_BIN="$HOME/Library/Application Support/Steam/steamapps/common/Blender/Blender.app/Contents/MacOS/Blender"
    elif [ -f "/opt/blender/blender" ]; then
        # Common manual-install location on headless Linux servers
        BLENDER_BIN="/opt/blender/blender"
    else
        echo "ERROR: Could not find a Blender executable."
        echo "  Checked: PATH, /Applications/Blender.app/..., Steam install, /opt/blender/blender"
        echo "  Fix by either:"
        echo "    a) adding blender to PATH, or"
        echo "    b) running: BLENDER_BIN=/full/path/to/blender ./run_pipeline.sh"
        exit 1
    fi
fi

echo "Using Blender: $BLENDER_BIN"
echo "Blender version: $("$BLENDER_BIN" --version 2>/dev/null | head -1)"
echo ""

# ── Parse arguments ──────────────────────────────────────────────────────────
SCENE_FILE=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scene)
            SCENE_FILE="$2"
            shift 2
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

# ── Build Blender command ─────────────────────────────────────────────────────
CMD=("$BLENDER_BIN" "--background")

if [ -n "$SCENE_FILE" ]; then
    if [ ! -f "$SCENE_FILE" ]; then
        echo "ERROR: Scene file not found: $SCENE_FILE"
        exit 1
    fi
    CMD+=("$SCENE_FILE")
    echo "Scene file: $SCENE_FILE"
else
    echo "No .blend file specified — using procedural test scene."
fi

CMD+=("--python" "$SCRIPT_DIR/render_pipeline.py")
CMD+=("--")
CMD+=("--config" "$SCRIPT_DIR/config.json")
CMD+=("${EXTRA_ARGS[@]}")

# ── Run ───────────────────────────────────────────────────────────────────────
echo "Command: ${CMD[*]}"
echo ""
echo "Starting pipeline at $(date)"
echo "============================================================"

"${CMD[@]}"

echo ""
echo "============================================================"
echo "Done at $(date)"
