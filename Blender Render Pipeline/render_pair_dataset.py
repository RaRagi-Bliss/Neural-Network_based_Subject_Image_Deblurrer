"""
render_pair_dataset.py

PyTorch Dataset that adapts render_pipeline.py output
(<idx>_sharp.png, <idx>_blur_NN.png, manifest.json) into
(blurry, reference, ground_truth) training triplets.

Design rationale, Java-style mental model and usage examples live in
render_pair_dataset_notes.md.

Self-test (needs torch, torchvision, Pillow):
    python3 render_pair_dataset.py --root "./720p render example"
"""

import argparse
import json
import random
from pathlib import Path
from typing import Optional

from torch.utils.data import Dataset
from PIL import Image
import torchvision.transforms.functional as TF


class RenderPairDataset(Dataset):
    """One dataset item = one blur variant (see notes, section 3)."""

    def __init__(
        self,
        root_dir: str,
        crop_size: Optional[int] = 256,
        manifest_name: str = "manifest.json",
        train: bool = True,
    ):
        """
        Args:
            root_dir: folder containing manifest.json and the pngs.
            crop_size: square crop side length, or None for full-size images.
            manifest_name: manifest filename inside root_dir.
            train: enables random flips (use False for val/test).
        """
        self.root_dir = Path(root_dir)
        self.crop_size = crop_size
        self.train = train

        manifest_path = self.root_dir / manifest_name
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"No manifest found at {manifest_path}. Did render_pipeline.py "
                f"finish running, and does --outdir point at the right folder?"
            )

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        ground_truths = manifest.get("pairs", [])
        if not ground_truths:
            raise ValueError(
                f"Manifest at {manifest_path} has no 'pairs' entries. "
                f"Nothing to train on."
            )

        # Flatten (ground truth, blur variant) into one row per blur shot.
        self.samples = []
        skipped = 0
        for gt in ground_truths:
            sharp_file = gt.get("sharp_file")
            blur_variants = gt.get("blur_variants", [])
            if not sharp_file or not blur_variants:
                skipped += 1
                continue

            sharp_path = self.root_dir / sharp_file
            if not sharp_path.exists():
                skipped += 1
                continue

            for variant in blur_variants:
                blur_file = variant.get("blur_file")
                if not blur_file:
                    continue
                blur_path = self.root_dir / blur_file
                if not blur_path.exists():
                    continue  # partial render run; see notes, section 5

                self.samples.append(
                    {
                        "index": gt.get("index"),
                        "blur_index": variant.get("blur_index"),
                        "sharp_path": sharp_path,
                        "blur_path": blur_path,
                    }
                )

        if not self.samples:
            raise ValueError(
                f"Manifest parsed, but zero usable (sharp, blur) pairs were "
                f"found on disk under {self.root_dir}. Check that the png "
                f"files actually exist alongside manifest.json."
            )

        if skipped:
            print(
                f"[RenderPairDataset] Note: skipped {skipped} ground truth "
                f"entries with missing files or no blur variants."
            )

        print(
            f"[RenderPairDataset] Loaded {len(self.samples)} training samples "
            f"from {len(ground_truths)} ground truths in {self.root_dir}"
        )

    def __len__(self) -> int:
        return len(self.samples)

    def _load_image(self, path: Path) -> Image.Image:
        return Image.open(path).convert("RGB")  # drop alpha for consistent shape

    def _paired_random_crop(self, *images: Image.Image):
        """Same random crop region for every image (notes, section 4)."""
        if self.crop_size is None:
            return images

        w, h = images[0].size
        if w < self.crop_size or h < self.crop_size:
            raise ValueError(
                f"Image size ({w}x{h}) is smaller than crop_size "
                f"({self.crop_size}). Lower --crop-size or check your "
                f"render resolution."
            )

        top = random.randint(0, h - self.crop_size)
        left = random.randint(0, w - self.crop_size)

        return tuple(
            TF.crop(img, top, left, self.crop_size, self.crop_size) for img in images
        )

    def _paired_augment(self, *images: Image.Image):
        """Identical random h/v flip across all images (p=0.5 each)."""
        if not self.train:
            return images

        if random.random() < 0.5:
            images = tuple(TF.hflip(img) for img in images)
        if random.random() < 0.5:
            images = tuple(TF.vflip(img) for img in images)

        return images

    def __getitem__(self, idx: int) -> dict:
        sample = self.samples[idx]

        blurry_img = self._load_image(sample["blur_path"])
        # Sharp image is both reference and ground truth for now (notes, section 2).
        sharp_img = self._load_image(sample["sharp_path"])

        blurry_img, sharp_img = self._paired_random_crop(blurry_img, sharp_img)
        blurry_img, sharp_img = self._paired_augment(blurry_img, sharp_img)

        # PIL (H, W, C) in [0, 255] -> tensor (C, H, W) in [0, 1]
        blurry_tensor = TF.to_tensor(blurry_img)
        sharp_tensor = TF.to_tensor(sharp_img)

        return {
            "blurry": blurry_tensor,
            "reference": sharp_tensor,
            "ground_truth": sharp_tensor,
            "index": sample["index"],
            "blur_index": sample["blur_index"],
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Smoke-test RenderPairDataset against a real output dir."
    )
    parser.add_argument(
        "--root",
        default="./720p render example",
        help="render_pipeline.py output directory (default: './720p render example').",
    )
    parser.add_argument(
        "--crop-size",
        type=int,
        default=256,
        help="Crop size to test with (default: 256, matching the paper).",
    )
    args = parser.parse_args()

    dataset = RenderPairDataset(root_dir=args.root, crop_size=args.crop_size)

    print(f"\nDataset length: {len(dataset)}")

    first = dataset[0]
    print("\nFirst sample:")
    print(f"  index:        {first['index']}")
    print(f"  blur_index:   {first['blur_index']}")
    print(
        f"  blurry shape: {tuple(first['blurry'].shape)}  dtype={first['blurry'].dtype}"
    )
    print(f"  ref shape:    {tuple(first['reference'].shape)}")
    print(f"  gt shape:     {tuple(first['ground_truth'].shape)}")
    print(
        f"  value range:  min={first['blurry'].min():.3f} "
        f"max={first['blurry'].max():.3f}  (expect ~0.0-1.0)"
    )

    from torch.utils.data import DataLoader

    loader = DataLoader(dataset, batch_size=4, shuffle=True, num_workers=0)
    batch = next(iter(loader))
    print("\nFirst batch (batch_size=4):")
    print(f"  blurry batch shape: {tuple(batch['blurry'].shape)}")
    print(f"  ref batch shape:    {tuple(batch['reference'].shape)}")
    print(f"  gt batch shape:     {tuple(batch['ground_truth'].shape)}")
    print(f"  indices in batch:   {batch['index'].tolist()}")

    print("\nOK -- dataset and DataLoader both work.")
