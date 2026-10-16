# Reference-Guided Image Unblur

A reference-guided deblurring tool for photographers, and the synthetic training-data pipeline built to train it.

> **Status:** The Blender rendering pipeline and its verification tooling are essentially complete. User testing is the next step for submission. The deblurring model is in the planning stage, and its development continues after university.

This README replaces the Blender-only README. The setup steps from that file are carried over into [Setup](#8-setup).

---

## 1. Motivation

Sports, wildlife and portrait photographers often find blurry or out-of-focus frames only when reviewing their shots, sometimes long after the moment has passed. The same moment is frequently captured in full or partial focus by another frame in the burst, but those frames are never aligned. The subject has moved, the framing has shifted and the camera has shifted slightly.

Traditional sharpening tools in editors such as GIMP and Lightroom work on the single image in front of them. This project asks whether a model can use a sharp reference frame to restore detail in a blurry one.

---

## 2. What the project builds

The project has two artifacts.

**Artifact 1: the Image Unblur tool.** A self-hosted, ideally portable deep-learning tool. It takes one blurry photo plus one or more sharp reference photos of the same scene, and outputs a cleaner version of the blurry photo. (COMPLETED)

**Artifact 2: the rendering pipeline.** A headless Blender pipeline that renders paired training data. For each scene it produces one sharp ground truth and several blurred variants, using ethically sourced 2D and 3D assets, with randomised cameras, lighting and objects. (IN PROGRESS)

### Design constraint: translative, not generative

The tool must transpose real detail from the reference onto the blurry image. It must not invent plausible detail. Generative restoration methods, such as diffusion-based models, work by imagining detail from the input, so they are excluded from the core design. The brief does ask for research into generative image unblurring and how users respond to it, but that is a separate evaluation question, not the core method.

---

## 3. Goals and evaluation

From the project brief:

1. The rendering pipeline produces simulated photos suitable as training data, including motion blur and a non-blurred ground truth for each set.
2. The deblurring tool's output makes the subject of the input photo discernible.
3. User testing compares the tool against traditional sharpening in GIMP or Lightroom.
4. User testing asks whether preferences change once participants know generative AI was used, and why.
5. Research asks how complex a process must be before public perception of such tools changes.

The brief leaves the detailed evaluation method open. Quantitative metrics against the ground truth, such as PSNR and SSIM, are proposed for model benchmarking but have not been confirmed.

---

## 4. Approach

### Stage A: single-image baseline

A plain single-image deblurring network, using the official open-source NAFNet implementation (megvii-research), trained on blurry-to-sharp pairs with no reference input. This stage checks that the training data is sound and gives a number the reference-guided model has to beat.

### Stage B: reference-guided model

The baseline is extended with three parts:

- **Reference encoder.** Extracts features from the sharp reference photo.
- **Patch matching.** For each region of the blurry image's features, finds the most similar region in the reference's features.
- **Feature fusion.** Blends the matched reference features into the blurry image's features, weighted by how confident each match is.

The approach is based on *Reference-based Motion Blur Removal: Learning to Utilize Sharpness in the Reference Image* (arXiv:2307.02875). We have not found an official code release for that paper, so these modules will be implemented from its description.

Stage B is only justified if it beats Stage A on the same test set.

### Data flow

```
Blender pipeline             Training                          Model
────────────────             ─────────────────────────         ──────────────────
render_pipeline.py    ──►    render_pair_dataset.py     ──►    Stage A: blurry → sharp
  sharp + blur PNGs           (blurry, reference,               Stage B: blurry + reference
  manifest.json                ground_truth) tensors              → unblurred image
```

---


## 5. Synthetic dataset format

`render_pipeline.py` writes one folder per run:

```
output/
├── 00000_sharp.png        # ground truth: in focus, no motion
├── 00000_blur_00.png      # blur variant 0 of ground truth 00000
├── 00000_blur_01.png      # blur variant 1 of ground truth 00000
├── ...
└── manifest.json          # {"pairs": [ ... one entry per ground truth ... ]}
```

Each manifest entry records:

- `index` and `sharp_file`
- `blur_variants`, a list with one item per blur shot. Each item has `blur_file`, `blur_index`, `motion_blur` (motion type, shutter speed and related values) and `depth_of_field` (enabled flag, focus distance, focus rack and aperture).
- `camera`: location, rotation in degrees and focal length in mm.
- `render_time_s` and `seed_offset`.
- `asset_pool` (only when curated assets are enabled): HDRI, floor texture, model names and whether the entry starts a new asset checkpoint.

Each blur variant uses motion blur (camera movement combined with shutter speed) and depth-of-field defocus (a focus change during the shot). The brief only specifies motion blur, so the defocus simulation goes beyond it.

---

## 6. Setup

### 6.1 Blender pipeline

1. Install Blender and Python 3.11. On Windows, add Blender's root folder to the system `PATH`.
2. Clone this repository.
3. *(Optional)* Install the Poly Haven add-on in Blender: `Edit > Preferences > Get Extensions`, search for "Poly Haven" and install it. Then, under `File Paths > Asset Libraries`, add a library folder for downloaded assets.
4. Create a virtual environment in the project folder and install the dependencies:

   ```bash
   python3 -m venv venv
   source venv/bin/activate        # macOS, Linux, Git Bash
   source venv/Scripts/activate    # Windows Git Bash
   python3 -m pip install -r requirements.txt
   ```

5. *(Optional, for curated assets)* Copy `machine_config.example.json` to `machine_config.json` and set `poly_haven_library_path` and `manual_asset_library_path` to this machine's folders.
6. *(Optional, for curated assets)* Copy `asset_pools.example.json` to `asset_pools.json` and list the folder names of the assets you want the pipeline to use, split into `hdris`, `models` and `floor_textures`.
7. *(Optional, for curated assets)* In `config.json`, set `asset_pipeline.enabled` to `true`. The relevant settings are:
   - `min_models` / `max_models`: how many models are placed per scene.
   - `duplicate_cap_percent`: the maximum share of those models that may be repeats.
   - `checkpoint_percent`: how often the asset selection refreshes. Positions and lighting still randomise on every render.
8. Add `machine_config.json` to `.gitignore`.

### 6.2 Deep-learning stage (not yet required)

```bash
python3 -m pip install torch torchvision pillow
```

Choose the PyTorch build for your CUDA version from the selector at pytorch.org.

---

## 7. Usage

Quote any path that contains spaces.

```bash
# Render 50 ground-truth sets (sharp + blur variants each)
./run_pipeline.sh --count 50

# Preview one random asset selection without opening Blender
python3 build_render_manifest.py

# Check a finished run for missing or failed renders, with blur/focus stats
python3 verify_output.py --outdir ./output --show-stats

# Smoke-test the PyTorch dataset against a real output folder
python3 render_pair_dataset.py --root "./720p render example"
```

---

## 8. Ethics and resourcing

The brief sets two ethics requirements that shape the project.

- **Sourcing of training assets.** Digital assets, art and 3D models are often protected by copyright or licences that do not permit reuse outside their original context. The project uses Poly Haven assets (CC0) and manually sourced assets whose provenance must be recorded. Keeping training data and the training process ethically sourced is a stated requirement, not an optional extra.
- **Surveys and user testing.** The only approved survey so far is the ENGR489 cohort survey. A wider public survey or public user testing would need further ethics review.
---

## 9. Roadmap

The project is being submitted for university in its current state. User testing is the next step for submission. The deep-learning work is also planned to continue afterwards.

### Phase 1: Submission

| Stage                      | Status | Notes                                           |
|----------------------------|---|-------------------------------------------------|
| Blender rendering pipeline | Essentially complete | Minor work remaining                            |
| Verification tooling       | Complete | `verify_output.py`, `build_render_manifest.py`  |
| PyTorch dataset adapter    | Written | Not yet run on real renders                     |
| ENGR489 opinion surveying  | Next | To be completed right after artefact submission |

### Phase 2: After university

| # | Stage | Notes |
|---|---|---|
| 1 | Smoke-test the dataset adapter | Run `render_pair_dataset.py` on a real render output with torch installed |
| 2 | Finish the rendering pipeline | Minor remaining work |
| 3 | Baseline single-image model (Stage A) | Official NAFNet repo as the backbone; sets the no-reference baseline |
| 4 | Reference-guided model (Stage B) | Patch matching and feature fusion; must beat Stage A to justify itself |

---

## 10. Known issues

1. **Dataset not yet tested on real renders.** `render_pair_dataset.py` has been checked for syntax only. Run the self-test in [§9](#9-usage) once `torch` is installed.

---

## 11. References

- Zou et al. (arXiv): *Reference-based Motion Blur Removal: Learning to Utilize Sharpness in the Reference Image*, arXiv:2307.02875. The basis for the reference-guided model.
- Chen et al., *Simple Baselines for Image Restoration* (NAFNet), ECCV 2022. Official code: github.com/megvii-research/NAFNet.
- Poly Haven. Source of CC0 HDRIs, textures and models: polyhaven.com.
- Blender Python API (`bpy`).
- PyTorch.
- Project brief: `The Ethics of Generative AI in Algorithmic Photo Editing (SWEN).pdf`.
- Anthropic (2026). Claude. https://www.anthropic.com. Used for literature search, planning, drafting and designing code and notes. Conversations took place in October 2026.

---

## License

To be decided.
