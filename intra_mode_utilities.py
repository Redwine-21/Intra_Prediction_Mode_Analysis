"""
PROJECT 2502M - Utility functions for AV1/VVC real intra prediction mode analysis.
Imported by intra_prediction_analysis.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import numpy as np


class MetricsCalculator:
    """Statistical metrics for intra-mode distributions."""

    @staticmethod
    def entropy(mode_distribution: Mapping[int, int]) -> float:
        total = int(sum(mode_distribution.values()))
        if total <= 0:
            return 0.0

        value = 0.0
        for count in mode_distribution.values():
            if count > 0:
                p = count / total
                value -= p * np.log2(p)
        return float(value)

    @staticmethod
    def diversity(mode_distribution: Mapping[int, int]) -> float:
        total = int(sum(mode_distribution.values()))
        if total <= 0:
            return 0.0
        return float(1.0 - sum((count / total) ** 2 for count in mode_distribution.values()))

    @staticmethod
    def concentration_top_n(mode_distribution: Mapping[int, int], n: int = 3) -> float:
        total = int(sum(mode_distribution.values()))
        if total <= 0:
            return 0.0
        top_sum = sum(sorted(mode_distribution.values(), reverse=True)[:n])
        return float(top_sum / total * 100.0)

    @staticmethod
    def normalized_complexity(mode_distribution: Mapping[int, int]) -> float:
        unique_modes = len([v for v in mode_distribution.values() if v > 0])
        if unique_modes <= 1:
            return 0.0
        max_entropy = np.log2(unique_modes)
        return float(min(100.0, MetricsCalculator.entropy(mode_distribution) / max_entropy * 100.0))


class AV1Utilities:
    """AV1 luma intra prediction mode names."""

    MODES = {
        0: "DC",
        1: "V",
        2: "H",
        3: "D45",
        4: "D135",
        5: "D113",
        6: "D157",
        7: "D203",
        8: "D67",
        9: "SMOOTH",
        10: "SMOOTH_V",
        11: "SMOOTH_H",
        12: "PAETH",
    }

    @staticmethod
    def name(mode_id: int) -> str:
        return AV1Utilities.MODES.get(int(mode_id), f"Unknown({mode_id})")

    @staticmethod
    def category(mode_id: int) -> str:
        mode_id = int(mode_id)
        if mode_id == 0:
            return "dc"
        if mode_id in {1, 2, 3, 4, 5, 6, 7, 8}:
            return "directional"
        if mode_id in {9, 10, 11}:
            return "smooth"
        if mode_id == 12:
            return "paeth"
        return "other"


class VVCUtilities:
    """VVC/H.266 luma intra prediction mode names.

    VVC normally has:
      0  = Planar
      1  = DC
      2..66 = Angular modes
    """

    @staticmethod
    def name(mode_id: int) -> str:
        mode_id = int(mode_id)
        if mode_id == 0:
            return "PLANAR"
        if mode_id == 1:
            return "DC"
        if 2 <= mode_id <= 66:
            return f"ANGULAR_{mode_id}"
        return f"Unknown({mode_id})"

    @staticmethod
    def category(mode_id: int) -> str:
        mode_id = int(mode_id)
        if mode_id == 0:
            return "planar"
        if mode_id == 1:
            return "dc"
        if 2 <= mode_id <= 66:
            return "angular"
        return "other"


def codec_mode_name(codec: str, mode_id: int) -> str:
    codec = codec.lower()
    if codec == "av1":
        return AV1Utilities.name(mode_id)
    if codec == "vvc":
        return VVCUtilities.name(mode_id)
    return str(mode_id)


def codec_mode_category(codec: str, mode_id: int) -> str:
    codec = codec.lower()
    if codec == "av1":
        return AV1Utilities.category(mode_id)
    if codec == "vvc":
        return VVCUtilities.category(mode_id)
    return "other"


def export_json(data: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
