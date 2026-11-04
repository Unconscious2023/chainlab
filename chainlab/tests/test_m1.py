"""M1 验收用例（对应规格 §9）。

直接运行：python tests/test_m1.py
不依赖 pytest。
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import chainlab.blocks  # noqa: E402,F401
from chainlab.blocks.adc import ADCError  # noqa: E402
from chainlab.core.graph import ChainError  # noqa: E402
from chainlab.core.runner import run  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def case(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append((name, True, ""))
        except Exception as exc:  # noqa: BLE001
            RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        return fn

    return deco


def _base(**meta):
    m = {"name": "t", "fs": 100e3, "t_window": "10周期"}
    m.update(meta)
    return m


def _chain(*blocks):
    return {"meta": _base(), "chain": list(blocks)}


def _expect_raise(exc_type, fn, contains=""):
    try:
        fn()
    except exc_type as e:
        if contains and contains not in str(e):
            raise AssertionError(f"错误信息里没有 {contains!r}：{e}") from None
        return
    raise AssertionError(f"期望抛出 {exc_type.__name__}，但没有")


# ---------------------------------------------------------------- A1
@case("A1 端到端：8× → 滤波 → 加偏置 → ADC → 观测")
def _a1():
    cfg, base = __import__("chainlab.core.runner", fromlist=["load"]).load(
        ROOT / "chainlab" / "examples" / "amp8_chain.yaml"
    )
    res = run(cfg, base)
    assert len(res["chain"]) >= 5, res["chain"]
    assert res["metrics"]["enob"] > 0
    assert "scope" in res["sinks"] and "specan" in res["sinks"]
    assert res["noise_total"] > 0


# ---------------------------------------------------------------- A2
@case("A2 预算表合计 = 各分项平方和开方")
def _a2():
    cfg = _chain(
        {"source": {"type": "sine", "f": 1e3, "amp": 0.1}},
        {"amp": {"device": "OPA227", "G": 8}},
        {"adc": {"device": "STM32F407", "vref": 3.3}},
    )
    res = run(cfg)
    rows = sum(r["noise"] ** 2 for r in res["budget"])
    assert abs(rows ** 0.5 - res["noise_total"]) < 1e-15


# ---------------------------------------------------------------- A6
@case("A6 R_AIN = 10k @ F4 → 反算得 28 周期")
def _a6():
    cfg = _chain(
        {"source": {"type": "sine", "f": 1e3, "amp": 0.1}},
        {"add": {"zout": 10e3}},
        {"adc": {"device": "STM32F407", "vref": 3.3, "sample": {"solve": {"rain": 10e3}}}},
    )
    res = run(cfg)
    assert res["adc"]["info"]["cycles"] == 28, res["adc"]["info"]


# ---------------------------------------------------------------- A7
@case("A7 R_AIN = 1M @ F4 → 拒绝执行并建议加缓冲")
def _a7():
    cfg = _chain(
        {"source": {"type": "sine", "f": 1e3, "amp": 0.1}},
        {"adc": {"device": "STM32F407", "vref": 3.3, "sample": {"solve": {"rain": 1e6}}}},
    )
    _expect_raise(ADCError, lambda: run(cfg), "缓冲")


# ---------------------------------------------------------------- A11
@case("A11 fs 超吞吐（1 MSPS @ 28 周期）→ 报错并给三条出路")
def _a11():
    cfg = _chain(
        {"source": {"type": "sine", "f": 1e3, "amp": 0.1}},
        {"adc": {"device": "STM32F407", "vref": 3.3, "sample": {"solve": {"rain": 10e3}}}},
    )
    cfg["meta"]["fs"] = 1e6
    try:
        run(cfg)
    except ADCError as e:
        msg = str(e)
        assert "①" in msg and "②" in msg and "③" in msg, msg
        assert "750 kHz" in msg, msg          # 吸附到 28 周期后的真实上限
        assert "6.88" in msg, msg             # 降源阻抗的上限
        return
    raise AssertionError("期望 ADCError，但没有抛出")


# ---------------------------------------------------------------- A12
@case("A12 OPA847 配置 G=5 → 报最小稳定增益")
def _a12():
    cfg = _chain(
        {"source": {"type": "sine", "f": 1e3, "amp": 1e-3}},
        {"amp": {"device": "OPA847", "G": 5}},
        {"adc": {"device": "STM32F407", "vref": 3.3}},
    )
    res = run(cfg)
    assert any("振荡" in e for e in res["errors"]), res["errors"]


# ---------------------------------------------------------------- A13
@case("A13 OPA695 在 G=8 用查表带宽，不是 GBW/G")
def _a13():
    cfg = _chain(
        {"source": {"type": "sine", "f": 1e3, "amp": 1e-3}},
        {"amp": {"device": "OPA695", "G": 8}},
        {"adc": {"device": "STM32F407", "vref": 3.3}},
    )
    import chainlab.blocks.amp as A
    from chainlab.core.block import Ctx

    a = A.Amp({"device": "OPA695", "G": 8}, Ctx({}))
    # 手册首页：「GAIN = +8 BANDWIDTH (450MHz)」（OPA695.txt:169）
    assert abs(a.fc - 450e6) < 1e6, a.fc


# ---------------------------------------------------------------- A5
@case("A5 32 kHz 分量 @ fs=50k → 混叠到 18 kHz")
def _a5():
    cfg = {
        "meta": {"name": "alias", "fs": 50e3, "t_window": "2ms"},
        "chain": [
            {"source": {"type": "sine", "f": 32e3, "amp": 1.0}},
            {"adc": {"device": "STM32F407", "vref": 3.3}},
            {"specan": {"n_fft": 65536}},
        ],
    }
    res = run(cfg)
    peak = res["sinks"]["specan"]["峰值频率"]
    assert abs(peak - 18e3) < 1e3, peak


# ---------------------------------------------------------------- 多 ADC / 多 DAC
@case("多 ADC/DAC：各观测点独立，指标仍按第一个 ADC")
def _multi():
    cfg = {
        "meta": _base(),
        "nodes": [
            {"id": "src", "type": "source", "params": {"type": "sine", "f": 1e3, "amp": 0.1}},
            {"id": "a1", "type": "adc", "params": {"device": "STM32F407", "vref": 3.3}},
            {"id": "a2", "type": "adc", "params": {"device": "STM32H743", "n_bits": 16, "vref": 3.3}},
            {"id": "d1", "type": "dac", "params": {"device": "AD9764"}},
            {"id": "sc", "type": "scope", "params": {"source": "d1"}},
            {"id": "sp", "type": "specan", "params": {"source": "a2"}},
        ],
        "edges": [["src", "a1"], ["src", "a2"], ["src", "d1"],
                  ["d1", "sc"], ["a2", "sp"]],
    }
    res = run(cfg)
    assert res["adc_id"] == "a1", res["adc_id"]
    assert [a["id"] for a in res["adcs"]] == ["a1", "a2"], res["adcs"]
    assert res["adcs"][1]["n_bits"] == 16, res["adcs"][1]
    assert [d["id"] for d in res["dacs"]] == ["d1"], res["dacs"]
    assert res["metrics"]["adc_id"] == "a1"
    assert res["sinks"]["scope"]["_source"] == "d1"
    assert res["sinks"]["specan"]["_source"] == "a2"

    # 顶栏「指标 ADC」可切换默认指标基准
    cfg["meta"]["metrics_adc"] = "a2"
    res2 = run(cfg)
    assert res2["adc_id"] == "a2", res2["adc_id"]
    assert res2["metrics"]["adc_id"] == "a2"


# ---------------------------------------------------------------- 虚拟 ADC
@case("未放 ADC → 自动补理想 ADC（浮点、无量化噪声）")
def _ideal():
    cfg = _chain({"source": {"type": "sine", "f": 1e3, "amp": 0.1}})
    res = run(cfg)
    assert res["adc"]["ideal"] is True
    assert res["adc"]["adc_noise"] == 0.0


# ---------------------------------------------------------------- 分压
@case("分压：前级 zout 大时后级输入被拉低")
def _div():
    cfg = _chain(
        {"source": {"type": "sine", "f": 1e3, "amp": 0.1}},
        {"add": {"zout": 1e3}},
        {"amp": {"G": 1, "zin": 1e3, "gbw": 1e9}},
        {"adc": {"device": "STM32F407", "vref": 3.3}},
    )
    res = run(cfg)
    assert any("分压损失" in w for w in res["warnings"]), res["warnings"]


# ---------------------------------------------------------------- 报告
@case("中文报告可生成且包含预算表与未建模清单")
def _report():
    from chainlab.core.report import format_report

    cfg = _chain(
        {"source": {"type": "sine", "f": 1e3, "amp": 0.1}},
        {"amp": {"device": "OPA227", "G": 8}},
        {"adc": {"device": "STM32F407", "vref": 3.3}},
    )
    txt = format_report(run(cfg))
    assert "误差预算" in txt and "未建模" in txt and "有效位数" in txt


# ---------------------------------------------------------------- 分叉汇合
def _sum_cfg(k):
    return {
        "meta": {"name": "两路求和", "fs": 100e3, "t_window": "10周期"},
        "nodes": [
            {"id": "src", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "100m"}},
            {"id": "a1", "type": "amp", "params": {"device": "OPA227", "G": "8"}},
            {"id": "a2", "type": "amp", "params": {"device": "OPA227", "G": "2"}},
            {"id": "sum", "type": "add", "params": {"k": k, "dc": "1.65"}},
            {"id": "adc", "type": "adc", "params": {"device": "STM32F407", "vref": "3.3"}},
        ],
        "edges": [["src", "a1"], ["src", "a2"],
                  ["a1", "sum", 0], ["a2", "sum", 1], ["sum", "adc"]],
    }


@case("A22 两路求和：8× 与 2× 并联后相加 = 10×")
def _a22():
    res = run(_sum_cfg([1, 1]))
    amp = res["metrics"]["fund_amp"]
    assert abs(amp - 1.0) < 1e-6, amp


@case("A23 求和端口增益 k 同时作用于信号与噪声")
def _a23():
    a = run(_sum_cfg([1, 1]))
    b = run(_sum_cfg([0.5, 1]))
    assert abs(b["metrics"]["fund_amp"] - 0.6) < 1e-6
    na = max(r["noise"] for r in a["budget"] if r["kind"] == "amp")
    nb = max(r["noise"] for r in b["budget"] if r["kind"] == "amp")
    assert abs(na / nb - 2.0) < 0.01, (na, nb)


@case("A24 削顶自动切时域，并产生真实谐波")
def _a24():
    cfg = {
        "meta": {"name": "clip", "fs": 200e3, "t_window": "10周期"},
        "nodes": [
            {"id": "s", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "1.0"}},
            {"id": "a", "type": "amp", "params": {"device": "OPA656", "G": "2",
                                                  "v_hi": "1.5", "v_lo": "-1.5"}},
            {"id": "adc", "type": "adc", "params": {"device": "STM32H743", "n_bits": "16",
                                                    "vref": "3.3"}},
        ],
        "edges": [["s", "a"], ["a", "adc"]],
    }
    res = run(cfg)
    assert "时域" in res["mode"], res["mode"]
    assert res["metrics"]["thd"] > 0.05, res["metrics"]["thd"]
    assert 1.5 < res["metrics"]["fund_amp"] < 1.9, res["metrics"]["fund_amp"]


# ---------------------------------------------------------------- 多个观测端
@case("多个示波器/频谱仪：数据来源按连线推导，各路互不干扰")
def _multi_sinks():
    cfg = {
        "meta": {"name": "multi-sink", "fs": 100e3, "t_window": "10周期"},
        "nodes": [
            {"id": "src", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "0.1"}},
            {"id": "a1", "type": "adc", "params": {"device": "STM32F407", "vref": "3.3"}},
            {"id": "a2", "type": "adc", "params": {"device": "STM32H743", "n_bits": "16",
                                                   "vref": "3.3"}},
            {"id": "d1", "type": "dac", "params": {"device": "STM32H723-DAC", "fs": "100k"}},
            {"id": "sc1", "type": "scope", "params": {}},
            {"id": "sc2", "type": "scope", "params": {"t_start": "1m"}},
            {"id": "sp", "type": "specan", "params": {}},
        ],
        # 三个观测端各自接在不同的 ADC/DAC 上，且都不写 source
        "edges": [["src", "a1"], ["src", "a2"], ["src", "d1"],
                  ["a1", "sc1"], ["d1", "sc2"], ["a2", "sp"]],
    }
    res = run(cfg)
    assert res["sink_order"] == ["sc1", "sc2", "sp"], res["sink_order"]
    assert res["sinks"]["sc1"]["_source"] == "a1"
    assert res["sinks"]["sc2"]["_source"] == "d1"      # 上游是 DAC 就观测 DAC
    assert res["sinks"]["sp"]["_source"] == "a2"
    for sid in ("sc1", "sc2", "sp"):
        assert res["sinks"][sid]["_source_mode"] == "wire", res["sinks"][sid]
        assert "连线上游" in res["sinks"][sid]["_source_text"], res["sinks"][sid]["_source_text"]

    # 每路的观测设置独立：sc2 从 1 ms 开始，sc1 从 0 s 开始
    p1, p2 = res["sinks"]["sc1"]["_plot"], res["sinks"]["sc2"]["_plot"]
    assert p1["t0"] == 0.0 and p2["t0"] >= 1e-3, (p1["t0"], p2["t0"])
    # 观测 DAC 的示波器按零阶保持画阶梯，观测 ADC 的不是
    assert p2["step"] is True and p1["step"] is False
    assert res["sinks"]["specan"]["_plot"]["f"] and res["sinks"]["sc1"]["_plot"]["t"]


@case("观测端上游不是 ADC/DAC：给出明确提示，不静默改看第一个 ADC")
def _bad_sink_upstream():
    cfg = {
        "meta": {"name": "bad-sink", "fs": 100e3, "t_window": "10周期"},
        "nodes": [
            {"id": "src", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "0.1"}},
            {"id": "amp", "type": "amp", "params": {"device": "OPA227", "G": "8"}},
            {"id": "a1", "type": "adc", "params": {"device": "STM32F407", "vref": "3.3"}},
            {"id": "sc_ok", "type": "scope", "params": {}},
            {"id": "sc_bad", "type": "scope", "params": {}},
        ],
        "edges": [["src", "amp"], ["amp", "a1"], ["a1", "sc_ok"], ["amp", "sc_bad"]],
    }
    res = run(cfg)
    assert res["sinks"]["sc_ok"]["_source"] == "a1"
    bad = res["sinks"]["sc_bad"]
    assert bad["_source"] is None, bad
    assert "放大器" in bad["错误"] and "不能直接观测" in bad["错误"], bad["错误"]
    assert "_plot" not in bad
    assert any("不能直接观测" in w for w in res["warnings"]), res["warnings"]


@case("多个 DAC 都接观测端：指标并联理想 ADC，各观测端仍看自己的 DAC")
def _multi_dac():
    cfg = {
        "meta": {"name": "two-dac", "fs": 100e3, "t_window": "4周期"},
        "nodes": [
            {"id": "s", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "0.1"}},
            {"id": "d1", "type": "dac", "params": {"device": "STM32F407-DAC", "fs": "20k"}},
            {"id": "d2", "type": "dac", "params": {"device": "STM32H723-DAC", "fs": "100k"}},
            {"id": "sc1", "type": "scope", "params": {}},
            {"id": "sc2", "type": "scope", "params": {}},
        ],
        "edges": [["s", "d1"], ["s", "d2"], ["d1", "sc1"], ["d2", "sc2"]],
    }
    res = run(cfg)
    assert res["sink_order"] == ["sc1", "sc2"], res["sink_order"]
    assert res["sinks"]["sc1"]["_source"] == "d1"
    assert res["sinks"]["sc2"]["_source"] == "d2"
    assert res["sinks"]["sc1"]["_plot"]["step"] is True
    assert res["adc"]["ideal"] is True          # 指标用的理想 ADC 是并联补的


# ---------------------------------------------------------------- DAC 零阶保持
@case("DAC 零阶保持：相邻更新点之间为常值，只在更新时刻跳变")
def _dac_zoh():
    from chainlab.blocks.dac import DAC
    from chainlab.core.block import Ctx

    ctx = Ctx({})
    ctx.fs = 100e3
    d = DAC({"device": "STM32F407-DAC", "fs": "20k"}, ctx)

    fs_i = 200e3                       # 观测网格比更新率快 10 倍
    t = np.arange(60) / fs_i
    x = np.sin(2 * np.pi * 1e3 * t)
    y = d.time_apply([x], fs_i, t)

    # 差分只在 t = k/20k 处非零，其余点为常值（没有插值、没有跨帧连线）
    nz = list(np.nonzero(np.abs(np.diff(y)) > 1e-15)[0])
    assert nz == [9, 19, 29, 39, 49], nz
    for k in range(6):
        seg = y[10 * k: 10 * (k + 1)]
        assert np.allclose(seg, seg[0]), (k, seg)
        if k < 5:
            assert abs(seg[0] - x[10 * k]) < 1e-15, (k, seg[0], x[10 * k])

    # 更新率没填时取器件手册上限；填了超过上限要报错
    assert abs(DAC({"device": "STM32F407-DAC"}, ctx).fs - 1e6) < 1
    over = DAC({"device": "STM32F407-DAC", "fs": "2M"}, ctx)
    assert any("超过手册上限" in e for e in over.check()), over.check()


def main() -> int:
    width = max(len(n) for n, _, _ in RESULTS) + 2
    ok = 0
    for name, passed, msg in RESULTS:
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {name:<{width}} {msg}")
        ok += passed
    print(f"\n{ok}/{len(RESULTS)} 通过")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
