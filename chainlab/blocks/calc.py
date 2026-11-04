"""积分器 / 微分器 / 压控振荡器。

积分器和微分器都是线性块，直接给 H(s)；VCO 必须走时域（相位是频率的积分）。
"""

from __future__ import annotations

import numpy as np

from ..core import devices
from ..core.block import Block, num, register
from ..core.spectral import Spectrum


@register("integrator")
class Integrator(Block):
    n_in = 1
    n_out = 1

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.r = self.num("r", 10e3)
        self.c = self.num("c", 10e-9)
        self.tau = self.r * self.c
        if self.tau <= 0:
            raise ValueError("积分器时间常数必须大于 0")
        self.label = f"积分器 τ = {self.tau * 1e6:g} µs"

    def H(self, f):
        f = np.asarray(f, dtype=float)
        out = np.zeros(f.shape, dtype=complex)
        nz = f != 0
        # 理想积分器 1/(sτ)；DC 增益取 0（真实电路靠反馈电阻限流，这里按理想处理）
        out[nz] = 1.0 / (2j * np.pi * f[nz] * self.tau)
        return out

    def process(self, inputs):
        out = inputs[0].filtered(self.H)
        return out, {"bw": None, "peak_out": out.peak(), "label": "积分器"}

    def describe(self):
        return f"积分器  R={self.r:g} Ω, C={self.c:g} F, τ={self.tau * 1e6:g} µs"


@register("vco")
class VCO(Block):
    """压控振荡器：f = f0 + Kv·v(t)，输出正弦。只能走时域。"""

    n_in = 1
    n_out = 1

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.f0 = self.num("f0", 10e3)
        self.kv = self.num("kv", 10e3)
        self.amp = self.num("amp", 0.5)
        self.phase0 = self.num("phase", 0.0) * np.pi / 180.0
        self.label = f"VCO f0={self.f0 / 1e3:g}k, Kv={self.kv / 1e3:g}k Hz/V"

    @property
    def is_nonlinear(self) -> bool:
        return True

    def process(self, inputs):
        # 解析层只能给自由振荡频率，扫频/调频得靠时域
        out = Spectrum.empty().with_tone(self.f0, self.amp, self.phase0)
        return out, {
            "bw": None,
            "peak_out": self.amp,
            "label": "VCO（解析层只给 f0）",
            "note": "VCO 的频率调制需要时域，解析层只输出 f0 处的单音",
        }

    def time_apply(self, inputs, fs, t):
        v = np.asarray(inputs[0], dtype=float)
        f_inst = self.f0 + self.kv * v
        if np.any(f_inst <= 0) or np.any(f_inst >= fs / 2):
            f_inst = np.clip(f_inst, 1.0, fs / 2 * 0.999)
        phase = 2 * np.pi * np.cumsum(f_inst) / fs
        return self.amp * np.sin(phase + self.phase0)

    def describe(self):
        return (f"VCO  f0={self.f0 / 1e3:g} kHz, Kv={self.kv / 1e3:g} kHz/V, "
                f"输出 {self.amp * 1e3:g} mVpk")

@register("pll")
class PLL(Block):
    """锁相环复合块：内部是闭环，对外仍是 1 进 1 出。

    用线性化相位模型（s 域）算环路指标，输出锁定后的载波。
    算不了捕获过程与锁定时间之外的非线性行为。
    """

    n_in = 1
    n_out = 1

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.kd = self.num("kd", 1.0)            # V/rad
        self.kv = self.num("kv", 10e3)           # Hz/V
        self.f0 = self.num("f0", 10e3)           # Hz
        self.n_div = max(int(self.num("n_div", 1)), 1)
        self.r1 = self.num("r1", 1e3)
        self.r2 = self.num("r2", 1e3)
        self.c = self.num("c", 100e-9)
        self.amp = self.num("amp", 0.5)
        self.v_max = self.num("v_max", 5.0)
        self._loop()

    # ---------------------------------------------------------------- 环路
    def _loop(self):
        self.tau1 = (self.r1 + self.r2) * self.c
        self.tau2 = self.r2 * self.c
        if self.tau1 <= 0:
            raise ValueError("环路滤波器时间常数必须大于 0")
        k = self.kd * 2 * np.pi * self.kv / self.n_div      # rad/s
        self.k_loop = k
        self.wn = float(np.sqrt(k / self.tau1))             # rad/s
        self.zeta = float((1.0 + k * self.tau2) / (2.0 * np.sqrt(k * self.tau1)))
        self.bw = float(self.wn * (self.zeta + np.sqrt(self.zeta ** 2 + 1.0)))  # rad/s
        self.ts = float(4.0 / max(self.zeta * self.wn, 1e-9))
        self.f_hold = float(self.kv * self.v_max)           # ±Hz 保持范围

    def process(self, inputs):
        f_ref, a_ref = inputs[0].fundamental()
        if not f_ref:
            return inputs[0], {"bw": None, "peak_out": 0.0,
                               "label": "锁相环（无参考）"}

        f_out = f_ref * self.n_div
        v_tune = (f_ref - self.f0) / self.kv if self.kv else 0.0

        self._warn = []
        if abs(v_tune) > self.v_max:
            self._warn.append(
                f"锁相环：参考 {f_ref / 1e3:.4g} kHz 需要调谐电压 {v_tune:.2f} V，"
                f"超过 VCO 范围 ±{self.v_max:g} V，锁不住"
            )
        if self.zeta < 0.4:
            self._warn.append(
                f"环路阻尼 ζ = {self.zeta:.2f} 偏小，阶跃响应会严重过冲、振铃"
            )
        elif self.zeta > 2.0:
            self._warn.append(f"环路阻尼 ζ = {self.zeta:.2f} 偏大，响应迟缓")

        out = Spectrum.empty().with_tone(f_out, self.amp, 0.0)
        self._f_out = f_out
        return out, {
            "bw": self.bw / (2 * np.pi),
            "peak_out": self.amp,
            "label": f"锁相环 {f_out / 1e3:.4g} kHz",
            "note": f"fn={self.wn / 2 / np.pi / 1e3:.3g} kHz, ζ={self.zeta:.2f}",
        }

    def warnings(self):
        return list(getattr(self, "_warn", []))

    def describe(self):
        return (f"锁相环  fn={self.wn / 2 / np.pi / 1e3:.3g} kHz, ζ={self.zeta:.2f}, "
                f"环路带宽 {self.bw / 2 / np.pi / 1e3:.3g} kHz, 锁定范围 ±{self.f_hold / 1e3:.3g} kHz")


@register("dcblock")
class DCBlock(Block):
    """隔直电容：一阶高通，隔离级间直流偏置。"""

    n_in = 1
    n_out = 1

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.c = self.num("c", 100e-9)
        r_raw = self.params.get("r")
        # R 留空 = 对地阻抗无穷大 = 理想隔直（只去直流，没有高通拐点）
        self.ideal = r_raw is None or r_raw == ""
        self.r = None if self.ideal else self.num("r", 1e8)
        if self.ideal:
            self.tau = None
            self.fc = 0.0
            self.label = f"隔直（理想）C={self.c * 1e9:g}n"
        else:
            if self.r <= 0:
                raise ValueError("隔直电容的对地电阻必须大于 0")
            self.tau = self.r * self.c
            if self.tau <= 0:
                raise ValueError("隔直电容的 R、C 必须大于 0")
            self.fc = 1.0 / (2 * np.pi * self.tau)
            self.label = f"隔直 C={self.c * 1e9:g}n, fc={self.fc:g} Hz"

    thermal_zout = True

    @property
    def zin(self) -> float:
        return 1e12 if self.ideal else self.r

    def H(self, f):
        f = np.asarray(f, dtype=float)
        if self.ideal:
            out = np.ones(f.shape, dtype=complex)
            out[f == 0] = 0.0
            return out
        s = 2j * np.pi * f * self.tau
        return s / (1.0 + s)

    def process(self, inputs):
        out = inputs[0].filtered(self.H)
        return out, {"bw": None, "peak_out": out.peak(), "label": "隔直"}

    def describe(self):
        if self.ideal:
            return f"隔直电容（理想）C={self.c * 1e9:g} nF，对地阻抗无穷大"
        return (f"隔直电容  C={self.c * 1e9:g} nF, R={self.r / 1e3:g} kΩ, "
                f"高通拐点 {self.fc:g} Hz")


@register("tia")
class TIA(Block):
    """跨阻放大器：输入是电流，输出 V = −I·Rf。

    反相端是虚地，zin 取 1 Ω 近似；前级电流源的幅度乘上这个 1 Ω 变成
    电压，再被 Rf 放大，正好得到 I·Rf。
    """

    n_in = 1
    n_out = 1

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        dev = devices.load(self.params["device"]) if self.params.get("device") else {}
        cfg = (dev.get("opamp") or dev) if dev else {}
        self.name = (dev.get("name") if dev else "") or ("自定义" if self.params.get("device") == "__custom__" else "理想（无噪声）")
        self.rf = self.num("rf", 100e3)
        self.cf = self.num("cf", 1e-12)
        self.cin = self.num("cin", 5e-12)
        # 器件档里的值是 "7n" 这种带单位的字符串，必须过一遍 num() 解析
        self.gbw = self.num("gbw", None) or num(cfg.get("gbw")) or 230e6
        self.en = self.num("en", None) or num(cfg.get("en")) or 0.0
        self.in_noise = self.num("in", None) or num(cfg.get("in")) or 0.0
        # 跨阻带宽：GBW / (2π·Rf·(Cin+Cf))
        self.fp = float(np.sqrt(self.gbw / (2 * np.pi * self.rf * (self.cin + self.cf))))
        self.label = f"TIA Rf={self.rf / 1e3:g}k, BW={self.fp / 1e6:.2f}M"

    @property
    def zin(self) -> float:
        return 1.0                 # 虚地近似

    def H(self, f):
        f = np.asarray(f, dtype=float)
        return -self.rf / (1.0 + 1j * f / self.fp)

    noise_output_referred = True

    def psd(self, f):
        """输出端噪声密度（含噪声增益 1+Cin/Cf 与反馈电阻热噪声），
        并按跨阻带宽做单极点滚降。"""
        f = np.asarray(f, dtype=float)
        kt = 1.380649e-23 * 290.0
        gain = 1.0 + self.cin / self.cf if self.cf > 0 else 1.0
        flat = (self.en * gain) ** 2 + 4.0 * kt * self.rf
        if self.in_noise:
            flat += (self.in_noise * self.rf) ** 2
        return flat / (1.0 + (f / self.fp) ** 2)

    def process(self, inputs):
        out = inputs[0].filtered(self.H)
        return out, {"bw": self.fp, "peak_out": out.peak(), "label": "TIA"}

    def output_noise(self):
        """输出噪声（Vrms）。

        e_out² = (in·Rf)² + 4kT·Rf + en²·(1 + Cin/Cf)²
        —— 第三项是噪声增益：Cin 越大、Cf 越小，en 被放大得越厉害。
        积分到跨阻带宽 fp（π/2 因子把单极点响应当噪声带宽）。
        """
        kt = 1.380649e-23 * 290.0
        gain = 1.0 + self.cin / self.cf if self.cf > 0 else 1.0
        psd = (self.en * gain) ** 2 + 4.0 * kt * self.rf
        if self.in_noise:
            psd += (self.in_noise * self.rf) ** 2
        fs = getattr(self.ctx, 'fs', None) or 0.0
        band = min(fs / 2.0, self.fp) if fs else self.fp
        return float(np.sqrt(psd * band))

    def check(self):
        out = []
        cf_min = float(np.sqrt(self.cin / (2 * np.pi * self.rf * self.gbw)))
        if self.cf > 0 and self.cf < cf_min:
            out.append(
                f"TIA 反馈电容 Cf={self.cf * 1e12:.2f} pF 小于稳定所需 "
                f"{cf_min * 1e12:.2f} pF：环路会振铃，且噪声增益 (1+Cin/Cf)="
                f"{1 + self.cin / self.cf:.0f} 会把运放电压噪声放大成噪声"
            )
        if self.cf <= 0:
            out.append("TIA 没有反馈电容，实际无法稳定工作")
        return out

    def describe(self):
        return (f"跨阻放大  {self.name}，Rf={self.rf / 1e3:g} kΩ, Cf={self.cf * 1e12:g} pF, "
                f"带宽 {self.fp / 1e6:.2f} MHz, 输出噪声 {self.output_noise() * 1e6:.1f} µVrms")
