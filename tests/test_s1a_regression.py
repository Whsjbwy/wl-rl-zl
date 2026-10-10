"""独立参考循环、终点对齐及故意扰动检测；完整200秒在阶段实验执行。"""

import copy
from pathlib import Path

import numpy as np
import pytest

from scripts.s0_checks import read_json
from scripts.s1a_experiments import regressions, trajectory_errors

ROOT = Path(__file__).resolve().parents[1]


def test_short_alignment_and_perturbation(tmp_path: Path) -> None:
    """单元用短长度覆盖五种情况和参考切换；不替代正式完整回归。"""
    config = copy.deepcopy(read_json(ROOT / "configs/s1a.json"))
    config.update(n_steps=20, duration_s=0.4)
    config["reference_schedule"][1]["start_step"] = 5
    config["reference_schedule"][2]["start_step"] = 12
    assert len(regressions(config, tmp_path)) == 5
    expected = np.loadtxt(tmp_path / "default/upstream.csv", delimiter=",", skiprows=1)
    actual = np.loadtxt(tmp_path / "default/adapter.csv", delimiter=",", skiprows=1)
    actual[3, 16] += 0.01
    with pytest.raises(ValueError, match="门槛"):
        trajectory_errors(expected, actual, config)
    actual = expected.copy()
    actual[-1, 0] += 0.02
    with pytest.raises(ValueError, match="时间"):
        trajectory_errors(expected, actual, config)
