"""
asset_pool_selection.py

Pure-Python asset pool selection logic — no bpy dependency.
Shared by:
  - build_render_manifest.py  (standalone preview/dry-run tool, runs with plain python3)
  - asset_pool_blender.py     (the real loader used inside the Blender pipeline)

Keeping this bpy-free means you can preview what a checkpoint's selection would
look like without needing Blender installed at all.
"""

import json
import math
import os
import random
import sys
from pathlib import Path

VALID_SOURCES = {"poly_haven", "manual"}

# Poly Haven's info.json uses a numeric type code: 0 = HDRIs, 1 = Textures, 2 = Models.
TYPE_CODE_TO_POOL = {0: "hdris", 1: "floor_textures", 2: "models"}


def load_json(path) -> dict:
    path = Path(path)
    if not path.exists():
        print(f"[error] Config file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_machine_config(machine_config: dict) -> None:
    for key in ("poly_haven_library_path", "manual_asset_library_path"):
        if key not in machine_config:
            print(
                f"[error] machine_config.json missing required key: {key}",
                file=sys.stderr,
            )
            sys.exit(1)


def validate_pools(pools: dict) -> None:
    for pool_name in ("hdris", "models", "floor_textures"):
        if pool_name not in pools:
            print(
                f"[error] asset_pools.json missing required pool: {pool_name}",
                file=sys.stderr,
            )
            sys.exit(1)
        if not pools[pool_name]:
            print(
                f"[error] Pool '{pool_name}' is empty - add at least one asset before running.",
                file=sys.stderr,
            )
            sys.exit(1)
        for entry in pools[pool_name]:
            if entry.get("source") not in VALID_SOURCES:
                print(
                    f"[error] Invalid source in pool '{pool_name}': {entry}",
                    file=sys.stderr,
                )
                print(
                    f"        source must be one of: {VALID_SOURCES}", file=sys.stderr
                )
                sys.exit(1)


def resolve_library_path(source: str, machine_config: dict) -> str:
    if source == "poly_haven":
        return machine_config["poly_haven_library_path"]
    return machine_config["manual_asset_library_path"]


def pick_single(pool: list, machine_config: dict) -> dict:
    """Pick exactly one asset from a pool, resolving its library path."""
    choice = random.choice(pool)
    return {
        "name": choice["name"],
        "source": choice["source"],
        "library_path": resolve_library_path(choice["source"], machine_config),
    }


def pick_models_with_duplicate_cap(
    pool: list,
    machine_config: dict,
    min_count: int,
    max_count: int,
    duplicate_cap_percent: float,
) -> dict:
    """
    Randomly select a count between min_count-max_count (inclusive) from the models pool.
    Duplicates are allowed but capped at duplicate_cap_percent of the total count.
    """
    total_count = random.randint(min_count, max_count)
    max_duplicate_slots = math.floor(total_count * (duplicate_cap_percent / 100.0))
    unique_needed = total_count - max_duplicate_slots

    fallback_triggered = False
    if unique_needed > len(pool):
        fallback_triggered = True
        unique_needed = len(pool)

    unique_picks = random.sample(pool, unique_needed)
    chosen = list(unique_picks)

    duplicate_slots_remaining = total_count - len(chosen)
    for _ in range(duplicate_slots_remaining):
        chosen.append(random.choice(unique_picks))

    random.shuffle(chosen)

    resolved = [
        {
            "name": entry["name"],
            "source": entry["source"],
            "library_path": resolve_library_path(entry["source"], machine_config),
        }
        for entry in chosen
    ]

    return {
        "assets": resolved,
        "total_count": total_count,
        "unique_count": len(unique_picks),
        "duplicate_count": duplicate_slots_remaining,
        "duplicate_cap_percent": duplicate_cap_percent,
        "fallback_triggered": fallback_triggered,
    }


def select_checkpoint(
    pools: dict,
    machine_config: dict,
    min_models: int,
    max_models: int,
    duplicate_cap_percent: float,
) -> dict:
    """
    Pick one fresh random selection: 1 HDRI, 1 floor texture, and a
    duplicate-capped random count of models. This is what runs once per
    checkpoint (e.g. every 10% of a render batch).
    """
    hdri = pick_single(pools["hdris"], machine_config)
    floor_texture = pick_single(pools["floor_textures"], machine_config)
    models_result = pick_models_with_duplicate_cap(
        pools["models"], machine_config, min_models, max_models, duplicate_cap_percent
    )

    return {
        "hdri": hdri,
        "floor_texture": floor_texture,
        "models": models_result["assets"],
        "models_meta": {
            "total_count": models_result["total_count"],
            "unique_count": models_result["unique_count"],
            "duplicate_count": models_result["duplicate_count"],
            "duplicate_cap_percent": models_result["duplicate_cap_percent"],
        },
        "fallback_triggered": models_result["fallback_triggered"],
    }


def classify_asset_type(info: dict):
    """Map Poly Haven's numeric info.json 'type' field to our pool bucket name.
    Returns None if the type is missing or unrecognised."""
    return TYPE_CODE_TO_POOL.get(info.get("type"))


def _asset_matches_allowlist(info: dict, category_allowlist) -> bool:
    if not category_allowlist:
        return True
    asset_categories = set(info.get("categories", []))
    return bool(asset_categories.intersection(category_allowlist))


def discover_poly_haven_assets(
    library_path: str, category_allowlist=None, log=print
) -> dict:
    """
    Scan every subfolder of a Poly Haven library folder, read each asset's
    info.json, and sort it into hdris/models/floor_textures based on its
    'type' field. If category_allowlist is non-empty, only assets whose
    info.json 'categories' intersects it are included.
    """
    discovered = {"hdris": [], "models": [], "floor_textures": []}

    if not os.path.isdir(library_path):
        log(f"[warning] Poly Haven library path not found: {library_path}")
        return discovered

    for entry_name in sorted(os.listdir(library_path)):
        asset_folder = os.path.join(library_path, entry_name)
        info_path = os.path.join(asset_folder, "info.json")
        if not os.path.isdir(asset_folder) or not os.path.isfile(info_path):
            continue

        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log(f"[warning] Could not read info.json for '{entry_name}': {e}")
            continue

        pool_name = classify_asset_type(info)
        if pool_name is None:
            continue

        if not _asset_matches_allowlist(info, category_allowlist):
            continue

        discovered[pool_name].append({"name": entry_name, "source": "poly_haven"})

    return discovered


def build_pools_for_mode(
    source_mode: str,
    machine_config: dict,
    curated_pools: dict,
    category_allowlist=None,
    log=print,
) -> dict:
    """
    Returns the pool dict actually used for selection this run.
      - 'curated': exactly what's in asset_pools.json (unchanged behaviour).
      - 'auto_discover': Poly Haven assets are scanned live from disk and
        category-filtered. Any 'manual' source entries already in
        asset_pools.json are kept alongside them, since manual assets have
        no info.json to auto-classify or auto-categorize from.
    """
    if source_mode == "curated":
        return curated_pools

    if source_mode != "auto_discover":
        log(
            f"[warning] Unknown asset_pipeline.source_mode '{source_mode}', falling back to 'curated'."
        )
        return curated_pools

    discovered = discover_poly_haven_assets(
        machine_config["poly_haven_library_path"], category_allowlist, log=log
    )

    merged = {"hdris": [], "models": [], "floor_textures": []}
    for pool_name in merged:
        manual_entries = [
            e for e in curated_pools.get(pool_name, []) if e.get("source") == "manual"
        ]
        merged[pool_name] = discovered[pool_name] + manual_entries

    return merged
