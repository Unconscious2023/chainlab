"""白噪声源：串在链路里，给下游注入噪声。

噪声不进谱表示（随机，不是确定分量），只以 PSD 形式参与预算；
时域上由 runner 按「总 PSD」合成有色噪声，所以后级滤波会真的把它塑形。
"""

from __future__ import annotations

import numpy as np

from ..core.block import Block, register


@register("noise")
class WhiteNoise(Block):
    n_in = 0
    n_out = 1

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.psd_level = self.num("psd", None)     # V/√Hz
        self.rms = self.num("rms", None)           # 或者直接给有效值
        self.f_lo = self.num("f_lo", 0.0) or 0.0
        self.f_hi = self.num("f_hi", None)         # 窄带噪声给上下限
        if self.psd_level is None and self.rms is None:
            raise ValueError("白噪声模块需要给 psd 或 rms")
        if self.f_hi is not None and self.f_hi <= self.f_lo:
            raise ValueError("噪声上限频率必须大于下限频率")

    @property
    def bandwidth(self) -> float:
        if self.f_hi:
            return max(self.f_hi - self.f_lo, 1e-9)
        return (self.ctx.fs / 2.0) if self.ctx.fs else 1e6

    def psd(self, f):
        f = np.asarray(f, dtype=float)
        if self.psd_level is not None:
            s = self.psd_level ** 2
        else:
            s = self.rms ** 2 / self.bandwidth
        out = np.where(f >= self.f_lo, s, 0.0)
        if self.f_hi:
            out = np.where(f <= self.f_hi, out, 0.0)
        return out

    def process(self, inputs):
        # 噪声源：没有确定性信号输出，只有 PSD；要接到加法器或直接进 ADC
        from ..core.spectral import Spectrum

        return Spectrum.empty(), {
            "bw": self.f_hi or float("inf"),
            "peak_out": 0.0,
            "label": "白噪声",
        }

    def describe(self):
        if self.psd_level is not None:
            s = f"{self.psd_level / 1e-9:.3g} nV/√Hz"
        else:
            s = f"RMS {self.rms * 1e6:.3g} µV"
        if self.f_lo or self.f_hi:
            lo = f"{self.f_lo / 1e3:.3g}k" if self.f_lo else "0"
            hi = f"{self.f_hi / 1e3:.3g}k" if self.f_hi else "满带"
            s += f"，{lo}~{hi} Hz"
        return f"白噪声  {s}"
