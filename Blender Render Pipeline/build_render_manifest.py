"""
Build a render manifest by randomly selecting assets from curated asset pools.

This script does NOT touch Blender/bpy. It reads two config files:

  machine_config.json  - machine-specific library folder paths (edit per computer)
  asset_pools.json      - your curated list of chosen assets (portable across machines)

...and writes out a render_manifest_<timestamp>.json describing exactly which
assets to use for one render: 1 HDRI, 1 floor texture, and a random 3-10 models
(duplicates allowed, capped at a percentage of the total model count).

Your Blender-side pipeline script reads the manifest and does the actual
import/placement - that's a separate step from this one.

Usage:
    python3 build_render_manifest.py
    python3 build_render_manifest.py --duplicate-cap-percent 25
    python3 build_render_manifest.py --min-models 5 --max-models 12
    python3 build_render_manifest.py --machine-config my_machine.json --asset-pools my_pools.json
"""

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path

from asset_pool_selection import (
    load_json,
    select_checkpoint,
    validate_machine_config,
    validate_pools,
)


def build_manifest(
    machine_config: dict,
    pools: dict,
    min_models: int,
    max_models: int,
    duplicate_cap_percent: float,
) -> dict:
    selection = select_checkpoint(
        pools, machine_config, min_models, max_models, duplicate_cap_percent
    )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hdri": selection["hdri"],
        "floor_texture": selection["floor_texture"],
        "models": selection["models"],
        "models_meta": selection["models_meta"],
    }

    if selection["fallback_triggered"]:
        print(
            "[warning] Models pool is smaller than the unique count needed for this render."
        )
        print(
            "          Allowed more repeats than the duplicate cap to fill the count."
        )

    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a randomized render manifest from curated asset pools."
    )
    parser.add_argument(
        "--machine-config",
        default="machine_config.json",
        help="Path to machine-specific config (default: machine_config.json)",
    )
    parser.add_argument(
        "--asset-pools",
        default="asset_pools.json",
        help="Path to curated asset pool file (default: asset_pools.json)",
    )
    parser.add_argument(
        "--720p render example-dir",
        default="render_manifests",
        help="Directory to write the manifest into (default: render_manifests)",
    )
    parser.add_argument(
        "--min-models",
        type=int,
        default=3,
        help="Minimum models per render (default: 3)",
    )
    parser.add_argument(
        "--max-models",
        type=int,
        default=10,
        help="Maximum models per render (default: 10)",
    )
    parser.add_argument(
        "--duplicate-cap-percent",
        type=float,
        default=33.0,
        help="Max percentage of models that may be duplicates (default: 33)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed, for reproducing a specific manifest (default: none, fully random)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    machine_config = load_json(Path(args.machine_config))
    pools = load_json(Path(args.asset_pools))

    validate_machine_config(machine_config)
    validate_pools(pools)

    manifest = build_manifest(
        machine_config=machine_config,
        pools=pools,
        min_models=args.min_models,
        max_models=args.max_models,
        duplicate_cap_percent=args.duplicate_cap_percent,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"render_manifest_{timestamp}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"HDRI:           {manifest['hdri']['name']}  ({manifest['hdri']['source']})")
    print(
        f"Floor texture:  {manifest['floor_texture']['name']}  ({manifest['floor_texture']['source']})"
    )
    print(
        f"Models ({manifest['models_meta']['total_count']} total, "
        f"{manifest['models_meta']['duplicate_count']} duplicate):"
    )
    for m in manifest["models"]:
        print(f"  - {m['name']}  ({m['source']})")
    print(f"\nManifest written to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
