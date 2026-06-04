#!/usr/bin/env python3
"""
PROJECT 2502M - AV1/VVC Real Intra Prediction Mode Analysis

Reads real CSV dumps created by modified encoders:
  frame_num,frame_type,x,y,width,height,intra_mode

This script DOES NOT generate synthetic/fake/content-derived CSV data.

Important analysis design:
- Raw units are read directly from each encoder CSV. AV1 and VVC may use different
  block/CU/PU partitioning, so raw unit counts are not directly comparable.
- For fair spatial comparison, this script normalizes both codecs to fixed 64x64
  grid cells. For each frame and each grid cell, the dominant intra mode is chosen.
"""

from __future__ import annotations

import argparse
import csv
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np

from intra_mode_utilities import (
    MetricsCalculator,
    codec_mode_category,
    codec_mode_name,
    export_json,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("project2502m")

GRID_SIZE = 64


@dataclass(frozen=True)
class BlockInfo:
    frame_num: int
    frame_type: str
    x: int
    y: int
    width: int
    height: int
    intra_mode: int


@dataclass
class ModeStatistics:
    codec: str

    # Raw encoder CSV statistics.
    total_blocks: int
    frame_count: int
    mode_counts: Counter
    mode_percentages: Dict[int, float]
    mode_by_blocksize: Dict[str, Counter]
    mode_by_frametype: Dict[str, Counter]
    category_counts: Counter

    # Normalized 64x64 grid statistics.
    grid_size: int
    grid_cell_count: int
    grid_mode_counts: Counter
    grid_mode_percentages: Dict[int, float]
    grid_category_counts: Counter

    # Spatial maps built on fixed grid cells.
    spatial_dominant_share: np.ndarray
    spatial_dominant_mode_id: np.ndarray

    frame_width: int
    frame_height: int


class CsvIntraModeAnalyzer:
    def __init__(self, codec: str, csv_path: str | Path, num_frames: Optional[int] = None, grid_size: int = GRID_SIZE):
        self.codec = codec.lower()
        if self.codec not in {"av1", "vvc"}:
            raise ValueError("codec must be 'av1' or 'vvc'")
        self.csv_path = Path(csv_path)
        self.num_frames = num_frames
        self.grid_size = int(grid_size)
        if self.grid_size <= 0:
            raise ValueError("grid_size must be a positive integer")
        self.blocks: List[BlockInfo] = []

    def load(self) -> List[BlockInfo]:
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            raise FileNotFoundError(f"CSV not found or empty: {self.csv_path}")

        selected_frames = None
        if self.num_frames is not None and self.num_frames > 0:
            selected_frames = set()

        valid = 0
        skipped = 0

        with self.csv_path.open("r", newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or row[0].strip().startswith("#"):
                    continue
                if row[0].strip().lower() in {"frame_num", "frame", "poc"}:
                    continue
                if len(row) < 7:
                    skipped += 1
                    continue

                try:
                    frame_num = int(row[0])
                    frame_type = row[1].strip().upper()
                    x = int(row[2])
                    y = int(row[3])
                    width = int(row[4])
                    height = int(row[5])
                    intra_mode = int(row[6])

                    if frame_type not in {"I", "P", "B"}:
                        raise ValueError("invalid frame type")
                    if width <= 0 or height <= 0:
                        raise ValueError("invalid size")
                except Exception:
                    skipped += 1
                    continue

                if selected_frames is not None:
                    if frame_num not in selected_frames:
                        if len(selected_frames) < self.num_frames:
                            selected_frames.add(frame_num)
                        else:
                            continue

                self.blocks.append(BlockInfo(frame_num, frame_type, x, y, width, height, intra_mode))
                valid += 1

        if valid == 0:
            raise RuntimeError(f"No valid rows found in {self.csv_path}")

        if skipped:
            logger.warning("%s: skipped %d invalid rows", self.csv_path, skipped)

        logger.info("%s: loaded %d valid raw blocks/PUs", self.csv_path, valid)
        return self.blocks

    def compute_statistics(self) -> ModeStatistics:
        if not self.blocks:
            self.load()

        mode_counts = Counter(block.intra_mode for block in self.blocks)
        total_blocks = len(self.blocks)
        mode_percentages = {mode: count / total_blocks * 100.0 for mode, count in mode_counts.items()}

        mode_by_blocksize: Dict[str, Counter] = defaultdict(Counter)
        mode_by_frametype: Dict[str, Counter] = defaultdict(Counter)
        category_counts: Counter = Counter()

        for block in self.blocks:
            mode_by_blocksize[f"{block.width}x{block.height}"][block.intra_mode] += 1
            mode_by_frametype[block.frame_type][block.intra_mode] += 1
            category_counts[codec_mode_category(self.codec, block.intra_mode)] += 1

        frame_width = max(block.x + block.width for block in self.blocks)
        frame_height = max(block.y + block.height for block in self.blocks)
        frame_count = len(set(block.frame_num for block in self.blocks))

        (
            spatial_dominant_share,
            spatial_dominant_mode_id,
            grid_mode_counts,
            grid_cell_count,
        ) = self._make_normalized_grid_maps(frame_width, frame_height, grid_size=self.grid_size)

        grid_mode_percentages = {
            mode: count / grid_cell_count * 100.0 for mode, count in grid_mode_counts.items()
        } if grid_cell_count else {}

        grid_category_counts: Counter = Counter()
        for mode, count in grid_mode_counts.items():
            grid_category_counts[codec_mode_category(self.codec, mode)] += count

        return ModeStatistics(
            codec=self.codec,
            total_blocks=total_blocks,
            frame_count=frame_count,
            mode_counts=mode_counts,
            mode_percentages=mode_percentages,
            mode_by_blocksize=dict(mode_by_blocksize),
            mode_by_frametype=dict(mode_by_frametype),
            category_counts=category_counts,
            grid_size=self.grid_size,
            grid_cell_count=grid_cell_count,
            grid_mode_counts=grid_mode_counts,
            grid_mode_percentages=grid_mode_percentages,
            grid_category_counts=grid_category_counts,
            spatial_dominant_share=spatial_dominant_share,
            spatial_dominant_mode_id=spatial_dominant_mode_id,
            frame_width=frame_width,
            frame_height=frame_height,
        )

    def _make_normalized_grid_maps(
        self, frame_width: int, frame_height: int, grid_size: int
    ) -> Tuple[np.ndarray, np.ndarray, Counter, int]:
        """
        Normalize raw encoder blocks/PUs to fixed 64x64 spatial cells.

        Per-frame grid mode distribution:
          key = (frame_num, gy, gx)
          value = Counter(intra_mode)
        For every frame-cell pair, choose one dominant mode. This prevents VVC
        from dominating statistics simply because it may split into far more PUs.

        Spatial visualization maps aggregate all frames into each (gy, gx) cell.
        """
        grid_w = max(1, (frame_width + grid_size - 1) // grid_size)
        grid_h = max(1, (frame_height + grid_size - 1) // grid_size)

        per_frame_cell_counts: Dict[Tuple[int, int, int], Counter] = defaultdict(Counter)
        spatial_cell_counts: Dict[Tuple[int, int], Counter] = defaultdict(Counter)

        for block in self.blocks:
            block_x0 = max(0, block.x)
            block_y0 = max(0, block.y)
            block_x1 = min(frame_width, block.x + block.width)
            block_y1 = min(frame_height, block.y + block.height)
            if block_x1 <= block_x0 or block_y1 <= block_y0:
                continue

            gx0 = max(0, block_x0 // grid_size)
            gy0 = max(0, block_y0 // grid_size)
            gx1 = min(grid_w - 1, (block_x1 - 1) // grid_size)
            gy1 = min(grid_h - 1, (block_y1 - 1) // grid_size)

            for gy in range(gy0, gy1 + 1):
                cell_y0 = gy * grid_size
                cell_y1 = min(frame_height, cell_y0 + grid_size)
                overlap_y = min(block_y1, cell_y1) - max(block_y0, cell_y0)
                if overlap_y <= 0:
                    continue

                for gx in range(gx0, gx1 + 1):
                    cell_x0 = gx * grid_size
                    cell_x1 = min(frame_width, cell_x0 + grid_size)
                    overlap_x = min(block_x1, cell_x1) - max(block_x0, cell_x0)
                    if overlap_x <= 0:
                        continue

                    overlap_area = int(overlap_x * overlap_y)
                    per_frame_cell_counts[(block.frame_num, gy, gx)][block.intra_mode] += overlap_area
                    spatial_cell_counts[(gy, gx)][block.intra_mode] += overlap_area

        grid_mode_counts: Counter = Counter()
        for counter in per_frame_cell_counts.values():
            if counter:
                dominant_mode, _ = counter.most_common(1)[0]
                grid_mode_counts[int(dominant_mode)] += 1

        grid_cell_count = sum(grid_mode_counts.values())

        dominant_share_map = np.zeros((grid_h, grid_w), dtype=float)
        dominant_mode_id_map = np.full((grid_h, grid_w), -1, dtype=int)

        for (gy, gx), counter in spatial_cell_counts.items():
            total = sum(counter.values())
            if total <= 0:
                continue
            dominant_mode, dominant_count = counter.most_common(1)[0]
            dominant_share_map[gy, gx] = dominant_count / total
            dominant_mode_id_map[gy, gx] = int(dominant_mode)

        return dominant_share_map, dominant_mode_id_map, grid_mode_counts, grid_cell_count

    def mode_name(self, mode_id: int) -> str:
        return codec_mode_name(self.codec, mode_id)


class ModeVisualizer:
    def __init__(self, analyzer: CsvIntraModeAnalyzer):
        self.analyzer = analyzer

    def _top_items(self, counter: Counter, limit: int):
        items = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)
        top = items[:limit]
        other = sum(v for _, v in items[limit:])
        if other > 0:
            top.append((None, other))
        return top

    def _labels_counts(self, counter: Counter, limit: int):
        labels, counts = [], []
        for mode, count in self._top_items(counter, limit):
            labels.append("Other" if mode is None else self.analyzer.mode_name(mode))
            counts.append(int(count))
        return labels, counts

    def _bar_distribution(self, counter: Counter, output_path: Path, title: str, ylabel: str) -> None:
        labels, counts = self._labels_counts(counter, limit=12)
        total = sum(counts)
        percentages = [c / total * 100 if total else 0 for c in counts]

        fig, ax = plt.subplots(figsize=(12, 7))
        ax.bar(range(len(labels)), counts)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=35, ha="right")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.3)

        for i, pct in enumerate(percentages):
            ax.text(i, counts[i], f"{pct:.1f}%", ha="center", va="bottom", fontsize=8)

        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
        plt.close(fig)

    def plot_mode_distribution(self, stats: ModeStatistics, output_path: Path) -> None:
        self._bar_distribution(
            stats.mode_counts,
            output_path,
            f"{stats.codec.upper()} Raw Intra Mode Distribution",
            "Raw block/PU count",
        )

    def plot_normalized_grid_mode_distribution(self, stats: ModeStatistics, output_path: Path) -> None:
        self._bar_distribution(
            stats.grid_mode_counts,
            output_path,
            f"{stats.codec.upper()} Normalized 64x64 Grid Dominant Mode Distribution",
            "64x64 frame-cell count",
        )

    def plot_spatial_dominant_share(self, stats: ModeStatistics, output_path: Path) -> None:
        fig, ax = plt.subplots(figsize=(12, 7))
        im = ax.imshow(stats.spatial_dominant_share, interpolation="nearest", vmin=0, vmax=1)
        ax.set_title(f"{stats.codec.upper()} Spatial Dominant Mode Share ({stats.grid_size}x{stats.grid_size} grid)")
        ax.set_xlabel(f"{stats.grid_size}-pixel grid X")
        ax.set_ylabel(f"{stats.grid_size}-pixel grid Y")
        cbar = fig.colorbar(im, ax=ax)
        cbar.set_label("Dominant mode share")
        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
        plt.close(fig)

    def plot_spatial_dominant_mode_id(self, stats: ModeStatistics, output_path: Path) -> None:
        data = np.ma.masked_where(stats.spatial_dominant_mode_id < 0, stats.spatial_dominant_mode_id)
        fig, ax = plt.subplots(figsize=(12, 7))
        im = ax.imshow(data, interpolation="nearest")
        ax.set_title(f"{stats.codec.upper()} Spatial Dominant Mode ID ({stats.grid_size}x{stats.grid_size} grid)")
        ax.set_xlabel(f"{stats.grid_size}-pixel grid X")
        ax.set_ylabel(f"{stats.grid_size}-pixel grid Y")
        cbar = fig.colorbar(im, ax=ax)
        cbar.set_label("Dominant intra mode ID")
        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
        plt.close(fig)

    def plot_blocksize_comparison(self, stats: ModeStatistics, output_path: Path) -> None:
        block_sizes = sorted(stats.mode_by_blocksize.keys(), key=lambda s: tuple(int(x) for x in s.split("x")))
        totals = [sum(stats.mode_by_blocksize[size].values()) for size in block_sizes]

        fig, ax = plt.subplots(figsize=(12, 6))
        ax.bar(range(len(block_sizes)), totals)
        ax.set_xticks(range(len(block_sizes)))
        ax.set_xticklabels(block_sizes, rotation=35, ha="right")
        ax.set_ylabel("Raw block/PU count")
        ax.set_title(f"{stats.codec.upper()} Raw Block/PU Size Distribution")
        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
        plt.close(fig)

    def plot_frametype_comparison(self, stats: ModeStatistics, output_path: Path) -> None:
        frame_types = [ft for ft in ["I", "P", "B"] if ft in stats.mode_by_frametype]
        if not frame_types:
            return

        fig, axes = plt.subplots(1, len(frame_types), figsize=(6 * len(frame_types), 5), squeeze=False)
        for ax, frame_type in zip(axes[0], frame_types):
            labels, counts = self._labels_counts(stats.mode_by_frametype[frame_type], limit=8)
            ax.bar(range(len(labels)), counts)
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=35, ha="right")
            ax.set_title(f"{stats.codec.upper()} {frame_type}-Frame Raw Units")
            ax.set_ylabel("Raw count")
            ax.grid(axis="y", alpha=0.3)

        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
        plt.close(fig)

    def save_all(self, stats: ModeStatistics, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.plot_mode_distribution(stats, output_dir / "01_raw_mode_distribution.png")
        self.plot_normalized_grid_mode_distribution(stats, output_dir / "02_normalized_grid_mode_distribution.png")
        self.plot_spatial_dominant_share(stats, output_dir / "03_spatial_dominant_share.png")
        self.plot_spatial_dominant_mode_id(stats, output_dir / "04_spatial_dominant_mode_id.png")
        self.plot_blocksize_comparison(stats, output_dir / "05_raw_blocksize_distribution.png")
        self.plot_frametype_comparison(stats, output_dir / "06_raw_frametype_distribution.png")

        # Backward-compatible names for older demo/report references.
        self.plot_mode_distribution(stats, output_dir / "01_mode_distribution.png")
        self.plot_spatial_dominant_share(stats, output_dir / "02_spatial_heatmap.png")
        self.plot_blocksize_comparison(stats, output_dir / "04_blocksize_comparison.png")
        self.plot_frametype_comparison(stats, output_dir / "05_frametype_comparison.png")


def _counter_to_dict(counter: Counter) -> dict:
    return {str(k): int(v) for k, v in sorted(counter.items())}


def _percent_to_dict(percentages: Dict[int, float]) -> dict:
    return {str(k): float(v) for k, v in sorted(percentages.items())}


def stats_to_json(stats: ModeStatistics, analyzer: CsvIntraModeAnalyzer) -> dict:
    raw_metrics = {
        "entropy": MetricsCalculator.entropy(stats.mode_counts),
        "diversity": MetricsCalculator.diversity(stats.mode_counts),
        "top3_concentration_percent": MetricsCalculator.concentration_top_n(stats.mode_counts, 3),
        "normalized_complexity_percent": MetricsCalculator.normalized_complexity(stats.mode_counts),
    }
    grid_metrics = {
        "entropy": MetricsCalculator.entropy(stats.grid_mode_counts),
        "diversity": MetricsCalculator.diversity(stats.grid_mode_counts),
        "top3_concentration_percent": MetricsCalculator.concentration_top_n(stats.grid_mode_counts, 3),
        "normalized_complexity_percent": MetricsCalculator.normalized_complexity(stats.grid_mode_counts),
    }

    return {
        "codec": stats.codec,
        "total_blocks": stats.total_blocks,
        "raw_unit_count": stats.total_blocks,
        "frame_count": stats.frame_count,
        "estimated_frame_size_from_csv": {"width": stats.frame_width, "height": stats.frame_height},
        "mode_distribution": _counter_to_dict(stats.mode_counts),
        "mode_percentages": _percent_to_dict(stats.mode_percentages),
        "mode_names": {str(k): analyzer.mode_name(k) for k in sorted(stats.mode_counts.keys())},
        "category_counts": {str(k): int(v) for k, v in sorted(stats.category_counts.items())},
        "mode_by_blocksize": {
            size: _counter_to_dict(counter) for size, counter in sorted(stats.mode_by_blocksize.items())
        },
        "mode_by_frametype": {
            ft: _counter_to_dict(counter) for ft, counter in sorted(stats.mode_by_frametype.items())
        },
        "metrics": raw_metrics,
        "top_modes": [
            {
                "mode_id": int(mode),
                "mode_name": analyzer.mode_name(mode),
                "count": int(count),
                "percentage": stats.mode_percentages[mode],
            }
            for mode, count in sorted(stats.mode_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
        ],
        "normalized_grid": {
            "grid_size": stats.grid_size,
            "grid_cell_count": stats.grid_cell_count,
            "grid_mode_distribution": _counter_to_dict(stats.grid_mode_counts),
            "grid_mode_percentages": _percent_to_dict(stats.grid_mode_percentages),
            "grid_category_counts": _counter_to_dict(stats.grid_category_counts),
            "metrics": grid_metrics,
            "top_grid_modes": [
                {
                    "mode_id": int(mode),
                    "mode_name": analyzer.mode_name(mode),
                    "count": int(count),
                    "percentage": stats.grid_mode_percentages[mode],
                }
                for mode, count in sorted(stats.grid_mode_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
            ],
        },
    }


def write_report(output_dir: Path, av1_data: dict, vvc_data: dict, av1_csv: Path, vvc_csv: Path) -> None:
    report_path = output_dir / "ANALYSIS_REPORT.txt"
    with report_path.open("w", encoding="utf-8") as f:
        f.write("PROJECT 2502M - REAL INTRA PREDICTION MODE ANALYSIS REPORT\n")
        f.write("=" * 78 + "\n\n")
        f.write("Data source:\n")
        f.write(f"  AV1 CSV: {av1_csv}\n")
        f.write(f"  VVC CSV: {vvc_csv}\n")
        f.write("  CSV schema: frame_num,frame_type,x,y,width,height,intra_mode\n")
        f.write("  CSV files are expected to be dumped by modified encoder source code.\n\n")
        f.write("Normalization note:\n")
        f.write("  AV1 and VVC can have very different raw block/CU/PU partitioning.\n")
        f.write("  Therefore, fair spatial comparison uses fixed 64x64 frame-grid cells.\n")
        f.write("  Each frame-cell contributes one dominant intra mode to normalized grid statistics.\n\n")
        f.write("Generated spatial/grid visualizations:\n")
        f.write("  02_normalized_grid_mode_distribution.png: dominant mode distribution after 64x64 normalization.\n")
        f.write("  03_spatial_dominant_share.png: dominant-mode concentration per 64x64 region.\n")
        f.write("  04_spatial_dominant_mode_id.png: dominant intra mode ID per 64x64 region.\n\n")

        for data in [av1_data, vvc_data]:
            grid = data["normalized_grid"]
            f.write(f"{data['codec'].upper()} SUMMARY\n")
            f.write("-" * 78 + "\n")
            f.write(f"Raw blocks/PUs logged: {data['raw_unit_count']}\n")
            f.write(f"Frames in CSV: {data['frame_count']}\n")
            f.write(f"Normalized 64x64 frame-cells: {grid['grid_cell_count']}\n")
            f.write(
                "Frame size estimated from CSV: "
                f"{data['estimated_frame_size_from_csv']['width']}x{data['estimated_frame_size_from_csv']['height']}\n"
            )
            f.write(f"Raw entropy: {data['metrics']['entropy']:.4f}\n")
            f.write(f"Grid entropy: {grid['metrics']['entropy']:.4f}\n")
            f.write("Top normalized-grid modes:\n")
            for item in grid["top_grid_modes"][:8]:
                f.write(
                    f"  Mode {item['mode_id']:>3} ({item['mode_name']}): "
                    f"{item['count']} cells ({item['percentage']:.2f}%)\n"
                )
            f.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze real AV1/VVC intra mode CSV dumps")
    parser.add_argument("--av1-stats", required=True, help="Path to AV1 real stats CSV")
    parser.add_argument("--vvc-stats", required=True, help="Path to VVC real stats CSV")
    parser.add_argument("--num-frames", type=int, default=None, help="Limit number of frame indices loaded from each CSV")
    parser.add_argument("--output-dir", default="results", help="Output directory")
    parser.add_argument("--grid-size", type=int, default=GRID_SIZE, help="Spatial normalization grid size. Default: 64")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    av1_analyzer = CsvIntraModeAnalyzer("av1", args.av1_stats, args.num_frames, grid_size=args.grid_size)
    vvc_analyzer = CsvIntraModeAnalyzer("vvc", args.vvc_stats, args.num_frames, grid_size=args.grid_size)

    av1_analyzer.load()
    vvc_analyzer.load()

    av1_stats = av1_analyzer.compute_statistics()
    vvc_stats = vvc_analyzer.compute_statistics()

    ModeVisualizer(av1_analyzer).save_all(av1_stats, output_dir / "av1")
    ModeVisualizer(vvc_analyzer).save_all(vvc_stats, output_dir / "vvc")

    av1_json = stats_to_json(av1_stats, av1_analyzer)
    vvc_json = stats_to_json(vvc_stats, vvc_analyzer)
    summary = {"av1": av1_json, "vvc": vvc_json}

    export_json(summary, output_dir / "analysis_summary.json")
    export_json(summary, "analysis_summary.json")
    write_report(output_dir, av1_json, vvc_json, Path(args.av1_stats), Path(args.vvc_stats))

    print("Analysis completed")
    print(f"Results saved to: {output_dir}")
    print(f"Summary JSON: {output_dir / 'analysis_summary.json'}")
    print(f"Report: {output_dir / 'ANALYSIS_REPORT.txt'}")


if __name__ == "__main__":
    main()
