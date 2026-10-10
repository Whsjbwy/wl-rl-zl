"""独立复算与真实CLI失败传播；测试夹具不是仿真实验。"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from auv_env.current_kinematics import current_terms
from auv_env.currents import CurrentField
from scripts.s1c_checks import numeric_checks
from scripts.s1c_legacy_diagnostics import capture
from scripts.s1c_numeric import execute
from scripts.strict_json import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_l0_trace_readonly():
    """实际采集上游局部项且恢复trace钩子，输入状态不能被原地修改。"""
    eta = np.array([0, 0, 30, 0.2, 0.3, 0.4])
    nu = np.array([1.2, 0.1, 0.05, 0.04, -0.03, 0.05])
    actual = np.array([0.02, -0.01, 900.0])
    before = [value.copy() for value in (eta, nu, actual)]
    old_trace = sys.gettrace()
    data = capture(eta, nu, actual, np.array([0, 0.3, 0]))
    assert sys.gettrace() is old_trace
    for value, original in zip((eta, nu, actual), before, strict=True):
        np.testing.assert_array_equal(value, original)
    assert len(data["rigid"]) == 6 and np.array(data["CRB"]).shape == (6, 6)


@pytest.mark.parametrize("term", ["rotation", "temporal", "spatial", "joint"])
def test_three_derivative_counterexamples(term):
    """独立激活三个导数项；漏掉对应项必须改变总导数。"""
    eta = np.array([1, 2, 30, 0.2, -0.3, 0.4])
    nu = np.array([1.2, 0.1, 0.2, 0.1, -0.2, 0.3])
    if term != "joint" and term != "rotation":
        nu[3:] = 0
    field = CurrentField(
        "affine",
        background=[0.2, 0.3, 0.1],
        jacobian=np.eye(3) * 0.01 if term in {"spatial", "joint"} else np.zeros((3, 3)),
        partial_t=[0.02, 0.03, 0.01] if term in {"temporal", "joint"} else [0, 0, 0],
    )
    values = current_terms(eta, nu, field.sample(eta[:3], 2))
    if term == "joint":
        assert all(np.linalg.norm(values[k]) > 1e-3 for k in ("rotation", "temporal", "spatial"))
    else:
        np.testing.assert_allclose(values["nu_current_dot"][:3], values[term], atol=1e-12)
        assert np.linalg.norm(values[term]) > 1e-3


def test_sinusoid_partial_independent():
    field = CurrentField(
        "sinusoidal",
        background=[0.1, 0.2, 0],
        amplitude=[0.03, 0.05, 0.02],
        omega_radps=0.2,
        phase_rad=0.1,
    )
    h = 1e-4
    for t in np.linspace(0, 60, 100):
        expected = (
            field.sample([0, 0, 0], t + h).velocity_ned_mps
            - field.sample([0, 0, 0], t - h).velocity_ned_mps
        ) / (2 * h)
        np.testing.assert_allclose(
            field.sample([0, 0, 0], t).partial_t_mps2, expected, atol=1e-6, rtol=1e-6
        )


@pytest.mark.parametrize("fault", ["both", "rotation", "hash", "missing", "nan"])
def test_numeric_independent_rejection(tmp_path, fault):
    execute(tmp_path / "numeric")
    numeric_checks(tmp_path)
    path = tmp_path / "numeric/derivatives.json"
    data = read_json(path)
    row = data["samples"][0]
    if fault == "both":
        row["analytic"]["nu_current_dot"][0] = 9.0
        row["differences"][0]["body_difference"][0] = 9.0
    elif fault == "rotation":
        row["analytic"]["rotation"] = [0, 0, 0]
    elif fault == "hash":
        row["field"]["config_hash"] = "wrong"
    elif fault == "missing":
        data["samples"].pop()
    else:
        row["analytic"]["rotation"][0] = float("nan")
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        numeric_checks(tmp_path)


@pytest.mark.parametrize("fault", ["nonzero", "timeout", "missing", "json"])
def test_real_cli_rejects(tmp_path, fault):
    report = dict(
        stage="S1-C",
        parent_status="PENDING",
        run_directory="results/s1c/test-fixture",
        commands=[
            dict(name=n, exit_code=0, duration_s=0.0, timed_out=False)
            for n in ("numeric", "trajectories", "atomic", "parent")
        ],
    )
    if fault == "nonzero":
        report["commands"][0]["exit_code"] = 7
    elif fault == "timeout":
        report["commands"][0]["timed_out"] = True
    path = tmp_path / "bad.json"
    write_json(path, report)
    if fault == "json":
        path.write_text("{bad", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/verify_s1c.py", "--check-report", str(path)],
        cwd=ROOT,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1
