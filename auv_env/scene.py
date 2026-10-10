"""不可变开发场景与透明序列化；独立 PCG64 只在生成时消费。"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from auv_env.config import finite_scalar, nonnegative_int
from auv_env.geometry import Box, Capsule, Sphere, tuple_vector
from auv_env.terrain import Gaussian, Terrain


@dataclass(frozen=True, slots=True)
class CollisionPolicy:
    """m/s 单位的判定预算；接触带与软裕度分开，耗尽返回 UNCERTAIN。"""

    contact_tolerance_m: float = 1e-6
    soft_margin_m: float = 1.0
    line_spacing_m: float = 0.05
    min_line_spacing_m: float = 0.002
    max_depth: int = 12
    min_interval_s: float = 0.02 / 4096

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            if name != "max_depth":
                object.__setattr__(self, name, finite_scalar(getattr(self, name), name))
                if getattr(self, name) <= 0:
                    raise ValueError("几何阈值和间隔必须正")
        if nonnegative_int(self.max_depth, "max_depth") > 20:
            raise ValueError("递归深度不得超过20")
        if self.min_line_spacing_m > self.line_spacing_m or self.min_line_spacing_m < 1e-5:
            raise ValueError("中心线细分范围非法")


@dataclass(frozen=True, slots=True)
class Scene:
    """静态场景；边界 NED m；schema、生成器、实际参数和种子进入内容哈希。"""

    scene_id: str
    mother_terrain_id: str
    terrain: Terrain
    seed: int = 17
    lower: tuple[float, ...] = (0.0, 0.0, 5.0)
    upper: tuple[float, ...] = (300.0, 300.0, 85.0)
    spheres: tuple[Sphere, ...] = ()
    boxes: tuple[Box, ...] = ()
    body: Capsule = Capsule()
    policy: CollisionPolicy = CollisionPolicy()
    schema_version: int = 1
    generator_version: str = "analytic-development-v1"
    purpose: str = "development"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.scene_id, str)
            or not self.scene_id
            or not isinstance(self.mother_terrain_id, str)
            or not self.mother_terrain_id
            or type(self.schema_version) is not int
            or self.schema_version != 1
        ):
            raise ValueError("场景标识或版本非法")
        if self.purpose != "development" or self.generator_version != "analytic-development-v1":
            raise ValueError("仅支持已登记的开发场景版本")
        nonnegative_int(self.seed, "scene_seed")
        if not isinstance(self.terrain, Terrain) or not isinstance(self.body, Capsule):
            raise ValueError("地形或艇体类型错误")
        if not isinstance(self.policy, CollisionPolicy):
            raise ValueError("几何配置类型错误")
        object.__setattr__(self, "lower", tuple_vector(self.lower, 3))
        object.__setattr__(self, "upper", tuple_vector(self.upper, 3))
        if np.any(np.array(self.lower) >= self.upper):
            raise ValueError("场景边界非法")
        object.__setattr__(self, "spheres", tuple(self.spheres))
        object.__setattr__(self, "boxes", tuple(self.boxes))
        if any(not isinstance(x, Sphere) for x in self.spheres) or any(
            not isinstance(x, Box) for x in self.boxes
        ):
            raise ValueError("静态障碍类型错误")
        ids = [obj.object_id for obj in self.spheres] + [obj.object_id for obj in self.boxes]
        if len(set(ids)) != len(ids) or any(x in {"terrain", "domain_boundary"} for x in ids):
            raise ValueError("障碍ID重复或占用保留ID")

    @property
    def scene_hash(self) -> str:
        """不含时间戳的规范化几何内容 SHA-256。"""
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, allow_nan=False).encode()
        ).hexdigest()

    def to_json(self) -> str:
        """输出实际参数与哈希；不重新抽样，不消费 RNG。"""
        return json.dumps(
            {"scene": asdict(self), "scene_hash": self.scene_hash}, sort_keys=True, allow_nan=False
        )

    @classmethod
    def from_json(cls, text: str) -> "Scene":
        """严格重建后验证哈希；缺失字段/不合法参数抛 ValueError，不运行外部代码。"""
        try:
            data = json.loads(text)
            if set(data) != {"scene", "scene_hash"} or set(data["scene"]) != set(
                cls.__dataclass_fields__
            ):
                raise ValueError("场景字段不完整")
            value: dict[str, Any] = data["scene"]
            terrain = value["terrain"]
            terrain["bumps"] = tuple(Gaussian(**item) for item in terrain["bumps"])
            value["terrain"] = Terrain(**terrain)
            value["body"] = Capsule(**value["body"])
            value["policy"] = CollisionPolicy(**value["policy"])
            value["spheres"] = tuple(Sphere(**obj) for obj in value["spheres"])
            value["boxes"] = tuple(Box(**obj) for obj in value["boxes"])
            result = cls(**value)
            if result.scene_hash != data["scene_hash"]:
                raise ValueError("场景哈希不符")
            return result
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("非法场景 JSON") from error


def generate_scene(kind: str, seed: int) -> Scene:
    """生成一张300m开发图：平底/斜面/丘陵/山脊/弯曲通道；不创建正式集。"""
    nonnegative_int(seed, "seed")
    rng = np.random.Generator(np.random.PCG64(seed))
    if kind == "flat":
        terrain = Terrain()
    elif kind == "slope":
        terrain = Terrain(60.0, 0.03, 0.02)
    elif kind in {"hills", "ridge", "channel"}:
        count = 6 if kind == "channel" else 4
        # 参数层面限制总幅值55m，不对地形函数作逐点clip。
        weights = rng.uniform(0.5, 1.0, count)
        amplitudes = weights / weights.sum() * rng.uniform(35.0, 55.0)
        bumps = []
        for index in range(count):
            if kind == "hills":
                north, east = rng.uniform(65.0, 235.0, 2)
                width_n, width_e = rng.uniform(15.0, 50.0, 2)
            elif kind == "ridge":
                north, east = 65.0 + index * 55.0, 150.0 + rng.uniform(-10.0, 10.0)
                width_n, width_e = 50.0, rng.uniform(15.0, 22.0)
            else:
                north = 65.0 + (index // 2) * 85.0
                bend = 20.0 * math.sin(north / 60.0)
                east = 150.0 + bend + (-40.0 if index % 2 == 0 else 40.0)
                width_n, width_e = 50.0, rng.uniform(15.0, 20.0)
            bumps.append(
                Gaussian(
                    float(amplitudes[index]),
                    float(north),
                    float(east),
                    float(width_n),
                    float(width_e),
                )
            )
        terrain = Terrain(bumps=tuple(bumps))
    else:
        raise ValueError("未知地形类别")
    return Scene(
        f"{kind}-{seed}",
        f"mother-{kind}-{seed}",
        terrain,
        seed,
        spheres=(Sphere("sphere-1", (100.0, 100.0, 20.0), 2.0),),
        boxes=(Box("box-1", (180.0, 170.0, 20.0), (184.0, 175.0, 30.0)),),
    )
