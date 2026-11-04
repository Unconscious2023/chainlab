import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import numpy as np

import chainlab.blocks  # noqa: F401
from chainlab.core.runner import run


def show(tag, cfg):
    try:
        r = run(cfg)
    except Exception as e:
        print(f"{tag}: 失败 -> {type(e).__name__}: {e}")
        return None
    m = r["metrics"]
    print(f"{tag}: 基波 {m['fund_amp']*1e3:.1f} mV @ {m['fund_hz']:.1f} Hz, "
          f"ENOB {m['enob']:.2f}, 噪声 {r['noise_total']*1e6:.1f} µVrms")
    for w in r["warnings"][:2]:
        print("   提示:", w[:70])
    return r


base = {"name": "t", "fs": "200k", "t_window": "10周期"}

# 1) 白噪声 + 低通：噪声应被低通塑形
cfg = {"meta": base, "nodes": [
    {"id": "s", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "50m"}},
    {"id": "n", "type": "noise", "params": {"psd": "100n"}},
    {"id": "f", "type": "filter", "params": {"mode": "design", "type": "lowpass",
                                             "order": "4", "fc": "5k"}},
    {"id": "a", "type": "adc", "params": {"device": "STM32H743", "n_bits": "16", "vref": "3.3"}},
    {"id": "sp", "type": "specan"},
], "edges": [["s", "n"], ["n", "f"], ["f", "a"], ["a", "sp"]]}
r = show("白噪声+4阶低通", cfg)
if r:
    b = [x for x in r["budget"] if x["kind"] == "noise"]
    print("   白噪声贡献:", round(b[0]["noise"] * 1e6, 2), "µVrms")

# 2) 乘法器：1k × 10k → 9k / 11k
cfg = {"meta": base, "nodes": [
    {"id": "s1", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "100m"}},
    {"id": "s2", "type": "source", "params": {"type": "sine", "f": "10k", "amp": "100m"}},
    {"id": "m", "type": "mult", "params": {"device": "AD835"}},
    {"id": "a", "type": "adc", "params": {"device": "STM32H743", "n_bits": "16", "vref": "3.3"}},
], "edges": [["s1", "m", 0], ["s2", "m", 1], ["m", "a"]]}
try:
    r = run(cfg)
    from chainlab.core.graph import Graph
    from chainlab.core.block import Ctx
    g = Graph(cfg, Ctx(cfg))
    g.propagate()
    comps = sorted({round(f) for f in g.nodes["m"].sig.freqs if abs(f) > 1})
    print("乘法器输出分量(Hz):", comps[:8])
except Exception as e:
    print("乘法器: 失败 ->", type(e).__name__, e)

# 3) 差分 ADC：H743 两路输入
cfg = {"meta": base, "nodes": [
    {"id": "p", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "100m"}},
    {"id": "n", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "100m", "phase": "180"}},
    {"id": "a", "type": "adc", "params": {"device": "STM32H743", "n_bits": "16",
                                          "input_mode": "differential", "vref": "3.3"}},
], "edges": [["p", "a", 0], ["n", "a", 1]]}
r = show("差分（反相相减）", cfg)

# 4) F407 选差分应报错
cfg2 = {"meta": base, "nodes": [
    {"id": "p", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "100m"}},
    {"id": "n", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "100m"}},
    {"id": "a", "type": "adc", "params": {"device": "STM32F407", "input_mode": "differential"}},
], "edges": [["p", "a", 0], ["n", "a", 1]]}
show("F407 差分", cfg2)

# 5) 谐波超过 Nyquist
cfg3 = {"meta": {"name": "t", "fs": "100k", "t_window": "10周期"}, "nodes": [
    {"id": "s", "type": "source", "params": {"type": "square", "f": "1k", "amp": "100m",
                                             "n_harm": "51"}},
    {"id": "a", "type": "adc", "params": {"device": "STM32F407", "vref": "3.3"}},
], "edges": [["s", "a"]]}
show("51 次谐波 @100k", cfg3)
