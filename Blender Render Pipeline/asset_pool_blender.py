"""
asset_pool_blender.py

Blender-side asset pool manager. Requires bpy - only import this from inside
a running Blender session (i.e. from render_pipeline.py).

Handles:
  - Loading the HDRI, floor texture, and a duplicate-capped random set of
    models for one "checkpoint" (a stretch of the render batch).
  - Cleanly unloading the previous checkpoint's assets before loading a new
    one, so memory use stays bounded to roughly one checkpoint's worth of
    assets rather than growing with the whole curated pool.
  - Per-render transform jitter (position/rotation for models, rotation/
    strength for the HDRI) so things still move even between checkpoints.

Asset folder convention this assumes (matches the Poly Haven Blender add-on's
library layout):
    <library_path>/<asset_name>/<asset_name>.blend
    <library_path>/<asset_name>/<asset_name>_<res>k.hdr   (HDRIs only)
    <library_path>/<asset_name>/<asset_name>_<res>k.exr   (HDRIs only, alt format)
"""

import glob
import math
import os
import random
import re

import bpy
from asset_pool_selection import build_pools_for_mode, select_checkpoint, validate_pools
from mathutils import Euler, Matrix, Vector


def rand_range(lo, hi):
    return random.uniform(lo, hi)


def rand_vec3(ranges: dict) -> Vector:
    return Vector(
        (rand_range(*ranges["x"]), rand_range(*ranges["y"]), rand_range(*ranges["z"]))
    )


def rand_euler_full() -> Euler:
    return Euler(
        (
            rand_range(0, 2 * math.pi),
            rand_range(0, 2 * math.pi),
            rand_range(0, 2 * math.pi),
        ),
        "XYZ",
    )


def _find_non_overlapping_xy(radius, existing, position_range, max_attempts=30):
    """
    Pure rejection-sampling search for an XY position that doesn't overlap
    any already-placed instance, approximating every instance as a circle
    (bounding-sphere-style, projected onto the ground plane). Returns
    (x, y, attempts_used) - if attempts_used == max_attempts, no clear spot
    was found and the last attempted position is returned anyway.
    """
    x = y = 0.0
    for attempt in range(max_attempts):
        x = rand_range(*position_range["x"])
        y = rand_range(*position_range["y"])
        if all(math.hypot(x - ex, y - ey) >= (radius + er) for ex, ey, er in existing):
            return x, y, attempt
    return x, y, max_attempts


# ---------------------------------------------------------------------------
#  HDRI world node graph (shared with the legacy flat-directory HDRI mode
#  in render_pipeline.py, so there's only one implementation of this)
# ---------------------------------------------------------------------------


def ensure_hdri_world_nodes(world):
    """
    Build (or reuse) the node graph needed to display an HDRI:
        Texture Coordinate -> Mapping -> Environment Texture -> Background -> World Output

    Returns the Environment Texture and Mapping nodes.
    """
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links

    env_node = nodes.get("HDRI_Environment")
    mapping_node = nodes.get("HDRI_Mapping")

    if env_node and mapping_node:
        return env_node, mapping_node

    nodes.clear()

    coord_node = nodes.new("ShaderNodeTexCoord")
    coord_node.location = (-800, 0)

    mapping_node = nodes.new("ShaderNodeMapping")
    mapping_node.name = "HDRI_Mapping"
    mapping_node.location = (-600, 0)

    env_node = nodes.new("ShaderNodeTexEnvironment")
    env_node.name = "HDRI_Environment"
    env_node.location = (-350, 0)

    bg_node = nodes.new("ShaderNodeBackground")
    bg_node.name = "Background"
    bg_node.location = (-100, 0)

    output_node = nodes.new("ShaderNodeOutputWorld")
    output_node.location = (150, 0)

    links.new(coord_node.outputs["Generated"], mapping_node.inputs["Vector"])
    links.new(mapping_node.outputs["Vector"], env_node.inputs["Vector"])
    links.new(env_node.outputs["Color"], bg_node.inputs["Color"])
    links.new(bg_node.outputs["Background"], output_node.inputs["Surface"])

    return env_node, mapping_node


def _find_highest_res_hdri_file(folder: str, asset_name: str):
    """Find the HDRI image file inside an asset folder, auto-detecting resolution.
    Poly Haven names these <asset_name>_<N>k.hdr or .exr - if more than one
    resolution was downloaded, picks the highest."""
    candidates = glob.glob(os.path.join(folder, f"{asset_name}_*k.hdr")) + glob.glob(
        os.path.join(folder, f"{asset_name}_*k.exr")
    )
    if not candidates:
        return None

    def res_key(path):
        m = re.search(r"_(\d+)k\.", os.path.basename(path))
        return int(m.group(1)) if m else 0

    candidates.sort(key=res_key, reverse=True)
    return candidates[0]


class AssetPoolManager:
    """
    Owns the currently-loaded checkpoint's assets and knows how to swap them
    out for a fresh random selection, plus jitter their transforms every
    render without changing which assets are loaded.
    """

    def __init__(
        self,
        machine_config: dict,
        asset_pools: dict,
        floor_object_name: str,
        min_models: int,
        max_models: int,
        duplicate_cap_percent: float,
        source_mode: str = "curated",
        category_allowlist=None,
        log=print,
    ):
        self.machine_config = machine_config
        self.asset_pools = asset_pools
        self.floor_object_name = floor_object_name
        self.min_models = min_models
        self.max_models = max_models
        self.duplicate_cap_percent = duplicate_cap_percent
        self.source_mode = source_mode
        self.category_allowlist = category_allowlist or []
        self.log = log

        self.current_selection = None
        self.model_roots = []  # list of bpy Empty objects, one per model instance
        self.model_instance_names = []  # parallel list: source asset name for each root

    # -- public API ---------------------------------------------------------

    def refresh(self) -> dict:
        """Unload the previous checkpoint's assets and load a fresh random selection."""
        self._unload_current()

        pools = build_pools_for_mode(
            self.source_mode,
            self.machine_config,
            self.asset_pools,
            self.category_allowlist,
            log=self.log,
        )
        validate_pools(pools)

        selection = select_checkpoint(
            pools,
            self.machine_config,
            self.min_models,
            self.max_models,
            self.duplicate_cap_percent,
        )
        self.current_selection = selection

        if selection["fallback_triggered"]:
            self.log(
                "[warning] Models pool smaller than needed for the duplicate cap - "
                "allowed more repeats than the cap to fill the count."
            )

        self._load_hdri(selection["hdri"])
        self._load_floor_texture(selection["floor_texture"])
        instances = self._load_models(selection["models"])
        self.model_instance_names = [name for name, _ in instances]
        self.model_roots = [root for _, root in instances]

        return selection

    def randomize_model_transforms(
        self, position_range: dict, max_placement_attempts: int = 30
    ):
        """
        Reposition/reorient the currently loaded models every render:
          - random rotation per instance
          - ground-snapped so nothing floats above or clips through the floor
          - roughly non-overlapping, via a fast bounding-sphere approximation

        Returns a list of per-instance placement records (name, final
        location, final rotation) - this is the exact data needed to
        reconstruct this specific render's object placement without having
        to replay the RNG stream.
        """
        if not self.model_roots:
            return []

        floor_z = self._floor_top_z()
        placed = []  # (x, y, radius) of instances already placed this render
        report = []

        for name, root in zip(self.model_instance_names, self.model_roots):
            rotation = rand_euler_full()
            rotation_matrix = rotation.to_matrix()

            # Corners in root-local space, ignoring root's own transform entirely.
            root_space_corners = self._hierarchy_corners_in_root_space(root)
            if not root_space_corners:
                # No mesh geometry found under this root (shouldn't normally
                # happen) - fall back to the old purely-random placement.
                root.rotation_euler = rotation
                root.location = rand_vec3(position_range)
                report.append(self._placement_record(name, root))
                continue

            # Apply the rotation we're about to give the object BEFORE measuring -
            # otherwise a tumbled object's true lowest point (and true footprint)
            # gets missed entirely, causing floor clipping/floating.
            rotated_corners = [rotation_matrix @ c for c in root_space_corners]

            xs = [c.x for c in rotated_corners]
            ys = [c.y for c in rotated_corners]
            zs = [c.z for c in rotated_corners]
            radius = max(max(xs) - min(xs), max(ys) - min(ys)) / 2.0
            min_z_local = min(zs)

            x, y, attempts_used = _find_non_overlapping_xy(
                radius, placed, position_range, max_placement_attempts
            )
            if attempts_used >= max_placement_attempts:
                self.log(
                    f"[warning] Could not find a non-overlapping spot for '{root.name}' "
                    f"after {max_placement_attempts} attempts - placing anyway."
                )

            root.rotation_euler = rotation
            root.location = Vector((x, y, floor_z - min_z_local))
            placed.append((x, y, radius))
            report.append(self._placement_record(name, root))

        return report

    def _placement_record(self, name: str, root) -> dict:
        return {
            "name": name,
            "location": [round(v, 4) for v in root.location],
            "rotation_deg": [round(math.degrees(v), 2) for v in root.rotation_euler],
        }

    def randomize_hdri_transform(self, light_cfg: dict) -> dict:
        """Jitter HDRI rotation + strength without swapping which HDRI is loaded.
        Call every render. Returns the actual values applied, for provenance."""
        result = {"rotation_z_deg": None, "strength": None}

        world = bpy.context.scene.world
        if not world or not world.use_nodes:
            return result

        mapping_node = world.node_tree.nodes.get("HDRI_Mapping")
        if mapping_node:
            rot_z = rand_range(0, 2 * math.pi)
            mapping_node.inputs["Rotation"].default_value = (0, 0, rot_z)
            result["rotation_z_deg"] = round(math.degrees(rot_z), 2)

        bg_node = world.node_tree.nodes.get("Background")
        if bg_node:
            strength = rand_range(*light_cfg.get("ambient_strength_range", [0.5, 1.5]))
            bg_node.inputs["Strength"].default_value = strength
            result["strength"] = round(strength, 3)

        return result

    # -- geometry helpers (ground-snapping / overlap avoidance) -------------

    def _hierarchy_mesh_objects(self, root):
        """All mesh objects under root (root itself is an Empty, excluded)."""
        objs = []

        def walk(o):
            if getattr(o, "data", None) is not None:
                objs.append(o)
            for child in o.children:
                walk(child)

        walk(root)
        return objs

    def _local_to_root_matrix(self, obj, root):
        """Matrix mapping obj's local space into root's local space, ignoring
        root's own current location/rotation entirely (as if root sat at the
        world origin with no rotation). This only depends on matrix_local
        (location/rotation/scale relative to each object's own parent),
        which needs no dependency-graph update to be correct - unlike
        matrix_world, it's safe to read immediately after re-parenting."""
        matrix = Matrix.Identity(4)
        current = obj
        while current is not None and current != root:
            matrix = current.matrix_local @ matrix
            current = current.parent
        return matrix

    def _hierarchy_corners_in_root_space(self, root):
        """All 8-corner bounding-box points of every mesh in the hierarchy,
        expressed in root-local space - i.e. independent of wherever root
        is actually currently placed/rotated."""
        corners = []
        for obj in self._hierarchy_mesh_objects(root):
            local_to_root = self._local_to_root_matrix(obj, root)
            for corner in obj.bound_box:
                corners.append(local_to_root @ Vector(corner))
        return corners

    def _floor_top_z(self):
        floor_obj = bpy.data.objects.get(self.floor_object_name)
        if floor_obj is None or not getattr(floor_obj, "bound_box", None):
            return 0.0
        world_corners = [
            floor_obj.matrix_world @ Vector(c) for c in floor_obj.bound_box
        ]
        return max(c.z for c in world_corners)

    # -- internals ------------------------------------------------------------

    def _asset_folder(self, entry: dict) -> str:
        if entry.get("source") == "manual":
            # Manual assets sit flat in the library folder: <library_path>/<name>.blend
            return entry["library_path"]
        # Poly Haven's add-on convention: <library_path>/<name>/<name>.blend
        return os.path.join(entry["library_path"], entry["name"])

    def _blend_path(self, entry: dict) -> str:
        return os.path.join(self._asset_folder(entry), f"{entry['name']}.blend")

    def _load_hdri(self, entry: dict):
        folder = self._asset_folder(entry)
        file_path = _find_highest_res_hdri_file(folder, entry["name"])
        if not file_path:
            self.log(
                f"[warning] No .hdr/.exr file found for HDRI asset '{entry['name']}' in {folder}"
            )
            return

        world = bpy.context.scene.world
        if world is None:
            world = bpy.data.worlds.new("World")
            bpy.context.scene.world = world

        env_node, _ = ensure_hdri_world_nodes(world)

        existing = bpy.data.images.get(os.path.basename(file_path))
        if existing:
            env_node.image = existing
            return

        try:
            env_node.image = bpy.data.images.load(file_path, check_existing=True)
        except RuntimeError as e:
            self.log(f"[warning] Could not read HDRI file '{file_path}': {e}")

    def _load_floor_texture(self, entry: dict):
        blend_path = self._blend_path(entry)
        if not os.path.exists(blend_path):
            self.log(f"[warning] Floor texture .blend not found: {blend_path}")
            return

        try:
            with bpy.data.libraries.load(blend_path, link=False) as (
                data_from,
                data_to,
            ):
                if entry["name"] in data_from.materials:
                    data_to.materials = [entry["name"]]
                elif data_from.materials:
                    data_to.materials = [data_from.materials[0]]
                else:
                    data_to.materials = []
        except OSError as e:
            self.log(
                f"[warning] Could not read floor texture .blend '{blend_path}': {e}"
            )
            return

        if not data_to.materials:
            self.log(f"[warning] No material found in {blend_path}")
            return

        mat = data_to.materials[0]
        floor_obj = bpy.data.objects.get(self.floor_object_name)
        if floor_obj is None:
            self.log(
                f"[warning] Floor object '{self.floor_object_name}' not found in scene - texture not applied."
            )
            return

        if floor_obj.data.materials:
            floor_obj.data.materials[0] = mat
        else:
            floor_obj.data.materials.append(mat)

    def _import_master_objects(self, entry: dict):
        """Append all top-level objects from an asset's .blend, unlinked from the scene.
        These act as the shared source for one or more instance copies."""
        blend_path = self._blend_path(entry)
        if not os.path.exists(blend_path):
            self.log(f"[warning] Model .blend not found: {blend_path}")
            return []

        try:
            with bpy.data.libraries.load(blend_path, link=False) as (
                data_from,
                data_to,
            ):
                data_to.objects = data_from.objects
        except OSError as e:
            self.log(f"[warning] Could not read model .blend '{blend_path}': {e}")
            return []

        return [obj for obj in data_to.objects if obj is not None]

    def _copy_hierarchy(self, source_obj, new_parent, collection):
        """Recursively copy an object and its children, sharing mesh/material data
        (cheap - only object-level duplication, not geometry duplication)."""
        obj_copy = source_obj.copy()
        obj_copy.parent = new_parent
        collection.objects.link(obj_copy)
        for child in source_obj.children:
            self._copy_hierarchy(child, obj_copy, collection)
        return obj_copy

    def _load_models(self, entries: list) -> list:
        """Returns a list of (asset_name, root_empty) pairs - paired explicitly
        rather than relying on list-index alignment, since an entry can be
        skipped entirely (missing/corrupt .blend) without leaving a gap."""
        collection = bpy.context.scene.collection
        master_cache = {}
        instances = []

        for i, entry in enumerate(entries):
            name = entry["name"]
            if name not in master_cache:
                master_cache[name] = self._import_master_objects(entry)

            masters = master_cache[name]
            if not masters:
                continue

            empty = bpy.data.objects.new(f"PoolModel_{i:02d}_{name}", None)
            collection.objects.link(empty)

            for master_obj in masters:
                if master_obj.parent is None:  # only walk from top-level objects
                    self._copy_hierarchy(master_obj, empty, collection)

            instances.append((name, empty))

        # Master objects are only needed as a copy source - remove them now that
        # copies exist and reference the same mesh/material data. The data
        # itself survives because the copies still hold references to it.
        for masters in master_cache.values():
            for master_obj in masters:
                bpy.data.objects.remove(master_obj, do_unlink=True)

        return instances

    def _unload_current(self):
        for root in self.model_roots:
            for child in list(root.children):
                bpy.data.objects.remove(child, do_unlink=True)
            bpy.data.objects.remove(root, do_unlink=True)
        self.model_roots = []
        self.model_instance_names = []

        # Reclaim mesh/material/image/world data that's no longer referenced
        # by anything (previous checkpoint's models, floor material, HDRI image).
        bpy.data.orphans_purge(do_local_ids=True, do_recursive=True)
