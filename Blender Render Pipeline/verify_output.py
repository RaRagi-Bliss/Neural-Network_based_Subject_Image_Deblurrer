"""
verify_output.py  —  Inspect and validate the generated training data.

Handles the "N blur shots per ground truth" structure:
    00000_sharp.png
    00000_blur_00.png
    00000_blur_01.png
    00000_blur_02.png
    ...

Run with standard Python (no Blender needed):
    python verify_output.py                          # checks ./output/
    python verify_output.py --outdir /path/to/data
    python verify_output.py --outdir ./output --show-stats
"""

import argparse

## python3 verify_output.py --outdir ./output --show-statsimport argparse
import json
import re
import sys
from pathlib import Path

BLUR_PATTERN = re.compile(r"^(\d+)_blur_(\d+)$")
SHARP_PATTERN = re.compile(r"^(\d+)_sharp$")


def parse_args():
    p = argparse.ArgumentParser(description="Verify training data output")
    p.add_argument("--outdir", default="./output", help="Output directory to inspect")
    p.add_argument(
        "--show-stats", action="store_true", help="Print detailed statistics"
    )
    return p.parse_args()


def main():
    args = parse_args()
    out_dir = Path(args.outdir)

    print(f"\nVerifying training data at: {out_dir.resolve()}\n")

    if not out_dir.exists():
        print("ERROR: Output directory does not exist.")
        sys.exit(1)

    # Load manifest
    manifest_path = out_dir / "manifest.json"
    ground_truths = []
    if not manifest_path.exists():
        print("WARNING: No manifest.json found.")
    else:
        with open(manifest_path) as f:
            manifest = json.load(f)
        ground_truths = manifest.get("pairs", [])
        print(f"Manifest loaded: {len(ground_truths)} ground truths expected")
        print(f"Generated at:    {manifest.get('generated_at', 'unknown')}")
        print(f"Seed:            {manifest.get('seed', 'unknown')}")
        expected_blur_total = sum(
            len(g.get("blur_variants", [])) for g in ground_truths
        )
        print(f"Expected blur shots (total): {expected_blur_total}")
        print()

    # Scan actual files
    sharp_files = sorted(out_dir.glob("*_sharp.png"))
    blur_files = sorted(out_dir.glob("*_blur_*.png"))

    print(f"Sharp files found: {len(sharp_files)}")
    print(f"Blur files found:  {len(blur_files)}")

    # Group blur files by their ground-truth index
    sharp_ids = set()
    for f in sharp_files:
        m = SHARP_PATTERN.match(f.stem)
        if m:
            sharp_ids.add(m.group(1))

    blur_ids_by_gt = {}
    unparseable = []
    for f in blur_files:
        m = BLUR_PATTERN.match(f.stem)
        if m:
            gt_id, blur_idx = m.group(1), m.group(2)
            blur_ids_by_gt.setdefault(gt_id, []).append(blur_idx)
        else:
            unparseable.append(f.name)

    if unparseable:
        print(
            f"\nWARNING: {len(unparseable)} blur files didn't match the expected "
            f"'<index>_blur_<NN>.png' naming pattern:"
        )
        for name in unparseable[:5]:
            print(f"  {name}")

    blur_gt_ids = set(blur_ids_by_gt.keys())
    missing_blur = sharp_ids - blur_gt_ids
    missing_sharp = blur_gt_ids - sharp_ids

    if missing_blur:
        print(f"\nWARNING: {len(missing_blur)} sharp files have no blur shots at all:")
        for s in sorted(missing_blur)[:5]:
            print(f"  {s}_sharp.png")

    if missing_sharp:
        print(
            f"\nWARNING: {len(missing_sharp)} blur files have no matching sharp file:"
        )
        for s in sorted(missing_sharp)[:5]:
            print(f"  {s}_blur_*.png")

    # Check blur counts are consistent (flags a partially-completed run)
    if blur_ids_by_gt:
        counts = [len(v) for v in blur_ids_by_gt.values()]
        most_common_count = max(set(counts), key=counts.count)
        inconsistent = {
            gt: len(v)
            for gt, v in blur_ids_by_gt.items()
            if len(v) != most_common_count
        }
        if inconsistent:
            print(
                f"\nWARNING: {len(inconsistent)} ground truths have a different "
                f"blur-shot count than the rest (expected {most_common_count}):"
            )
            for gt, count in sorted(inconsistent.items())[:5]:
                print(f"  {gt}: {count} blur shots")

    # File size sanity check (flag any suspiciously small renders — likely failed)
    small_files = []
    for f in list(sharp_files) + list(blur_files):
        size = f.stat().st_size
        if size < 5_000:  # < 5 KB is likely a failed/blank render
            small_files.append((f.name, size))

    if small_files:
        print(
            f"\nWARNING: {len(small_files)} suspiciously small files (possible failed renders):"
        )
        for name, size in small_files[:10]:
            print(f"  {name}  ({size} bytes)")

    # Stats from manifest
    if args.show_stats and ground_truths:
        print("\n── Motion blur & focus statistics ──────────────────────────")
        all_variants = [v for gt in ground_truths for v in gt.get("blur_variants", [])]
        render_times = [gt["render_time_s"] for gt in ground_truths]

        shutters = [
            v["motion_blur"]["shutter_speed"]
            for v in all_variants
            if "motion_blur" in v
        ]
        motion_types = [
            v["motion_blur"]["motion_type"] for v in all_variants if "motion_blur" in v
        ]

        if shutters:
            print(
                f"  Shutter speed:  min={min(shutters):.3f}  max={max(shutters):.3f}  "
                f"avg={sum(shutters)/len(shutters):.3f}"
            )

        type_counts = {}
        for t in motion_types:
            type_counts[t] = type_counts.get(t, 0) + 1
        if type_counts:
            print("  Motion types:")
            for t, c in sorted(type_counts.items()):
                print(f"    {t:<12}  {c:>4} ({100*c/len(motion_types):.0f}%)")

        dof_variants = [
            v["depth_of_field"]
            for v in all_variants
            if v.get("depth_of_field", {}).get("enabled")
        ]
        if dof_variants:
            focus_starts = [d["focus_distance_start"] for d in dof_variants]
            focus_racks = [abs(d["focus_rack_delta"]) for d in dof_variants]
            fstops = [d["aperture_fstop"] for d in dof_variants]
            print(
                f"  Focus distance: min={min(focus_starts):.2f}m  max={max(focus_starts):.2f}m"
            )
            print(
                f"  Focus rack:     avg={sum(focus_racks)/len(focus_racks):.3f}m  "
                f"max={max(focus_racks):.3f}m"
            )
            print(f"  Aperture f-stop: min={min(fstops):.1f}  max={max(fstops):.1f}")

        print("\n  Render time (per ground truth, incl. all its blur shots):")
        print(
            f"    min={min(render_times):.1f}s  max={max(render_times):.1f}s  "
            f"avg={sum(render_times)/len(render_times):.1f}s"
        )
        print(f"  Total render time: {sum(render_times)/60:.1f} min")

    # Summary
    print()
    complete_gts = len(sharp_ids & blur_gt_ids)
    total_blur_found = len(blur_files)
    ok = (
        complete_gts > 0
        and len(missing_blur) == 0
        and len(missing_sharp) == 0
        and len(small_files) == 0
        and not unparseable
    )

    if ok:
        print(
            f"✓ All {complete_gts} ground truths look good, {total_blur_found} blur shots total!"
        )
    else:
        print(
            f"⚠  {complete_gts} complete ground truths found ({total_blur_found} blur shots). "
            f"Review warnings above."
        )

    print()


if __name__ == "__main__":
    main()
