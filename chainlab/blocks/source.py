"""信号源：枚举常见波形，同时接受外部波形表与任意函数。"""

from __future__ import annotations

import numpy as np

from ..core.block import Block, register, num
from ..core.spectral import Spectrum


def _fact(k: int) -> int:
    r = 1
    for i in range(2, k + 1):
        r *= i
    return r


def _besselj(n: int, x: float) -> float:
    """J_n(x) 的级数展开（n 取绝对值，符号由调用方处理）。"""
    n = abs(int(n))
    half = x / 2.0
    term = half ** n / _fact(n)
    total = 0.0
    for k in range(80):
        total += term
        term *= -(half * half) / ((k + 1) * (k + 1 + n))
        if abs(term) < 1e-18:
            break
    return total


@register("source")
class Source(Block):
    n_in = 0

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.kind_of = str(self.params.get("type", "sine")).lower()
        self.f = self.num("f", 1e3)
        self.amp = self.num("amp", 1.0)
        self.phase = np.deg2rad(self.num("phase", 0.0))   # 界面上按角度填
        self.offset = self.num("offset", 0.0)
        self.n_harm = int(self.params.get("n_harm", 15))
        # voltage = 理想电压源；current = 理想电流源（幅度单位 A，按负载阻抗换算成电压）
        self.mode = str(self.params.get("mode", "voltage")).lower()
        self.is_current = self.mode.startswith("cur")
        # 结电容：电流源（光电二极管等）的寄生电容，和负载阻抗构成一阶低通
        self.c_par = self.num("c_par", 0.0) or 0.0
        # 白噪声当作信号源的一种波形：没有确定性分量，只有 PSD
        self.noise_psd = self.num("psd", None)
        self.noise_rms = self.num("rms", None)
        self.n_lo = self.num("noise_f_lo", 0.0) or 0.0
        self.n_hi = self.num("noise_f_hi", None)
        if self.kind_of == "noise" and self.noise_psd is None and self.noise_rms is None:
            raise ValueError("白噪声信号源需要给 psd 或 rms")

    thermal_zout = True

    def psd(self, f):
        f = np.asarray(f, dtype=float)
        if self.kind_of != "noise":
            return np.zeros(f.shape)
        if self.noise_psd is not None:
            s = self.noise_psd ** 2
        else:
            bw = (self.n_hi - self.n_lo) if self.n_hi else (
                (self.ctx.fs / 2.0) if self.ctx and self.ctx.fs else 1e6)
            s = self.noise_rms ** 2 / max(bw, 1e-9)
        out = np.where(f >= self.n_lo, s, 0.0)
        if self.n_hi:
            out = np.where(f <= self.n_hi, out, 0.0)
        return out

    @property
    def zout(self):
        # 电流源的诺顿电阻默认极大（可改）；电压源默认 0（理想）
        return self.num("zout", 1e10 if self.is_current else 0.0)

    def process(self, inputs):
        k = self.kind_of
        a = self.amp
        f = self.f
        if k == "noise":
            self._warn = []
            return Spectrum.empty(), {"bw": float("inf"), "peak_out": 0.0,
                                      "waveform": "noise", "source_f": 0.0}
        if k in ("sine", "sin"):
            out = Spectrum.tone(f, a, self.phase)
        elif k == "dc":
            out = Spectrum.empty()
        elif k == "square":
            # 方波是正弦级数：4A/π · Σ sin(nωt)/n（奇数 n）。
            # 用余弦系数会得到对数发散的怪波形，不是方波。
            out = Spectrum.empty()
            for n in range(1, self.n_harm + 1, 2):
                out = out.with_tone(n * f, 4 * a / (np.pi * n), self.phase - np.pi / 2)
        elif k == "triangle":
            out = Spectrum.empty()
            for i, n in enumerate(range(1, self.n_harm + 1, 2)):
                sign = (-1) ** i
                out = out.with_tone(
                    n * f, 8 * a / (np.pi ** 2 * n * n) * sign, self.phase - np.pi / 2
                )
        elif k == "am":
            fm = self.num("fm", 100.0)
            depth = self.num("depth", 0.5)
            out = Spectrum.tone(f, a, self.phase)
            out = out.with_tone(f - fm, depth * a / 2, self.phase)
            out = out.with_tone(f + fm, depth * a / 2, self.phase)
        elif k == "fm":
            fm = self.num("fm", 100.0)
            beta = self.num("beta", 1.0)
            out = Spectrum.empty()
            for n in range(-12, 13):
                c = _besselj(n, beta)
                if abs(c) > 1e-9:
                    out = out.with_tone(f + n * fm, a * abs(c), self.phase + (0 if c >= 0 else np.pi))
        else:
            raise ValueError(
                f"未知波形：{k}（可用 sine/square/triangle/dc/am/fm/noise）"
            )

        if self.offset:
            out = out.with_dc(self.offset)

        diag = {
            "bw": float("inf"),
            "peak_out": out.peak(),
            "source_f": f,
            "waveform": k,
        }
        self._warn = []
        # 只有含谐波的波形才检查折叠；正弦就是一个频点
        hi = out.max_significant_f() or f
        if self.ctx and self.ctx.fs and hi > self.ctx.fs / 2:
            self._warn.append(
                f"信号源的谐波到 {hi / 1e3:.4g} kHz，超过 fs/2（{self.ctx.fs / 2e3:.4g} kHz）："
                f"这些分量会混叠回带内。要么降谐波数，要么提高采样率"
            )
        return out, diag

    def warnings(self):
        return list(getattr(self, "_warn", []))

    def time_gen(self, t):
        """时域波形：直接对谱求值；白噪声返回零（噪声单独合成）。"""
        if self.kind_of == "noise":
            return np.zeros_like(t)
        sig, _ = self.process([])
        return sig.eval(t)

    def describe(self):
        names = {
            "sine": "正弦", "sin": "正弦", "square": "方波", "triangle": "三角波",
            "dc": "直流", "am": "调幅", "fm": "调频",
        }
        nm = names.get(self.kind_of, self.kind_of)
        if self.kind_of == "dc":
            return f"信号源  直流 {self.offset * 1e3:.4g} mV"
        return f"信号源  {nm} {self.f:.4g} Hz，{self.amp * 1e3:.4g} mVpk"
