"""纯阻性负载：只是把前级拉低，用来验证驱动能力。

没有输出端口，也不产生观测数据；它的作用全在输入阻抗上。
"""

from __future__ import annotations

from ..core.block import Block, register
from ..core.spectral import Spectrum


@register("load")
class Load(Block):
    n_in = 1
    n_out = 0

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.r = self.num("r", 50.0)
        if self.r <= 0:
            raise ValueError("负载电阻必须大于 0")

    thermal_zout = True

    @property
    def zin(self) -> float:
        return self.r

    def process(self, inputs):
        return Spectrum.empty(), {"bw": None, "peak_out": 0.0, "label": "负载"}

    def time_apply(self, inputs, fs, t):
        return inputs[0]

    def describe(self):
        return f"负载    {self.r:g} Ω 对地"
