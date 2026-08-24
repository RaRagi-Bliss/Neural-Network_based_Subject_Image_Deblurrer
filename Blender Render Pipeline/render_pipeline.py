"""
=============================================================================
  Blender Training Data Rendering Pipeline
  Capstone Project — Artifact 2
  Victoria University of Wellington — SWEN489
  Author: Luigi Ibanez

  Generates training data as one ground truth + N blur variants:
    <index>_sharp.png        — Ground truth: no motion, no DOF, fully in focus
    <index>_blur_00.png      — Blur variant 0: random motion + focus distance
    <index>_blur_01.png      — Blur variant 1: different motion + focus
    <index>_blur_NN.png      — ... up to pipeline.blur_shots_per_sharp
    manifest.json             — Metadata for every ground truth + its variants

  Each blur variant independently randomises: motion direction/amount,
  shutter speed, and (if camera.depth_of_field.enabled) aperture and focus
  distance — including a slight focus "rack" during the simulated exposure.

  Run headlessly:
    blender --background [scene.blend] --python render_pipeline.py -- [--config config.json] [--count 500]

  Run with a GUI (for testing):
    blender [scene.blend] --python render_pipeline.py

  If no .blend is given, a simple procedural test scene is created.
=============================================================================
"""

import argparse
import json
import math
import os
import random
import sys
import time
from datetime import datetime

import bpy
from mathutils import Euler, Vector

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def log(msg: str):
    """Timestamped print that flushes immediately (important for headless runs)."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def clamp(val, lo, hi):
    return max(lo, min(hi, val))


def rand_range(lo, hi):
    return random.uniform(lo, hi)


def rand_vec3(ranges: dict) -> Vector:
    return Vector(
        (
            rand_range(*ranges["x"]),
            rand_range(*ranges["y"]),
            rand_range(*ranges["z"]),
        )
    )


# ---------------------------------------------------------------------------
#  Scene Setup
# ---------------------------------------------------------------------------


def configure_render_engine(scene, cfg: dict):
    """Set Cycles as render engine and try to use GPU."""
    engine = cfg["blender"].get("engine", "CYCLES")
    scene.render.engine = engine

    if engine == "CYCLES":
        prefs = bpy.context.preferences.addons["cycles"].preferences
        device_pref = cfg["blender"].get("device", "GPU")

        if device_pref == "CPU":
            # Explicit CPU request — skip GPU device probing entirely.
            scene.cycles.device = "CPU"
            log("Render device: CPU (requested via config)")
        else:
            try:
                prefs.compute_device_type = "CUDA"
                prefs.get_devices()
                for d in prefs.devices:
                    d.use = True
                scene.cycles.device = "GPU"
                log("Render device: GPU (CUDA)")
            except Exception:
                try:
                    prefs.compute_device_type = "METAL"
                    prefs.get_devices()
                    for d in prefs.devices:
                        d.use = True
                    scene.cycles.device = "GPU"
                    log("Render device: GPU (Metal)")
                except Exception:
                    if cfg["blender"].get("fallback_to_cpu", True):
                        scene.cycles.device = "CPU"
                        log("Render device: CPU (fallback)")
                    else:
                        raise RuntimeError(
                            "GPU not available and fallback_to_cpu is false."
                        )
        scene.cycles.samples = cfg["output"].get("samples", 128)


def configure_output(scene, cfg: dict):
    """Set resolution, format, and output path."""
    scene.render.image_settings.file_format = cfg["output"].get("image_format", "PNG")
    scene.render.image_settings.color_depth = cfg["output"].get("color_depth", "8")
    scene.render.resolution_x = cfg["output"].get("resolution_x", 1280)
    scene.render.resolution_y = cfg["output"].get("resolution_y", 720)
    scene.render.resolution_percentage = 100


def build_procedural_scene(cfg: dict = None):
    """
    Create a minimal test scene if no .blend file is provided.
    Replace / extend this with your own 3D assets.
    """
    log("Building procedural test scene...")

    # Clear defaults
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    # Ground plane
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = "Ground"
    mat_ground = bpy.data.materials.new("Ground_Mat")
    mat_ground.use_nodes = True
    mat_ground.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        0.4,
        0.55,
        0.3,
        1.0,
    )
    ground.data.materials.append(mat_ground)

    # A few simple objects as stand-ins — swap for your real assets
    primitives = [
        (
            "Cube",
            bpy.ops.mesh.primitive_cube_add,
            {"size": 1.0, "location": (0, 0, 0.5)},
        ),
        (
            "Sphere",
            bpy.ops.mesh.primitive_uv_sphere_add,
            {"radius": 0.6, "location": (2, 1, 0.6)},
        ),
        (
            "Cylinder",
            bpy.ops.mesh.primitive_cylinder_add,
            {"radius": 0.4, "depth": 1.4, "location": (-2, -1, 0.7)},
        ),
        (
            "Torus",
            bpy.ops.mesh.primitive_torus_add,
            {"major_radius": 0.5, "minor_radius": 0.15, "location": (2, -2, 0.5)},
        ),
        (
            "Cone",
            bpy.ops.mesh.primitive_cone_add,
            {"radius1": 0.5, "depth": 1.2, "location": (-2, 2, 0.6)},
        ),
    ]

    for name, op, kwargs in primitives:
        op(**kwargs)
        obj = bpy.context.active_object
        obj.name = name
        mat = bpy.data.materials.new(f"{name}_Mat")
        mat.use_nodes = True
        r, g, b = random.random(), random.random(), random.random()
        mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
            r,
            g,
            b,
            1.0,
        )
        obj.data.materials.append(mat)

    # Sun light
    bpy.ops.object.light_add(type="SUN", location=(5, 5, 10))
    sun = bpy.context.active_object
    sun.name = "Sun"
    sun.data.energy = 5.0

    # Camera
    bpy.ops.object.camera_add(location=(0, -6, 3))
    camera = bpy.context.active_object
    camera.name = "Camera"
    camera.rotation_euler = Euler((math.radians(60), 0, 0), "XYZ")
    bpy.context.scene.camera = camera

    # World background
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    bg_node = world.node_tree.nodes.get("Background")
    if bg_node:
        bg_node.inputs["Color"].default_value = (0.5, 0.7, 1.0, 1.0)
        bg_node.inputs["Strength"].default_value = 0.3

    log("Procedural scene ready.")

    # If this is the built-in test scene and the config hasn't specified
    # which objects to randomise, default to randomising all the primitives
    # we just created — otherwise scene_objects.randomise_positions being
    # empty means every render has objects sitting in identical spots.
    if cfg is not None:
        obj_cfg = cfg.setdefault("scene_objects", {})
        primitive_names = [name for name, _, _ in primitives]
        if not obj_cfg.get("randomise_positions"):
            obj_cfg["randomise_positions"] = primitive_names
            log(f"  Auto-registered for position randomisation: {primitive_names}")
        if not obj_cfg.get("randomise_rotations"):
            obj_cfg["randomise_rotations"] = primitive_names


# ---------------------------------------------------------------------------
#  Randomisation
# ---------------------------------------------------------------------------


def randomise_camera(camera_obj, cfg: dict):
    """Randomise camera position, aim, and focal length."""
    cam_cfg = cfg["camera"]

    # Position
    loc = rand_vec3(cam_cfg["location_range"])
    camera_obj.location = loc

    # Point at scene centre with a small jitter
    jitter = cam_cfg.get("look_at_jitter", 0.3)
    target = Vector(
        (
            rand_range(-jitter, jitter),
            rand_range(-jitter, jitter),
            rand_range(0, jitter),
        )
    )
    direction = target - loc
    rot_quat = direction.to_track_quat("-Z", "Y")
    camera_obj.rotation_euler = rot_quat.to_euler()

    # Focal length
    lens_range = cam_cfg.get("lens_mm", [24, 85])
    camera_obj.data.lens = rand_range(*lens_range)


def randomise_lighting(cfg: dict):
    """
    Randomise scene lighting.

    Two mutually exclusive modes, controlled by lighting.randomise_hdri:
      - False (default): procedural sun + flat coloured sky (as before)
      - True: swaps in a random HDRI from lighting.hdri_directory each render,
              which also drives realistic reflections/ambient light, not just
              a flat colour. This is what you'll use once Poly Haven HDRIs
              are in play.
    """
    light_cfg = cfg["lighting"]

    if light_cfg.get("randomise_hdri", False):
        apply_random_hdri(light_cfg)
    else:
        # Procedural sun light
        sun = bpy.data.objects.get("Sun")
        if sun and sun.type == "LIGHT":
            sun.data.energy = rand_range(*light_cfg["sun_energy_range"])
            rot_deg = rand_range(*light_cfg["sun_rotation_range_deg"])
            sun.rotation_euler = Euler(
                (
                    math.radians(rand_range(20, 80)),
                    0,
                    math.radians(rot_deg),
                ),
                "XYZ",
            )

        # Flat coloured sky, ambient strength only
        world = bpy.context.scene.world
        if world and world.use_nodes:
            bg = world.node_tree.nodes.get("Background")
            if bg:
                strength = rand_range(*light_cfg["ambient_strength_range"])
                bg.inputs["Strength"].default_value = strength


def get_hdri_file_list(hdri_dir: str) -> list:
    """Return all .hdr/.exr files in the given directory."""
    if not hdri_dir or not os.path.isdir(hdri_dir):
        return []
    valid_ext = (".hdr", ".exr")
    return [
        os.path.join(hdri_dir, f)
        for f in os.listdir(hdri_dir)
        if f.lower().endswith(valid_ext)
    ]


def ensure_hdri_world_nodes(world):
    """
    Build (or reuse) the node graph needed to display an HDRI:
        Texture Coordinate -> Mapping -> Environment Texture -> Background -> World Output

    Returns the Environment Texture and Mapping nodes so callers can set the
    image and rotation without re-searching the node tree every time.
    """
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links

    env_node = nodes.get("HDRI_Environment")
    mapping_node = nodes.get("HDRI_Mapping")

    if env_node and mapping_node:
        # Already built on a previous call — just return existing nodes.
        return env_node, mapping_node

    # Clear existing nodes and rebuild cleanly.
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


def apply_random_hdri(light_cfg: dict):
    """
    Pick a random HDRI file from lighting.hdri_directory, load it (or reuse
    it if already loaded this session), and assign it to the world.
    Also randomises the HDRI's rotation around Z so the same file doesn't
    always light the scene from the same direction.
    """
    hdri_dir = light_cfg.get("hdri_directory", "")
    hdri_files = get_hdri_file_list(hdri_dir)

    if not hdri_files:
        log(
            f"WARNING: randomise_hdri is true but no .hdr/.exr files found "
            f"in '{hdri_dir}'. Falling back to flat sky for this render."
        )
        return

    chosen_path = random.choice(hdri_files)

    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world

    env_node, mapping_node = ensure_hdri_world_nodes(world)

    # Reuse the image if Blender already has it loaded (avoids re-reading
    # multi-MB HDR files from disk every single render).
    existing = bpy.data.images.get(os.path.basename(chosen_path))
    if existing:
        env_node.image = existing
    else:
        env_node.image = bpy.data.images.load(chosen_path, check_existing=True)

    # Randomise rotation so lighting direction varies even on repeat files
    rot_z = rand_range(0, 2 * math.pi)
    mapping_node.inputs["Rotation"].default_value = (0, 0, rot_z)

    # Optional overall strength jitter, reusing the ambient range from config
    bg_node = world.node_tree.nodes.get("Background")
    if bg_node:
        strength = rand_range(*light_cfg.get("ambient_strength_range", [0.5, 1.5]))
        bg_node.inputs["Strength"].default_value = strength


def randomise_scene_objects(cfg: dict):
    """Randomise positions/rotations of named objects in the scene."""
    obj_cfg = cfg["scene_objects"]
    pos_range = obj_cfg.get("position_range", {"x": [-2, 2], "y": [-2, 2], "z": [0, 0]})

    for name in obj_cfg.get("randomise_positions", []):
        obj = bpy.data.objects.get(name)
        if obj:
            obj.location = rand_vec3(pos_range)

    for name in obj_cfg.get("randomise_rotations", []):
        obj = bpy.data.objects.get(name)
        if obj:
            obj.rotation_euler = Euler(
                (
                    math.radians(rand_range(0, 360)),
                    math.radians(rand_range(0, 360)),
                    math.radians(rand_range(0, 360)),
                ),
                "XYZ",
            )


# ---------------------------------------------------------------------------
#  Motion Blur
# ---------------------------------------------------------------------------


def apply_camera_motion(camera_obj, cfg: dict) -> dict:
    """
    Keyframe the camera at frame 1 (base), apply a random motion delta,
    then keyframe it at frame 2. Blender's motion blur system samples
    between these frames to produce the blur effect.

    Returns metadata about the motion applied.
    """
    cam_cfg = cfg["camera"]["motion"]
    motion_types = cam_cfg.get("motion_types", ["translate", "rotate", "combined"])
    motion_type = random.choice(motion_types)

    # Store original transform
    orig_loc = camera_obj.location.copy()
    orig_rot = camera_obj.rotation_euler.copy()

    # --- Frame 1: starting position ---
    scene = bpy.context.scene
    scene.frame_set(1)
    camera_obj.keyframe_insert(data_path="location", frame=1)
    camera_obj.keyframe_insert(data_path="rotation_euler", frame=1)

    # --- Compute delta ---
    translate_amount = rand_range(*cam_cfg["translate_range"])
    rotate_amount_deg = rand_range(*cam_cfg["rotate_range_deg"])

    delta_loc = Vector((0, 0, 0))
    delta_rot = Euler((0, 0, 0), "XYZ")

    if motion_type in ("translate", "combined"):
        # Random direction in camera local space, converted to world
        local_dir = (
            Vector(
                (
                    rand_range(-1, 1),
                    rand_range(-1, 1),
                    rand_range(-0.3, 0.3),
                )
            ).normalized()
            * translate_amount
        )
        world_dir = camera_obj.matrix_world.to_3x3() @ local_dir
        delta_loc = world_dir

    if motion_type in ("rotate", "combined"):
        axis = random.choice(["x", "y", "z"])
        angle = math.radians(rand_range(-rotate_amount_deg, rotate_amount_deg))
        delta_rot = Euler(
            (
                angle if axis == "x" else 0,
                angle if axis == "y" else 0,
                angle if axis == "z" else 0,
            ),
            "XYZ",
        )

    # --- Frame 2: end position (after motion) ---
    camera_obj.location = orig_loc + delta_loc
    camera_obj.rotation_euler = Euler(
        (
            orig_rot.x + delta_rot.x,
            orig_rot.y + delta_rot.y,
            orig_rot.z + delta_rot.z,
        ),
        "XYZ",
    )
    camera_obj.keyframe_insert(data_path="location", frame=2)
    camera_obj.keyframe_insert(data_path="rotation_euler", frame=2)

    # Set current frame to 1 for rendering (blurs between frame 1→2)
    scene.frame_set(1)

    return {
        "motion_type": motion_type,
        "translate_magnitude": round(translate_amount, 4),
        "rotate_magnitude_deg": round(rotate_amount_deg, 2),
        "delta_location": list(round(v, 4) for v in delta_loc),
    }


def clear_camera_animation(camera_obj):
    """Remove all keyframes from the camera — both its transform (object)
    and its lens/DOF properties (camera data), which are animated separately."""
    if camera_obj.animation_data:
        camera_obj.animation_data_clear()
    if camera_obj.data.animation_data:
        camera_obj.data.animation_data_clear()


def disable_dof(camera_obj):
    """Turn off depth of field — used for the sharp ground-truth render."""
    camera_obj.data.dof.use_dof = False


def apply_focus_stack(camera_obj, cfg: dict) -> dict:
    """
    Set up a randomised focus distance for this blur shot, and — matching
    the same frame1→frame2 keyframe approach used for camera motion — rack
    the focus distance slightly during the "exposure" so focus blur and
    motion blur combine, like a lens breathing/racking mid-shot.

    Returns metadata about the DOF settings used.
    """
    dof_cfg = cfg["camera"].get("depth_of_field", {})
    if not dof_cfg.get("enabled", False):
        camera_obj.data.dof.use_dof = False
        return {"enabled": False}

    scene = bpy.context.scene
    dof = camera_obj.data.dof
    dof.use_dof = True

    # Aperture — lower f-stop = shallower depth of field, stronger bokeh
    fstop_range = dof_cfg.get("aperture_fstop_range", [1.4, 8.0])
    dof.aperture_fstop = rand_range(*fstop_range)

    # Base focus distance for this shot
    focus_range = dof_cfg.get("focus_distance_range", [1.0, 8.0])
    base_focus = rand_range(*focus_range)

    # Frame 1: starting focus distance
    scene.frame_set(1)
    dof.focus_distance = base_focus
    dof.keyframe_insert(data_path="focus_distance", frame=1)

    # Frame 2: focus racks by a random delta during the "exposure",
    # simulating a focus pull/breathing happening as the shutter is open.
    rack_range = dof_cfg.get("focus_rack_range", [0.0, 0.0])
    rack_delta = rand_range(*rack_range) * random.choice([-1, 1])
    end_focus = max(0.1, base_focus + rack_delta)
    dof.focus_distance = end_focus
    dof.keyframe_insert(data_path="focus_distance", frame=2)

    scene.frame_set(1)

    return {
        "enabled": True,
        "aperture_fstop": round(dof.aperture_fstop, 2),
        "focus_distance_start": round(base_focus, 3),
        "focus_distance_end": round(end_focus, 3),
        "focus_rack_delta": round(rack_delta, 3),
    }


def enable_motion_blur(scene, cfg: dict) -> float:
    """Enable motion blur and return the shutter speed used."""
    mb_cfg = cfg["motion_blur"]
    scene.render.use_motion_blur = True

    if mb_cfg.get("randomise_shutter", True):
        shutter = rand_range(*mb_cfg["shutter_range"])
    else:
        shutter = mb_cfg.get("shutter_speed", 0.5)

    scene.render.motion_blur_shutter = shutter

    if scene.render.engine == "CYCLES":
        scene.cycles.motion_blur_position = "START"

    return shutter


def disable_motion_blur(scene):
    """Disable motion blur for the sharp render."""
    scene.render.use_motion_blur = False


# ---------------------------------------------------------------------------
#  Render
# ---------------------------------------------------------------------------


def render_to_file(filepath: str):
    """Render the current scene to a file."""
    bpy.context.scene.render.filepath = filepath
    bpy.ops.render.render(write_still=True)


# ---------------------------------------------------------------------------
#  Main Pipeline
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """
    Parse args passed after '--' on the Blender command line.
    e.g.: blender --background scene.blend --python render_pipeline.py -- --config config.json --count 100
    """
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []

    parser = argparse.ArgumentParser(description="Blender Training Data Pipeline")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument(
        "--count", type=int, default=None, help="Override num_renders from config"
    )
    parser.add_argument(
        "--outdir", default=None, help="Override output directory from config"
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Override random seed from config"
    )
    return parser.parse_args(argv)


def run_pipeline():
    args = parse_args()

    # Load config
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), config_path
        )

    if not os.path.exists(config_path):
        log(f"Config not found at {config_path} — using built-in defaults.")
        cfg = {
            "output": {
                "directory": "./output",
                "image_format": "PNG",
                "color_depth": "8",
                "resolution_x": 1280,
                "resolution_y": 720,
                "samples": 64,
            },
            "pipeline": {
                "num_renders": 10,
                "start_index": 0,
                "seed": None,
                "blur_shots_per_sharp": 3,
                "log_every": 5,
            },
            "camera": {
                "lens_mm": [24, 85],
                "location_range": {
                    "x": [-5.0, 5.0],
                    "y": [-8.0, -2.0],
                    "z": [0.5, 4.0],
                },
                "look_at_jitter": 0.3,
                "motion": {
                    "translate_range": [0.03, 1.2],
                    "rotate_range_deg": [0.5, 18.0],
                    "motion_types": ["translate", "rotate", "combined"],
                },
                "depth_of_field": {
                    "enabled": True,
                    "aperture_fstop_range": [1.4, 8.0],
                    "focus_distance_range": [1.0, 8.0],
                    "focus_rack_range": [0.0, 1.0],
                },
            },
            "motion_blur": {
                "shutter_speed": 0.5,
                "shutter_range": [0.3, 1.0],
                "randomise_shutter": True,
                "steps": 2,
            },
            "lighting": {
                "sun_energy_range": [1.0, 10.0],
                "sun_rotation_range_deg": [0, 360],
                "ambient_strength_range": [0.1, 1.0],
                "randomise_hdri": False,
                "hdri_directory": "./hdri",
            },
            "scene_objects": {
                "randomise_positions": [],
                "randomise_rotations": [],
                "position_range": {"x": [-2, 2], "y": [-2, 2], "z": [0, 0]},
            },
            "blender": {
                "engine": "CYCLES",
                "device": "GPU",
                "fallback_to_cpu": True,
                "scene_file": "",
            },
        }
    else:
        cfg = load_config(config_path)

    # Apply CLI overrides
    if args.count is not None:
        cfg["pipeline"]["num_renders"] = args.count
    if args.outdir is not None:
        cfg["output"]["directory"] = args.outdir
    if args.seed is not None:
        cfg["pipeline"]["seed"] = args.seed

    # Seed RNG
    # A fixed integer seed reproduces the exact same "random" sequence every
    # run — useful for debugging, but it's why you were seeing identical
    # output every time. Setting "seed": null in config.json (the new
    # default) makes each run genuinely different, while still logging
    # whatever seed got used so a specific run can be reproduced later if
    # you ever need to by passing --seed <that number>.
    seed = cfg["pipeline"].get("seed", None)
    if seed is None:
        seed = int(time.time() * 1000) % (2**31)
        log(f"No fixed seed set — using time-based seed: {seed}")
    else:
        log(f"Using fixed seed from config: {seed}")
    random.seed(seed)

    # Output directory
    out_dir = cfg["output"]["directory"]
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), out_dir)
    os.makedirs(out_dir, exist_ok=True)
    log(f"Output directory: {out_dir}")

    # Scene: load .blend or build procedural
    scene_file = cfg["blender"].get("scene_file", "")
    if scene_file and os.path.exists(scene_file):
        bpy.ops.wm.open_mainfile(filepath=scene_file)
        log(f"Loaded scene: {scene_file}")
    else:
        build_procedural_scene(cfg)

    scene = bpy.context.scene

    # Configure render engine and output settings
    configure_render_engine(scene, cfg)
    configure_output(scene, cfg)

    # Get camera
    camera_obj = scene.camera
    if camera_obj is None:
        raise RuntimeError(
            "No active camera found in the scene. "
            "Add a camera or ensure the .blend has one set as active."
        )

    num_renders = cfg["pipeline"]["num_renders"]
    start_index = cfg["pipeline"].get("start_index", 0)
    log_every = cfg["pipeline"].get("log_every", 10)
    blur_per_sharp = cfg["pipeline"].get("blur_shots_per_sharp", 1)

    manifest = []
    pipeline_start = time.time()
    total_blur_renders = num_renders * blur_per_sharp

    log(
        f"Starting pipeline: {num_renders} ground truths x "
        f"{blur_per_sharp} blur shot(s) each = {total_blur_renders} blur renders"
    )
    log("=" * 60)

    render_count = 0

    for i in range(num_renders):
        idx = start_index + i
        idx_str = f"{idx:05d}"

        iter_start = time.time()

        # ── Randomise scene (once per ground truth) ────────────────────────
        randomise_camera(camera_obj, cfg)
        randomise_lighting(cfg)
        randomise_scene_objects(cfg)

        # Snapshot camera state for metadata
        cam_location = list(round(v, 4) for v in camera_obj.location)
        cam_rotation = list(
            round(math.degrees(v), 2) for v in camera_obj.rotation_euler
        )
        cam_lens = round(camera_obj.data.lens, 1)

        # ── SHARP render — fully in focus, no motion, ground truth ─────────
        clear_camera_animation(camera_obj)
        disable_motion_blur(scene)
        disable_dof(camera_obj)
        scene.frame_set(1)

        sharp_path = os.path.join(out_dir, f"{idx_str}_sharp.png")
        render_to_file(sharp_path)

        # ── BLUR shots — same base camera pose, N motion+focus variants ────
        blur_variants = []
        for b in range(blur_per_sharp):
            motion_meta = apply_camera_motion(camera_obj, cfg)
            shutter_used = enable_motion_blur(scene, cfg)
            dof_meta = apply_focus_stack(camera_obj, cfg)

            blur_filename = f"{idx_str}_blur_{b:02d}.png"
            blur_path = os.path.join(out_dir, blur_filename)
            render_to_file(blur_path)

            blur_variants.append(
                {
                    "blur_file": blur_filename,
                    "blur_index": b,
                    "motion_blur": {
                        **motion_meta,
                        "shutter_speed": round(shutter_used, 3),
                    },
                    "depth_of_field": dof_meta,
                }
            )

            # Reset transform/DOF keyframes before the next variant, but
            # keep camera base position (only the motion/focus randomises).
            clear_camera_animation(camera_obj)
            disable_motion_blur(scene)
            camera_obj.location = Vector(cam_location)
            camera_obj.rotation_euler = Euler(
                (
                    math.radians(cam_rotation[0]),
                    math.radians(cam_rotation[1]),
                    math.radians(cam_rotation[2]),
                ),
                "XYZ",
            )

            render_count += 1

        iter_elapsed = round(time.time() - iter_start, 1)

        # ── Manifest entry ───────────────────────────────────────────────
        entry = {
            "index": idx,
            "sharp_file": f"{idx_str}_sharp.png",
            "blur_variants": blur_variants,
            "camera": {
                "location": cam_location,
                "rotation_deg": cam_rotation,
                "lens_mm": cam_lens,
            },
            "render_time_s": iter_elapsed,
            "seed_offset": i,
        }
        manifest.append(entry)

        if (i + 1) % log_every == 0 or i == 0:
            elapsed_total = time.time() - pipeline_start
            avg_time = elapsed_total / (i + 1)
            remaining = avg_time * (num_renders - i - 1)
            log(
                f"  [{i+1:>5}/{num_renders}]  ground truth {idx_str}  "
                f"({blur_per_sharp} blur shots, {iter_elapsed}s total | "
                f"~{remaining/60:.1f} min remaining)"
            )

    # ── Write manifest ────────────────────────────────────────────────────
    manifest_path = os.path.join(out_dir, "manifest.json")
    manifest_data = {
        "generated_at": datetime.now().isoformat(),
        "num_pairs": num_renders,
        "seed": seed,
        "config": cfg,
        "pairs": manifest,
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest_data, f, indent=2)

    total_elapsed = time.time() - pipeline_start
    log("=" * 60)
    log("Pipeline complete!")
    log(f"  Ground truths  : {num_renders}")
    log(f"  Blur per truth : {blur_per_sharp}")
    log(f"  Total blur imgs: {render_count}")
    log(f"  Output dir     : {out_dir}")
    log(f"  Manifest       : {manifest_path}")
    log(f"  Total time     : {total_elapsed/60:.1f} min  ({total_elapsed:.0f}s)")
    log(f"  Avg per truth  : {total_elapsed/num_renders:.1f}s")


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_pipeline()
