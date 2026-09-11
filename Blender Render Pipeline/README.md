# Instructions for running the Blender Pipeline

This rendering pipeline has been written for Blender 4.2, and Python 3.11. These tools may work with newer versions of
Python or Blender, however these have not been tested and are not guaranteed to work.

This pipeline is runnable on Linux, MacOS, and Windows.

### First set of requirements:
The following needs to be installed
- Blender Version 4.2, installed in the default filepath, or Via Steam
- Python 3.11
- Cloning this Repository (Duh)
- Install the Blender PolyHaven Asset Browser Extension (Optional)

### Optional First set of requirements extension (only if you installed the PolyHaven extension above):
- Open Blender, and go to `Edit > Preferences > Get Extensions`, search for "Poly Haven", and install it.
- Still in Preferences, go to `File Paths > Asset Libraries` and add a library folder for it — this is the folder
  Poly Haven will download assets into, and it's the same folder path you'll point `machine_config.json` at in the
  Third set of requirements below. Pick somewhere memorable, since this path differs per machine.

### Second set of requirements:
- If on Windows, add the root folder of Blender's executable into the Windows environment table's PATH
- Create a Python virtual environment specifically for the Blender Pipeline, and with the activated venv, install the
  necessary pip packages with the supplied `requirements.txt` within this `Blender Render Pipeline` directory.
  - If on Windows, all terminal interactions should be done within a BASH based terminal, or via WSL. Git Bash will be
  installed if you've already set up Git on your Windows Machine.

### Third set of requirements (optional — only needed if you want curated 3D assets in your renders):
By default the pipeline runs on a procedural test scene (primitives + flat-color ground). To have it pull in real
furniture/props/floor textures from a curated Poly Haven (or your own manually-collected) asset pool, do the following:

1. **Collect assets in Blender.** With the Poly Haven add-on installed and its library folder set up (see the 1.5th
   set of requirements above), browse the Asset Browser and download whichever HDRIs, models, and floor/ground
   textures you want available — each one lands in its own subfolder inside that library path.
2. **Set up your machine config.** Copy `machine_config.example.json` to `machine_config.json` within the
  `/Blender Render Pipeline` directory and edit the two paths to point at this machine's Poly Haven library folder, and
   (optionally) a separate folder for any manually-added assets that didn't come from Poly Haven.
   **This file is machine-specific — Set it up separately on every computer you run the pipeline on, and add if you are
   contributing, add it to `.gitignore` rather than committing it**, since the paths won't match between machines.
3. **Curate your pool.** Copy `asset_pools.example.json` to `asset_pools.json` within the
  `/Blender Render Pipeline` directory and list the exact asset names
   (matching their folder names from step 1) you want the pipeline to be able to pick from, split into `hdris`,
   `models`, and `floor_textures`. Unlike `machine_config.json`, this file *is* portable — it can be committed and
   shared across machines, since it only stores names, not paths.
4. **Enable it.** In `config.json`, set `asset_pipeline.enabled` to `true`. A few knobs in that same section are
   worth knowing about:
   - `min_models` / `max_models` — how many models get placed per scene (random count in this range)
   - `duplicate_cap_percent` — the max percentage of those models that are allowed to be repeats of each other
   - `checkpoint_percent` — how often (as a % of the total render count) the asset *selection* refreshes; positions
     and rotations still randomise every single render regardless of this setting
5. **(Optional) Preview a selection without opening Blender at all:**
   ```
   python3 build_render_manifest.py
   ```
   This prints what one random pick from your pool would look like — useful for sanity-checking `asset_pools.json`
   before committing to a full render run.

### Fourth set of Requirements:
Run the rendering pipeline
- Enter `./run_pipeline.sh` into the terminal, followed by `--count X`, replacing X with the number of
generated reference + ground truth sets you would like.
- E.g. `./run_pipeline.sh --count 50` to run the pipeline and generate 50 image sets

### Fifth set of Requirements (optional — verifying output):
Once a run finishes, you can sanity-check the generated dataset without needing Blender at all:
```
python3 verify_output.py --outdir ./output --show-stats
```
This checks that every ground truth has its expected blur shots, flags any suspiciously small (likely failed)
renders, and prints motion blur / depth-of-field statistics across the run.
