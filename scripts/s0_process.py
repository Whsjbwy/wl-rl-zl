"""唯一运行目录及子进程日志：保留失败/超时，不使用 shell 管道。"""

import logging
import os
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from scripts.s0_checks import Json


def create_run(output: Path, run_id: str | None = None) -> Path:
    """原子创建唯一目录；重复标识、路径穿越立即失败，绝不复用旧结果。"""
    name = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    if (
        not name
        or name in {".", ".."}
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in name
        )
    ):
        raise ValueError("非法 run_id")
    output.mkdir(parents=True, exist_ok=True)
    folder = output / name
    folder.mkdir(exist_ok=False)
    return folder


def run_command(
    name: str,
    argv: list[str],
    cwd: Path,
    logs: Path,
    timeout_s: float,
    env: dict[str, str] | None = None,
) -> Json:
    """运行一个命令，记录 stdout/stderr、退出码、秒耗时；启动或超时也返回失败证据。"""
    started = time.perf_counter()
    stdout = logs / f"{name}.stdout.log"
    stderr = logs / f"{name}.stderr.log"
    timed_out = False
    logging.info("运行 %s", name)
    with stdout.open("wb") as out, stderr.open("wb") as err:
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                stdout=out,
                stderr=err,
                timeout=timeout_s,
                env=env or os.environ.copy(),
                check=False,
            )
            code = result.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            code = 124
            err.write("子进程超时，已终止并等待回收。\n".encode())
        except OSError as error:
            code = 127
            err.write(str(error).encode())
    return {
        "name": name,
        "argv": argv,
        "cwd": str(cwd),
        "exit_code": code,
        "duration_s": time.perf_counter() - started,
        "timed_out": timed_out,
        "stdout": str(stdout),
        "stderr": str(stderr),
        "status": "PASS" if code == 0 else "FAIL",
    }
