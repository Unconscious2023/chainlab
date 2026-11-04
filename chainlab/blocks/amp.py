"""放大器：器件档给 bw(G)，自定义时用 GBW/G 外推。

内部架构不进模型——行为级只关心"这个增益下带宽是多少"。
"""

from __future__ import annotations

import numpy as np

from ..core import devices
from ..core.block import Block, num, register, num
from ..core.spectral import Spectrum

K_BOLTZMANN = 1.380649e-23
T_NOM = 300.0


def _interp_bw(table: dict, g: float) -> float | None:
    """在 bw_at_gain 表上按 log-log 线性插值。"""
    pts = sorted((float(num(k)), float(num(v))) for k, v in table.items())
    if not pts:
        return None
    xs = np.log10([p[0] for p in pts])
    ys = np.log10([p[1] for p in pts])
    lg = np.log10(max(g, 1e-9))
    if lg <= xs[0]:
        return float(10 ** ys[0])
    if lg >= xs[-1]:
        return float(10 ** ys[-1])
    return float(10 ** np.interp(lg, xs, ys))


@register("amp")
class Amp(Block):
    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.dev = devices.load(self.params["device"]) if self.params.get("device") else {}
        cfg = self.dev.get("opamp", self.dev) if self.dev else {}

        self.name = self.dev.get("name") if self.dev else "自定义"
        self.G = self.num("G", cfg.get("g_default", 1.0))
        self.en = self.num("en", None) or num(cfg.get("en")) or 0.0
        self.in_noise = self.num("in", None) or num(cfg.get("in")) or 0.0  # A/√Hz
        self.vos = self.num("vos", cfg.get("vos", 0.0))
        self.sr = self.num("sr", cfg.get("sr", None))
        self.v_swing = self.num("v_swing", cfg.get("v_swing", None))
        self.i_out_max = self.num("i_out_max", cfg.get("i_out_max", None))
        self.g_min = self.num("g_min", cfg.get("g_min", 1.0))

        table = self.params.get("bw_at_gain") or cfg.get("bw_at_gain")
        self._table = table
        # 反相组态（G<0）的闭环带宽只看 |G|
        if table:
            self.fc = _interp_bw(table, abs(self.G))
        else:
            gbw = self.num("gbw", cfg.get("gbw", None))
            if not gbw:
                raise ValueError(f"放大器 {self.name} 缺少带宽信息：给 gbw 或 bw_at_gain")
            self.fc = gbw / max(abs(self.G), 1e-9)

        self.switch_gbw = self.flag("use_gbw", False)
        self.switch_sr = self.flag("use_sr", False)
        self.switch_swing = self.flag("use_swing", False)

        self._checks: list[str] = []
        if abs(self.G) < self.g_min - 1e-12:
            self._checks.append(
                f"增益 |G|={abs(self.G):g} 低于最小稳定增益 {self.g_min:g}：这不是带宽不足，是实际会振荡"
            )
        if not self.switch_sr:
            ctx.risk("压摆率 SR（未启用；勾选后强制走时域）")

    def H(self, f):
        return self.G / (1.0 + 1j * np.asarray(f, dtype=float) / self.fc)

    def psd(self, f):
        f = np.asarray(f, dtype=float)
        return (self.en ** 2) * np.abs(self.H(f)) ** 2

    def process(self, inputs):
        out = inputs[0].filtered(self.H)
        peak = out.peak()
        diag = {
            "bw": self.fc,
            "gain": self.G,
            "peak_out": peak,
            "device": self.name,
            "v_swing": self.v_swing,
        }
        if self.v_swing is not None and peak > self.v_swing:
            diag["swing_exceeded"] = peak
        if abs(self.G) < self.g_min - 1e-12:
            diag["unstable"] = True
        return out, diag

    @property
    def is_nonlinear(self) -> bool:
        return self.switch_sr or super().is_nonlinear

    def time_apply(self, inputs, fs, t):
        from ..core.timedomain import clamp, lti

        y = lti(inputs[0], fs, self.H)
        if self.switch_sr and self.sr:
            # 压摆率是「输出跟不上输入」：按误差限速跟随，而不是限输入的变化率。
            # 后者遇到带纹波的方波会把正负变化互相抵消，输出缩成一条小抖动。
            step = self.sr * 1e6 / fs                 # sr 单位 V/µs → 每采样点最大变化量
            out = np.empty_like(y)
            prev = y[0]
            for i in range(len(y)):
                err = y[i] - prev
                prev += step if err > step else (-step if err < -step else err)
                out[i] = prev
            y = out
        return clamp(y, self.num("v_lo", None), self.num("v_hi", None))

    def describe(self):
        bw = self.fc
        if bw >= 1e9:
            txt = f"{bw / 1e9:.4g} GHz"
        elif bw >= 1e6:
            txt = f"{bw / 1e6:.4g} MHz"
        elif bw >= 1e3:
            txt = f"{bw / 1e3:.4g} kHz"
        else:
            txt = f"{bw:.4g} Hz"
        return f"放大器  {self.name}，G = {self.G:g}，闭环带宽 {txt}"

    def check(self):
        return list(self._checks)
