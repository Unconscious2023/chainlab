"""ADC：唯一的模拟/数字分界点。

核心判定不是"填参数"，而是：采样 + 转换 能不能塞进 1/fs。
塞不进就是物理不可能，直接拒绝执行并给出三条出路。
"""

from __future__ import annotations

import numpy as np

from ..core import devices
from ..core.block import Block, register, num
from ..core.spectral import Spectrum


class ADCError(ValueError):
    """物理上不可能或配置自相矛盾。"""


def _eng(v: float, unit: str = "") -> str:
    for scale, pre in (
        (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
        (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"),
    ):
        if abs(v) >= scale * 0.999:
            return f"{v / scale:.4g} {pre}{unit}"
    return f"{v:.4g} {unit}"


def _opt_num(v):
    """器件档里可能是 'VREF+' 这类文字，取值失败就当没给。"""
    if isinstance(v, (list, tuple)):
        v = v[-1] if v else None
    try:
        return num(v) if v is not None else None
    except (TypeError, ValueError):
        return None


@register("adc")
class ADC(Block):
    is_adc = True

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        dev = devices.load(params["device"]) if params.get("device") else {}
        cfg = dev.get("adc", {}) or {}
        self.dev = dev
        self.name = dev.get("name") or "自定义 ADC"

        self.n_bits = int(self.num("n_bits", cfg.get("n_bits_default", cfg.get("n_bits", 12))))
        self.n_bits_options = cfg.get("n_bits_options") or [self.n_bits]
        vp = self.params.get("vref")
        if vp is None or (isinstance(vp, str) and not vp.strip()):
            vr = cfg.get("vref", 3.3)
            self.vref = float(vr[-1]) if isinstance(vr, (list, tuple)) else float(vr)
        else:
            self.vref = num(vp)

        self.fs = self.num("fs", ctx.fs)
        self.f_adc = self.num("f_adc", cfg.get("f_adc", cfg.get("f_adc_max", 36e6)))
        self.radc = self.num("radc", cfg.get("radc", 6e3))
        self.cadc = self.num("cadc", cfg.get("cadc", 4e-12))
        self.fs_max = _opt_num(cfg.get("fs_max"))
        # 流水线 ADC 内部有采样保持，采样建立不占转换周期；SAR 才按 RC 建立算
        self.sampling_model = str(cfg.get("sampling_model") or "sar").lower()

        # 同一型号不同位数：转换周期、ENOB、采样档位都不同
        pb = (cfg.get("per_bits") or {}).get(self.n_bits) or (cfg.get("per_bits") or {}).get(str(self.n_bits)) or {}
        self.conv_cycles = self.num("conv_cycles", pb.get("conv_cycles", cfg.get("conv_cycles", self.n_bits)))
        self.enob = self.num("enob", pb.get("enob", cfg.get("enob", None)))
        if self.sampling_model == "pipeline":
            self.radc = self.num("radc", 0.0)
            if "conv_cycles" not in self.params:
                self.conv_cycles = 0.0
        self.part = cfg.get("part") or ""
        self.verified = bool(self.dev.get("verified", cfg.get("verified", True)))
        self.ideal = bool(self.dev.get("ideal", cfg.get("ideal", False)))
        if self.ideal:
            self.n_bits = 0
            self.enob = None
            self.conv_cycles = 0
            self.n_bits_options = [0]
        if self.dev and self.n_bits not in [int(b) for b in self.n_bits_options]:
            raise ADCError(
                f"{self.name} 不支持 {self.n_bits} bit（可选 {self.n_bits_options}）"
            )

        self.signed = self.flag("signed", False)
        self.diff = str(self.params.get("input_mode", "single")).lower() in (
            "differential", "diff", "差分",
        )
        if self.diff and not cfg.get("differential", False):
            raise ADCError(f"{self.name} 不支持差分输入（手册里没有差分模式）")
        self.cycles_spec = (
            self.params.get("sample_cycles")
            or pb.get("sample_cycles")
            or cfg.get("sample_cycles")
            or None
        )

        sample = self.params.get("sample")
        if isinstance(sample, dict):
            self.solve_cfg = sample.get("solve") or {}
            self.cycles_given = sample.get("cycles")
        else:
            self.solve_cfg = self.params.get("solve") or {}
            self.cycles_given = sample  # 允许 sample: 28 直接给周期数
        if self.cycles_given is None:
            self.cycles_given = self.params.get("sample_cycles")

        self.source_impedance = 0.0
        self._info = None

    # ------------------------------------------------------------ 判定
    @property
    def lsb(self) -> float:
        if self.ideal:
            return 0.0
        full = 2 * self.vref if self.diff else self.vref
        return full / (2 ** self.n_bits)

    @property
    def offset_code(self) -> int:
        return 2 ** (self.n_bits - 1) if (self.diff or self.signed) else 0

    def resolve(self):
        if self.ideal:
            return {"rain": 0.0, "tau": 0.0, "cycles_required": 0.0, "cycles": 0.0,
                    "t_sample": 0.0, "t_conv": 0.0, "t_total": 0.0, "err_lsb": 0.0,
                    "fs_max": float("inf"), "warnings": []}
        if self._info is not None:
            return self._info
        if not self.fs:
            raise ADCError("未指定采样率：在 adc.fs 或 meta.fs 里给一个值")
        if self.fs_max and self.fs > self.fs_max * (1 + 1e-9):
            raise ADCError(
                f"{self.name} 最高采样率 {_eng(self.fs_max, 'Hz')}，"
                f"当前 {_eng(self.fs, 'Hz')} 超出器件上限"
            )

        rain = num(self.solve_cfg.get("rain"), self.source_impedance)
        tau = (self.radc + rain) * self.cadc
        n = self.n_bits
        t_req = tau * np.log(2 ** (n + 2))
        cycles_req = t_req * self.f_adc

        budget = 1.0 / self.fs * self.f_adc - self.conv_cycles  # 吞吐允许的采样周期数

        allowed = [float(c) for c in self.cycles_spec] if self.cycles_spec else None

        if self.cycles_given is not None:
            cycles = float(num(self.cycles_given))
        elif allowed:
            legal = [c for c in allowed if c >= cycles_req - 1e-9]
            if not legal:
                raise ADCError(self._no_way(rain, cycles_req, budget, allowed))
            cycles = legal[0]
        else:
            cycles = float(np.ceil(cycles_req))

        if cycles > budget + 1e-9:
            raise ADCError(self._no_way(rain, cycles_req, budget, allowed))

        t_s = cycles / self.f_adc
        err_lsb = 2.0 ** n * np.exp(-t_s / tau) if tau > 0 else 0.0
        warnings = []
        if err_lsb > 0.5:
            warnings.append(
                f"建立误差 {err_lsb:.2f} LSB（>0.5 LSB），精度会掉；"
                f"建议采样周期 ≥ {int(np.ceil(cycles_req))} 或降低源阻抗"
            )

        self._info = {
            "rain": rain,
            "tau": tau,
            "cycles_required": cycles_req,
            "cycles": cycles,
            "t_sample": t_s,
            "t_conv": self.conv_cycles / self.f_adc,
            "t_total": (cycles + self.conv_cycles) / self.f_adc,
            "err_lsb": err_lsb,
            "fs_max": (self.f_adc / (cycles + self.conv_cycles)
                       if (cycles + self.conv_cycles) > 0 else float("inf")),
            "warnings": warnings,
        }
        return self._info

    def _no_way(self, rain, cycles_req, budget, allowed):
        conv = self.conv_cycles
        cands = [float(c) for c in allowed] if allowed else []
        lnf = np.log(2 ** (self.n_bits + 2))

        if cands:
            ge = [c for c in cands if c >= cycles_req - 1e-9]
            c_min = min(ge) if ge else None
            le = [c for c in cands if c <= budget + 1e-9]
            c_best = max(le) if le else None
            allowed_txt = "；该型号档位 " + "/".join(f"{c:g}" for c in cands)
        else:
            c_min = float(np.ceil(cycles_req))
            c_best = budget
            allowed_txt = ""

        options = []
        if c_min is not None:
            fs_max = self.f_adc / (c_min + conv)
            options.append(f"① 采样率降至 ≤ {_eng(fs_max, 'Hz')}")
        if c_best is not None:
            rain_max = c_best / (self.f_adc * lnf) / self.cadc - self.radc
            if rain_max > 0:
                options.append(
                    f"② R_AIN 降至 ≤ {_eng(rain_max, 'Ω')}（当前 {_eng(rain, 'Ω')}）"
                )
            else:
                options.append("② 降低源阻抗已无解，前端必须加缓冲")
        options.append("③ ADC 前插入缓冲器（BUF634 / THS3091）")

        need = f"{cycles_req:.1f} 周期建立"
        if c_min is not None and abs(c_min - cycles_req) > 1e-9:
            need += f"（吸附到合法档位 {c_min:g} 周期）"

        return (
            f"当前配置无解：fs = {_eng(self.fs, 'Hz')} 时每样本最多 "
            f"{budget:.0f} 个 ADCCLK，需要 {need} + {conv:g} 周期转换，塞不下{allowed_txt}。\n"
            "请任选其一：\n  " + "\n  ".join(options)
        )

    # ------------------------------------------------------------ 噪声
    def adc_noise(self) -> float:
        if self.ideal:
            return 0.0
        if self.enob:
            snr = 6.02 * self.enob + 1.76
            # 满量程正弦的峰值：差分 ±Vref，单端 0~Vref
            fs_peak = self.vref if self.diff else self.vref / 2.0
            fs_rms = fs_peak / np.sqrt(2.0)
            return float(fs_rms / (10 ** (snr / 20.0)))
        return float(self.lsb / np.sqrt(12.0))

    # ------------------------------------------------------------ 采样
    def capture(self, sig: Spectrum, n_samples: int, noise_v: float = 0.0, limits=None,
                noise=None):
        t = np.arange(n_samples) / self.fs
        return self.capture_array(sig.eval(t), limits=limits, noise=noise, noise_v=noise_v)

    def capture_array(self, voltage, limits=None, noise=None, noise_v: float = 0.0):
        """直接量化一段电压序列（时域层用）。"""
        info = self.resolve()
        x = np.asarray(voltage, dtype=float).copy()
        n_samples = len(x)
        if noise is not None:
            x = x + np.asarray(noise, dtype=float)[:n_samples]
        elif noise_v > 0:
            rng = np.random.default_rng(self.ctx.seed)
            x = x + rng.normal(0.0, noise_v, n_samples)

        limit_clipped = 0.0
        if limits:
            lo, hi = limits
            before = x
            if lo is not None:
                x = np.maximum(x, lo)
            if hi is not None:
                x = np.minimum(x, hi)
            limit_clipped = float(np.mean(x != before))

        if self.ideal:
            return x.copy(), {
                "fs": self.fs, "n_bits": None, "vref": self.vref, "lsb": 0.0,
                "offset_code": 0, "ideal": True, "clipped": 0.0, "adc_noise": 0.0,
                "info": {}, "name": self.name, "differential": self.diff,
                "warnings": [], "limit_clipped": limit_clipped,
                "noise_injected": float(noise_v), "code_max": 0, "code_min": 0,
            }

        lsb = self.lsb
        code = np.rint(x / lsb + self.offset_code)
        top = 2 ** self.n_bits - 1
        clipped = np.mean((code < 0) | (code > top))
        codes = np.clip(code, 0, top).astype(np.int64)

        meta = {
            "fs": self.fs,
            "n_bits": self.n_bits,
            "vref": self.vref,
            "lsb": lsb,
            "offset_code": self.offset_code,
            "ideal": False,
            "clipped": float(clipped),
            "adc_noise": self.adc_noise(),
            "info": info,
            "name": self.name,
            "differential": self.diff,
            "warnings": info["warnings"],
            "code_max": int(codes.max()) if codes.size else 0,
            "code_min": int(codes.min()) if codes.size else 0,
            "noise_injected": float(noise_v),
            "limit_clipped": limit_clipped,
        }
        return codes, meta

    def process(self, inputs):
        """信号在 ADC 处不改变，只是分界线；差分时先做 V_P − V_N。"""
        if self.diff:
            if len(inputs) < 2:
                raise ADCError("差分输入需要两路：V_P 和 V_N 都要接")
            out = inputs[0].plus(inputs[1].scaled(-1.0)).merged()
            return out, {"bw": None, "peak_out": out.peak(), "label": "ADC（差分）"}
        return inputs[0], {"bw": None, "peak_out": inputs[0].peak(), "label": "ADC"}

    def time_apply(self, inputs, fs, t):
        if self.diff:
            return inputs[0] - inputs[1]
        return inputs[0]

    @property
    def n_in(self):
        return 2 if self.diff else 1

    @property
    def zin(self) -> float:
        """ADC 的输入电阻：高速 ADC 常见 50 Ω，会把前级拉低。"""
        return self.num("r_in", (self.dev.get("adc") or {}).get("r_in", 1e12))

    def describe(self):
        try:
            info = self.resolve()
            cyc = f"{info['cycles']:g} 周期"
            err = f"建立误差 {info['err_lsb']:.3g} LSB"
        except ADCError:
            cyc, err = "—", "配置无效"
        mode = "差分" if self.diff else "单端"
        return (
            f"ADC      {self.name}，{self.n_bits} bit {mode}，"
            f"Vref = {self.vref:g} V，{cyc}，{err}"
        )


class IdealADC(Block):
    """虚拟 ADC：无限位，输出浮点，无量化噪声。"""

    kind = "adc_ideal"
    is_adc = True
    name = "理想 ADC"

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.fs = self.num("fs", ctx.fs)
        self.vref = self.num("vref", 3.3)
        self.source_impedance = 0.0

    def capture(self, sig: Spectrum, n_samples: int, noise_v: float = 0.0, limits=None,
                noise=None):
        if not self.fs:
            raise ADCError("使用理想 ADC 时必须给 meta.fs（采样率）")
        t = np.arange(n_samples) / self.fs
        return self.capture_array(sig.eval(t), limits=limits, noise=noise)

    def capture_array(self, voltage, limits=None, noise=None, noise_v: float = 0.0):
        if not self.fs:
            raise ADCError("使用理想 ADC 时必须给 meta.fs（采样率）")
        x = np.asarray(voltage, dtype=float).copy()
        n_samples = len(x)
        if noise is not None:
            x = x + np.asarray(noise, dtype=float)[:n_samples]
        limit_clipped = 0.0
        if limits:
            lo, hi = limits
            before = x
            if lo is not None:
                x = np.maximum(x, lo)
            if hi is not None:
                x = np.minimum(x, hi)
            limit_clipped = float(np.mean(x != before))
        codes = x
        meta = {
            "fs": self.fs,
            "n_bits": None,
            "vref": self.vref,
            "lsb": 0.0,
            "offset_code": 0,
            "ideal": True,
            "clipped": 0.0,
            "adc_noise": 0.0,
            "info": {},
            "name": self.name,
            "differential": False,
            "warnings": [],
            "limit_clipped": limit_clipped,
        }
        return codes, meta

    def process(self, inputs):
        return inputs[0], {"bw": None, "peak_out": inputs[0].peak(), "label": "ADC"}

    def describe(self):
        return f"ADC      {self.name}（无限位，浮点输出），fs = {_eng(self.fs or 0, 'Hz')}"

    def adc_noise(self) -> float:
        return 0.0
