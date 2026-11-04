"""模块契约、注册表与运行上下文。

硬约束：块是纯函数，不持有状态。所有可变量都在 params 与 Ctx 里。

v1.2 起块可以多入单出（求和），因此接口是 process(inputs) —— inputs 按端口顺序给。
"""

from __future__ import annotations

import numpy as np

REGISTRY: dict[str, type] = {}

# ---------------------------------------------------------------- 数值解析

_SI = {
    "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6,
    "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
}
_UNITS = ("Hz", "hz", "HZ", "ohm", "Ohm", "Ω", "V", "A")


def num(v, default=None):
    """把 10k / 2.2u / 3.3V / 1e6 这类写法转成 float。"""
    if v is None:
        # 默认值本身也可能是 "8M" 这种写法，一样要转换
        return None if default is None else num(default)
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float, np.floating, np.integer)):
        return float(v)
    s = str(v).strip().replace("_", "").replace(" ", "")
    if not s:
        return default
    # 时间后缀（先于单位剥离，避免 "2ms" 被当成 2）
    for suf, scale in (("ms", 1e-3), ("us", 1e-6), ("µs", 1e-6),
                       ("ns", 1e-9), ("ps", 1e-12)):
        if s.endswith(suf):
            return float(s[: -len(suf)]) * scale
    for unit in _UNITS:
        if s.endswith(unit):
            s = s[: -len(unit)]
            break
    mult = 1.0
    if s and s[-1] in _SI:
        mult = _SI[s[-1]]
        s = s[:-1]
    return float(s) * mult


def as_bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "是")
    return bool(v)


# ---------------------------------------------------------------- 频响工具

def sdomain_H(num, den):
    """由 s 域多项式系数返回 H(f)。num/den 为降幂系数，s = j2πf。"""
    num = np.asarray(num, dtype=float)
    den = np.asarray(den, dtype=float)

    def h(f):
        s = 1j * 2 * np.pi * np.asarray(f, dtype=float)
        return np.polyval(num, s) / np.polyval(den, s)

    return h


# ---------------------------------------------------------------- 上下文

class Ctx:
    """一次运行的全部环境信息。块只能读，不能写。"""

    def __init__(self, cfg=None, base_dir=None):
        self.cfg = cfg or {}
        self.base_dir = base_dir
        self.fs = None
        self.periods = None
        self.t_seconds = None
        self.seed = 0
        self.notes: list[str] = []
        self.risks: list[str] = []

    def note(self, msg: str):
        if msg not in self.notes:
            self.notes.append(msg)

    def risk(self, msg: str):
        if msg not in self.risks:
            self.risks.append(msg)


# ---------------------------------------------------------------- 基类

class Block:
    kind = ""
    n_in = 1
    n_out = 1
    is_sink = False
    is_adc = False
    is_dac = False

    def __init__(self, params, ctx: Ctx):
        self.params = dict(params or {})
        self.ctx = ctx

    # ---- 参数读取 ----
    def num(self, key, default=None):
        return num(self.params.get(key), default)

    def flag(self, key, default=False):
        if key not in self.params:
            return default
        return as_bool(self.params[key])

    # ---- 连接参数 ----
    @property
    def zin(self) -> float:
        return self.num("zin", 1e12)

    @property
    def zout(self) -> float:
        return self.num("zout", 0.0)

    # ---- 行为 ----
    def H(self, f):
        """复频响（输出电压 / 输入电压）。"""
        return np.ones(np.asarray(f, dtype=float).shape, dtype=complex)

    def psd(self, f):
        """本块在自身输出端的噪声功率谱密度（V²/Hz）。"""
        return np.zeros(np.asarray(f, dtype=float).shape)

    def port_gain(self, port: int, n_in: int) -> float:
        """第 port 路输入到本块输出的增益（求和器的 k_i）。"""
        return 1.0

    def process(self, inputs):
        """inputs: 按端口顺序的 Spectrum 列表。返回 (输出 Spectrum, diag)。"""
        raise NotImplementedError

    # ---- 时域层 ----
    def time_gen(self, t):
        """无输入块的波形生成（信号源）。"""
        raise NotImplementedError

    def time_apply(self, inputs, fs, t):
        """时域执行。默认：线性块按频响滤波，再施加输出限幅。"""
        from .timedomain import clamp, lti

        y = lti(inputs[0], fs, self.H)
        return clamp(y, self.num("v_lo", None), self.num("v_hi", None))

    @property
    def is_nonlinear(self) -> bool:
        """有时域专属行为（硬限幅/乘法/振荡）的块。"""
        return self.num("v_lo", None) is not None or self.num("v_hi", None) is not None

    def describe(self) -> str:
        return self.kind

    def check(self) -> list[str]:
        return []

    def warnings(self) -> list[str]:
        return []


class Sink:
    """观测端：接收 ADC 码值，产出报告。不参与链路传递。"""

    kind = ""
    n_in = 1
    n_out = 0
    is_sink = True
    is_adc = False
    is_dac = False

    def __init__(self, params, ctx: Ctx):
        self.params = dict(params or {})
        self.ctx = ctx

    def num(self, key, default=None):
        return num(self.params.get(key), default)

    @property
    def zin(self) -> float:
        return self.num("zin", 1e12)

    @property
    def zout(self) -> float:
        return 0.0

    def observe(self, codes, meta) -> dict:
        raise NotImplementedError

    def describe(self) -> str:
        return self.kind


# ---------------------------------------------------------------- 注册

def register(name):
    def deco(cls):
        cls.kind = name
        REGISTRY[name] = cls
        return cls

    return deco


def get_class(name: str):
    if name not in REGISTRY:
        raise KeyError(
            f"未知模块：{name}（可用：{', '.join(sorted(REGISTRY))}）"
        )
    return REGISTRY[name]
