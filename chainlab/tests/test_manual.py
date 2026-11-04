"""用手册表格反查：工具算出的转换时间应当与 RM 给的例子一致。"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import chainlab.blocks  # noqa: E402,F401
from chainlab.core import devices  # noqa: E402
from chainlab.core.block import Ctx  # noqa: E402
from chainlab.blocks.adc import ADC  # noqa: E402

# RM0433 表 194：Fadc_ker_ck = 24 MHz，采样 1.5 周期
cases = [
    ("STM32H743", 24e6, 16, 1.5, 416.7),
    ("STM32H743", 24e6, 14, 1.5, 375.0),
    ("STM32H743", 24e6, 12, 1.5, 333.3),
    ("STM32H723-ADC3", 30e6, 12, 2.5, 500.0),
    ("STM32H723-ADC3", 30e6, 10, 2.5, 433.3),
]

print(f"{'器件':<16}{'位数':>4}{'采样':>7}{'合计(ns)':>10}{'手册(ns)':>10}  结果")
ok = 0
for dev, fadc, bits, cyc, want in cases:
    ctx = Ctx({})
    ctx.fs = 1e6
    d = devices.load(dev)
    a = ADC({"device": dev, "n_bits": str(bits), "f_adc": str(fadc),
             "sample": {"cycles": str(cyc)}}, ctx)
    info = a.resolve()
    got = info["t_total"] * 1e9
    good = abs(got - want) / want < 0.002
    ok += good
    print(f"{dev:<16}{bits:>4}{cyc:>7}{got:>10.1f}{want:>10.1f}  {'✓' if good else '✗'}")

print(f"\n{ok}/{len(cases)} 与手册一致")
