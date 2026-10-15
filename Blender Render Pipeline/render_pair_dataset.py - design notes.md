# `render_pair_dataset.py` — Design Notes

The reasoning behind the choices in `render_pair_dataset.py`, kept here so the script itself stays short.

---

## 1. What the script does

It adapts the output of `render_pipeline.py` (a folder containing `<idx>_sharp.png`, `<idx>_blur_NN.png` and `manifest.json`) into training triplets of `(blurry, reference, ground_truth)` tensors that a PyTorch training loop can consume.

---

## 2. Why return a "reference" when there's also a ground truth?

In the reference-guided deblurring setup, the model receives **two** images at inference time:

1. The blurry photo it needs to fix (network input)
2. A sharp reference photo of the same scene (extra network input)

It produces **one** output, which is trained against the **ground truth** (the actual sharp version of the blurry photo).

Right now `render_pipeline.py` renders the sharp and blurry images from the identical scene and camera setup, so the reference and the ground truth are literally the same file. The dataset still returns them under two separate keys because:

- **The output shape already matches what the reference-guided model will expect** once you move past the no-reference baseline.
- **Adding reference jitter later needs no rewrite.** If you modify `render_pipeline.py` to render a slightly different reference frame per ground truth (recommended for realism, since a photographer's reference will be a *different* burst frame), you only change which manifest field feeds `reference`.

**For the Step 2 baseline (plain NAFNet, no reference):** ignore the `reference` key in the batch dict. Nothing else changes.

---

## 3. Why one dataset item per blur variant?

A ground truth with 3 blur variants contributes 3 separate samples, each paired against the same sharp image. This mirrors how the reference paper structures its training data (many blurry samples per scene) and multiplies your effective dataset size without extra sharp renders.

In the manifest, this means flattening `pairs[].blur_variants[]` into one row per blur shot.

---

## 4. Why crop and flip identically across the whole triplet?

**Paired random crop.** One random top-left corner is chosen and reused for every image. If the blurry and sharp images were cropped independently, the model would be asked to match content that is no longer at the same pixel location, and the loss would be meaningless.

**Paired flips.** Horizontal and vertical flips (p = 0.5 each, per the paper's Sec 4.1.2) are applied to all images together, for the same reason. Flips are only enabled when `train=True`; turn them off for validation/test splits so evaluation is deterministic apart from the crop.

**Why 256×256?** It matches the paper's training setup and keeps VRAM use comfortable on your GTX 1660 Super and RTX 3060.

---

## 5. Why skip missing files instead of crashing?

If the manifest references a PNG that isn't on disk (typically a partially completed or interrupted render run), the dataset skips that entry and prints a note rather than failing mid-training. Run `verify_output.py` separately if you want to know how many files are missing and why.

If *nothing* usable is found, the dataset raises an error immediately, so you never start a training run on an empty set.

---

## 6. Java-style mental model

Think of `RenderPairDataset` as implementing an interface like:

```java
interface Dataset<T> {
    int length();
    T getItem(int index);
}

class RenderPairDataset implements Dataset<Sample> {
    List<Sample> samples;          // built once in the constructor from manifest.json

    int length() { return samples.size(); }

    Sample getItem(int index) {
        // load blur + sharp pngs, crop, flip, convert to tensors
        return new Sample(blurry, reference, groundTruth);
    }
}
```

In Python these are `__len__` and `__getitem__`. You never call them yourself in normal use. You hand the whole `Dataset` object to a `DataLoader`, which calls them from worker processes to assemble batches.

---

## 7. Usage

```python
from render_pair_dataset import RenderPairDataset
from torch.utils.data import DataLoader

train_set = RenderPairDataset(root_dir="./720p render example", crop_size=256)
train_loader = DataLoader(
    train_set,
    batch_size=8,
    shuffle=True,
    num_workers=4,    # set to 0 if you hit multiprocessing issues on Windows
    pin_memory=True,  # faster host-to-GPU transfer on CUDA machines
)

for batch in train_loader:
    blurry    = batch["blurry"]        # (B, 3, 256, 256), float32 in [0, 1]
    reference = batch["reference"]     # (B, 3, 256, 256)
    gt        = batch["ground_truth"]  # (B, 3, 256, 256)
```

Self-test against a real output directory (needs `torch`, `torchvision` and `Pillow` installed):

```
python3 render_pair_dataset.py --root "./720p render example"
```

This prints the dataset length, tensor shapes and value range, then pulls one batch through a `DataLoader` to confirm collation works.
