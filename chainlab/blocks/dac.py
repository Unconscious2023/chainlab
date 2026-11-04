"""DAC：数字码流还原成模拟电压。

与 ADC 相反，DAC 的物理约束是「建立时间能不能塞进一个更新周期」：
更新率超过建立时间允许的上限时，输出还没稳到 1 LSB 就被下一次更新打断。

输出按零阶保持（ZOH）建模：每 Ts = 1/fs 锁存一次码值，之后一直保持到
下一次更新，不插值、不在帧之间连线。频域上，ZOH 等效于 sinc(f/fs) 包络
加半个更新周期的延迟；时域上，`time_apply` 直接做逐点保持。
"""

from __future__ import annotations

import numpy as np

from ..core import devices
from ..core.block import Block, num, register
from .adc import _eng


class DACError(ValueError):
    """DAC 配置自相矛盾或超出器件能力。"""


def _opt_num(v):
    """器件档里的值可能是 'VREF+' 这类文字，取值失败就当没给。"""
    if v is None or isinstance(v, (list, tuple)):
        if isinstance(v, (list, tuple)) and v:
            v = v[-1]
        else:
            return None
    try:
        return num(v)
    except (TypeError, ValueError):
        return None


@register("dac")
class DAC(Block):
    is_dac = True

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        dev = devices.load(params["device"]) if params.get("device") else {}
        cfg = dev.get("dac", {}) or {}
        self.dev = dev
        self.name = dev.get("name") or "自定义 DAC"
        self.part = cfg.get("part") or dev.get("part") or ""
        self.verified = bool(dev.get("verified", True))

        self.n_bits = int(self.num("n_bits", cfg.get("n_bits_default", cfg.get("bits", 12))))
        self.n_bits_options = [int(b) for b in
                               (cfg.get("n_bits_options") or [cfg.get("bits", self.n_bits)])]
        if dev and self.n_bits not in self.n_bits_options:
            raise DACError(
                f"{self.name} 不支持 {self.n_bits} bit（可选 {self.n_bits_options}）"
            )

        # 更新率：面板上填了就用填的；没填则取器件手册上限（没有上限的
        # 自定义器件退回链路采样率）。超速会由 check() 明确报出来，不静默通过。
        self.fs_max = _opt_num(cfg.get("fs_max"))
        fs_given = self.num("fs", None)
        if fs_given:
            self.fs, self.fs_source = float(fs_given), "手动"
        elif self.fs_max:
            self.fs, self.fs_source = float(self.fs_max), "器件上限"
        elif ctx.fs:
            self.fs, self.fs_source = float(ctx.fs), "链路采样率"
        else:
            self.fs, self.fs_source = None, "未指定"
        self.settling = self.num("settling", _opt_num(cfg.get("settling")))

        mode = str(self.params.get("output_mode") or "").lower()
        supported = bool(cfg.get("differential", False))
        if mode in ("differential", "diff", "差分"):
            if not supported:
                raise DACError(f"{self.name} 没有差分输出（手册只有单端输出）")
            self.diff = True
        elif mode in ("single", "单端"):
            self.diff = False
        else:
            self.diff = supported                    # 按器件默认

        self.vref = self.num("vref", _opt_num(cfg.get("vref")))
        self.r_load = self.num("r_load", 50.0)
        i_fs = _opt_num(cfg.get("i_out_fs"))
        self.i_out_fs = self.num("i_out_fs", i_fs)

        vfs = self.num("vfs", _opt_num(cfg.get("vfs")))
        if vfs:
            self.vfs = float(vfs)
        elif self.i_out_fs:
            # 电流输出型：满量程电压由外部负载电阻决定
            self.vfs = self.i_out_fs * self.r_load * (2.0 if self.diff else 1.0)
        elif self.vref:
            self.vfs = self.vref * (2.0 if self.diff else 1.0)
        else:
            self.vfs = 3.3 * (2.0 if self.diff else 1.0)
        self.vfs_amp = self.vfs / 2.0                 # 满量程正弦的峰值

        # 输出噪声：手册可能给电流噪声（pA/√Hz），按负载折成电压噪声
        nd = self.num("noise_density", _opt_num(cfg.get("noise_density")))
        kind = str(cfg.get("noise_density_kind") or "voltage").lower()
        self.noise_density = float(nd) * (self.r_load if kind == "current" else 1.0) if nd else 0.0
        self.noise_density_kind = kind
        self.output_resistance = _opt_num(cfg.get("output_resistance"))

    # ------------------------------------------------------------ 量化
    @property
    def lsb(self) -> float:
        return self.vfs / (2 ** self.n_bits)

    @property
    def offset_code(self) -> int:
        return 2 ** (self.n_bits - 1) if self.diff else 0

    def H(self, f):
        """零阶保持：幅度 sinc(f/fs)，相位延迟半个更新周期。"""
        f = np.asarray(f, dtype=float)
        if not self.fs:
            return np.ones(f.shape, dtype=complex)
        x = f / self.fs
        return np.sinc(x) * np.exp(-1j * np.pi * x)

    def process(self, inputs):
        out = inputs[0].filtered(self.H)
        self._diag = {
            "bw": None,
            "peak_out": out.peak(),
            "label": "DAC",
            "fs": self.fs,
            "fs_source": self.fs_source,
            "zoh": True,
            "settling_margin": (1.0 / self.fs - self.settling)
            if (self.fs and self.settling) else None,
        }
        return out, dict(self._diag)

    # ------------------------------------------------------------ 零阶保持
    def hold(self, x, fs):
        """把按 fs 采样的输入按更新率 fs 锁存（零阶保持，ZOH）。

        每个更新区间 [k·Ts, (k+1)·Ts) 内输出恒等于第 k 个更新时刻锁存的
        输入值：不插值，也不把下一帧的值提前连过来。时段内做逐点保持，
        只有更新时刻那一拍才允许跳变。
        """
        x = np.asarray(x, dtype=float)
        fs_d = self.fs
        if not fs_d or x.size == 0 or fs_d >= fs * (1 - 1e-12):
            # 更新网格不比观测网格慢：每个观测点都会重新锁存
            return x.copy()
        n = x.size
        # 每个细采样点落在第几个更新区间里（0 起）
        k = np.floor(np.arange(n) * (fs_d / fs) + 1e-9).astype(np.int64)
        # 各更新时刻对应的细采样点下标：t = k/fs_d 处的输入值
        latch = np.rint(np.arange(k[-1] + 1) * (fs / fs_d)).astype(np.int64)
        latch = np.clip(latch, 0, n - 1)
        return x[latch][k]

    def time_apply(self, inputs, fs, t):
        """时域：先零阶保持（锁存），再做输出限幅。

        这里不再套 sinc 频响——保持本身就会产生 sinc 包络，重复施加会把
        DAC 的镜像衰减算两遍。
        """
        from ..core.timedomain import clamp

        y = self.hold(inputs[0], fs)
        return clamp(y, self.num("v_lo", None), self.num("v_hi", None))

    # ------------------------------------------------------------ 观测
    def capture(self, sig, n_samples: int, noise_v: float = 0.0, limits=None, noise=None):
        t = np.arange(n_samples) / self.fs
        return self.capture_array(sig.eval(t), limits=limits, noise=noise, noise_v=noise_v)

    def capture_array(self, voltage, limits=None, noise=None, noise_v: float = 0.0):
        """把一段理想输出量化成码值；Scope/Specan 再用 code 还原成电压。"""
        if not self.fs:
            raise DACError("DAC 未指定更新率：在 dac.fs 或 meta.fs 里给一个值")
        x = np.asarray(voltage, dtype=float).copy()
        n_samples = len(x)
        if noise is not None:
            x = x + np.asarray(noise, dtype=float)[:n_samples]
        elif noise_v > 0:
            rng = np.random.default_rng(self.ctx.seed + 1)
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

        lsb = self.lsb
        code = np.rint(x / lsb + self.offset_code)
        top = 2 ** self.n_bits - 1
        clipped = np.mean((code < 0) | (code > top))
        codes = np.clip(code, 0, top).astype(np.int64)

        return codes, {
            "fs": self.fs,
            "n_bits": self.n_bits,
            "vref": self.vref,
            "full_scale_amp": self.vfs_amp,
            "lsb": lsb,
            "offset_code": self.offset_code,
            "ideal": False,
            "clipped": float(clipped),
            "adc_noise": 0.0,
            "info": {"settling": self.settling, "fs_max": self.fs_max,
                     "fs_source": self.fs_source},
            "name": self.name,
            "differential": self.diff,
            "warnings": self.warnings(),
            "code_max": int(codes.max()) if codes.size else 0,
            "code_min": int(codes.min()) if codes.size else 0,
            "noise_injected": float(noise_v),
            "limit_clipped": limit_clipped,
            # 观测端据此按零阶保持画阶梯，不要用直线把相邻帧连起来
            "zoh": True,
            "update_fs": self.fs,
        }

    # ------------------------------------------------------------ 校验
    def check(self) -> list[str]:
        """会让工具拒绝执行的问题：更新率超出手册上限。"""
        out = []
        if self.fs and self.fs_max and self.fs > self.fs_max * (1 + 1e-9):
            out.append(
                f"{self.name} 更新率 {_eng(self.fs, 'Hz')} 超过手册上限 "
                f"{_eng(self.fs_max, 'Hz')}（当前值来自{self.fs_source}）："
                f"请把更新率降到 {_eng(self.fs_max, 'Hz')} 或以下"
            )
        return out

    def warnings(self) -> list[str]:
        out = []
        # 手册的建立时间一般按满量程码跳变定义，与「小码跳变下的最大更新率」
        # 不是同一个条件，所以这里只提示，不判为不可执行。
        if self.fs and self.settling and self.fs * self.settling > 1 + 1e-9:
            out.append(
                f"{self.name} 更新周期 {_eng(1 / self.fs, 's')} 短于手册建立时间 "
                f"{_eng(self.settling, 's')}：满量程码跳变来不及稳到 1 LSB，"
                f"大幅度跳变会带来建立误差（小码跳变仍可按手册更新率更新）"
            )
        return out

    def describe(self):
        mode = "差分" if self.diff else "单端"
        fs = _eng(self.fs, "Hz") if self.fs else "—"
        src = f"（{self.fs_source}）" if self.fs else ""
        st = f"，建立 {_eng(self.settling, 's')}" if self.settling else ""
        return (
            f"DAC      {self.name}，{self.n_bits} bit {mode}，更新率 {fs}{src}，"
            f"零阶保持，满量程 {self.vfs:g} Vpp{st}"
        )


class IdealDAC(Block):
    """虚拟 DAC：无限位，浮点输出，无量化噪声。"""

    kind = "dac_ideal"
    is_dac = True
    name = "理想 DAC"

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.fs = self.num("fs", ctx.fs)
        self.fs_source = "手动" if self.num("fs", None) else "链路采样率"
        self.n_bits = 0
        self.vfs = self.num("vfs", 3.3)
        self.vfs_amp = self.vfs / 2.0
        self.settling = None
        self.fs_max = None
        self.diff = False
        self.vref = self.vfs
        self.lsb = 0.0
        self.offset_code = 0
        self.noise_density = 0.0

    def process(self, inputs):
        out = inputs[0]
        return out, {"bw": None, "peak_out": out.peak(), "label": "DAC"}

    def capture(self, sig, n_samples, noise_v=0.0, limits=None, noise=None):
        t = np.arange(n_samples) / self.fs
        return self.capture_array(sig.eval(t), limits=limits, noise=noise)

    def capture_array(self, voltage, limits=None, noise=None, noise_v=0.0):
        if not self.fs:
            raise DACError("使用理想 DAC 时必须给 meta.fs（更新率）")
        x = np.asarray(voltage, dtype=float).copy()
        if noise is not None:
            x = x + np.asarray(noise, dtype=float)[:len(x)]
        limit_clipped = 0.0
        if limits:
            lo, hi = limits
            before = x
            if lo is not None:
                x = np.maximum(x, lo)
            if hi is not None:
                x = np.minimum(x, hi)
            limit_clipped = float(np.mean(x != before))
        return x, {
            "fs": self.fs, "n_bits": None, "vref": self.vref,
            "full_scale_amp": self.vfs_amp, "lsb": 0.0, "offset_code": 0,
            "ideal": True, "clipped": 0.0, "adc_noise": 0.0, "info": {},
            "name": self.name, "differential": False, "warnings": [],
            "limit_clipped": limit_clipped, "noise_injected": float(noise_v),
            "code_max": 0, "code_min": 0,
            # 理想 DAC 不做零阶保持，观测端按连续曲线画
            "zoh": False, "update_fs": self.fs,
        }

    def describe(self):
        return f"DAC      {self.name}（无限位，浮点输出），fs = {_eng(self.fs or 0, 'Hz')}"
