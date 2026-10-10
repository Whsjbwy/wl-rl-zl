"""唯一解析海底 h(x,y) 与严格全域梯度界；NED 实体为 z>=h。"""

import math
from dataclasses import dataclass

import numpy as np

from auv_env.config import finite_scalar


@dataclass(frozen=True, slots=True)
class Gaussian:
    """浅化隆起；幅值 m 非负，中心/宽度 m；没有事后地形 clip。"""

    amplitude_m: float
    north_m: float
    east_m: float
    sigma_n_m: float
    sigma_e_m: float

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, finite_scalar(getattr(self, name), name))
        if self.amplitude_m < 0 or min(self.sigma_n_m, self.sigma_e_m) <= 0:
            raise ValueError("高斯幅值须非负，宽度须正")


@dataclass(frozen=True, slots=True)
class Terrain:
    """h=base+a*x+b*y-高斯和；m、无量纲坡度；生成/查询/绘图共用。"""

    base_depth_m: float = 80.0
    slope_n: float = 0.0
    slope_e: float = 0.0
    bumps: tuple[Gaussian, ...] = ()

    def __post_init__(self) -> None:
        for name in ("base_depth_m", "slope_n", "slope_e"):
            object.__setattr__(self, name, finite_scalar(getattr(self, name), name))
        if any(not isinstance(item, Gaussian) for item in self.bumps):
            raise ValueError("地形单元必须为 Gaussian")
        object.__setattr__(self, "bumps", tuple(self.bumps))

    def depth_at(self, north_m: float, east_m: float) -> float:
        """有限坐标处解析深度 m；非法输入抛 ValueError；无状态变更。"""
        x, y = finite_scalar(north_m, "north_m"), finite_scalar(east_m, "east_m")
        return (
            self.base_depth_m
            + self.slope_n * x
            + self.slope_e * y
            - sum(
                b.amplitude_m
                * math.exp(
                    -0.5
                    * (((x - b.north_m) / b.sigma_n_m) ** 2 + ((y - b.east_m) / b.sigma_e_m) ** 2)
                )
                for b in self.bumps
            )
        )

    def gradient_at(self, north_m: float, east_m: float) -> tuple[float, float]:
        """解析 (dh/dx,dh/dy)，无量纲；隆起减号对应向峰外深度增加。"""
        x, y = finite_scalar(north_m, "north_m"), finite_scalar(east_m, "east_m")
        gradient = np.array([self.slope_n, self.slope_e])
        for b in self.bumps:
            delta = np.array([x - b.north_m, y - b.east_m])
            widths = np.array([b.sigma_n_m, b.sigma_e_m])
            gradient += (
                b.amplitude_m * np.exp(-0.5 * np.sum((delta / widths) ** 2)) * delta / widths**2
            )
        return float(gradient[0]), float(gradient[1])

    def gradient_bound(self) -> float:
        """全域有效 G；归一化半径 u*exp(-u²/2) 在 u=1 取 1/sqrt(e)。"""
        return math.hypot(self.slope_n, self.slope_e) + sum(
            b.amplitude_m / (math.sqrt(math.e) * min(b.sigma_n_m, b.sigma_e_m)) for b in self.bumps
        )
