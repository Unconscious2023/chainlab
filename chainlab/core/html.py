"""生成单文件 HTML 报告：双击即可看，不依赖外部资源。"""

from __future__ import annotations

import html
import math


def _esc(s) -> str:
    return html.escape(str(s))


def _num(v, unit="", digits=4):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    for scale, pre in (
        (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
        (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"),
    ):
        if abs(v) >= scale * 0.999:
            return f"{v / scale:.{digits}g} {pre}{unit}"
    return f"{v:.{digits}g} {unit}"


def _spectrum_svg(plot) -> str:
    if not plot or not plot.get("f"):
        return "<p class='muted'>没有频谱数据</p>"
    f = plot["f"]
    db = plot["db"]
    fmax = plot["fs"] / 2.0
    top = max(db) + 6
    bot = top - 110

    w, h = 860, 260
    ml, mr, mt, mb = 52, 16, 12, 30
    iw, ih = w - ml - mr, h - mt - mb

    def x_of(fv):
        return ml + iw * (fv / fmax)

    def y_of(dv):
        t = (max(min(dv, top), bot) - bot) / (top - bot)
        return mt + ih * (1 - t)

    pts = " ".join(f"{x_of(a):.1f},{y_of(b):.1f}" for a, b in zip(f, db))

    grid = []
    for k in range(5):
        dv = bot + (top - bot) * k / 4
        y = y_of(dv)
        grid.append(
            f"<line x1='{ml}' y1='{y:.1f}' x2='{w - mr}' y2='{y:.1f}' "
            f"stroke='#e6e8ec' stroke-width='1'/>"
            f"<text x='{ml - 8}' y='{y + 4:.1f}' text-anchor='end' "
            f"font-size='11' fill='#6b7280'>{dv:.0f}</text>"
        )
    for k in range(5):
        fv = fmax * k / 4
        x = x_of(fv)
        grid.append(
            f"<text x='{x:.1f}' y='{h - 8}' text-anchor='middle' "
            f"font-size='11' fill='#6b7280'>{_num(fv, 'Hz', 3)}</text>"
        )

    return (
        f"<svg viewBox='0 0 {w} {h}' width='100%' role='img'>"
        f"<rect x='{ml}' y='{mt}' width='{iw}' height='{ih}' fill='#fbfcfd'/>"
        + "".join(grid)
        + f"<polyline points='{pts}' fill='none' stroke='#2563eb' stroke-width='1.2'/>"
        f"<text x='14' y='{mt + 12}' font-size='11' fill='#6b7280'>dBFS</text>"
        f"</svg>"
    )


def render(res: dict) -> str:
    m = res["metrics"]
    rows = [r for r in res["budget"] if r["noise"] > 0]
    total = res["noise_total"] or 1e-30
    rows.sort(key=lambda r: -r["noise"])

    KIND = {
        "source": "信号源", "amp": "放大器", "filter": "滤波器",
        "add": "加法器", "adc": "ADC", "dac": "DAC", "scope": "示波器",
        "specan": "频谱仪", "algo": "算法",
    }

    cards = [
        ("SNR", f"{m['snr_db']:.2f} dB" if math.isfinite(m["snr_db"]) else "∞"),
        ("有效位数", f"{m['enob']:.2f} bit"),
        ("总噪声", _num(total, "Vrms")),
        ("基波", _num(m["fund_hz"], "Hz") + " / " + _num(m["fund_amp"], "Vpk")),
    ]
    card_html = "".join(
        f"<div class='card'><div class='k'>{_esc(k)}</div><div class='v'>{_esc(v)}</div></div>"
        for k, v in cards
    )

    chain_html = "".join(
        f"<li>{_esc(line)}</li>" for line in res["chain"]
    )

    budget_html = "".join(
        f"<tr><td>{_esc(KIND.get(r['kind'], r['kind']))}</td>"
        f"<td class='n'>{_num(r['noise'], 'Vrms')}</td>"
        f"<td><div class='bar'><span style='width:{100 * r['noise'] ** 2 / total ** 2:.4f}%'></span></div></td>"
        f"<td class='n'>{100 * r['noise'] ** 2 / total ** 2:.2f}%</td></tr>"
        for r in rows
    )

    plot = None
    for sid in (res.get("sink_order") or list(res["sinks"])):
        v = res["sinks"].get(sid)
        if isinstance(v, dict) and "_plot" in v:
            plot = v["_plot"]

    notes = ""
    if res["warnings"] or res["errors"] or res["risks"]:
        items = [("错误", e) for e in res["errors"]]
        items += [("提示", w) for w in res["warnings"]]
        items += [("未建模", r) for r in res["risks"]]
        notes = "<ul class='notes'>" + "".join(
            f"<li class='{('err' if k == '错误' else 'warn' if k == '提示' else 'risk')}'>"
            f"<b>{k}</b> {_esc(v)}</li>"
            for k, v in items
        ) + "</ul>"

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{_esc(res['name'])} — 信号链路仿真</title>
<style>
:root{{color-scheme:light}}
body{{margin:0;padding:28px;background:#f4f5f7;color:#111827;
  font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:920px;margin:0 auto}}
h1{{font-size:20px;margin:0 0 4px}}
.sub{{color:#6b7280;font-size:13px;margin-bottom:20px}}
.panel{{background:#fff;border:1px solid #e6e8ec;border-radius:10px;padding:16px 18px;margin-bottom:16px}}
h2{{font-size:14px;margin:0 0 12px;color:#374151;font-weight:600}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}}
.card{{background:#fff;border:1px solid #e6e8ec;border-radius:10px;padding:12px 14px}}
.card .k{{color:#6b7280;font-size:12px}}
.card .v{{font-size:17px;font-weight:600;margin-top:4px}}
ul{{margin:0;padding-left:18px}}
li{{margin:3px 0}}
table{{width:100%;border-collapse:collapse}}
td,th{{padding:7px 8px;border-bottom:1px solid #eef0f3;text-align:left}}
th{{color:#6b7280;font-weight:500;font-size:12px}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}
.bar{{height:8px;background:#eef1f6;border-radius:4px;overflow:hidden}}
.bar span{{display:block;height:100%;background:#2563eb}}
.muted{{color:#9ca3af}}
.notes{{padding-left:0;list-style:none;font-size:13px}}
.notes li{{padding:5px 0;border-bottom:1px solid #f1f2f4}}
.notes b{{display:inline-block;min-width:56px;color:#6b7280;font-weight:500}}
.notes li.err b{{color:#b91c1c}}
.notes li.warn b{{color:#b45309}}
.notes li.risk b{{color:#6d28d9}}
</style></head><body><div class="wrap">
<h1>{_esc(res['name'])}</h1>
<div class="sub">{_esc(res['mode'])} · fs = {_num(res['fs'], 'Hz')} ·
时窗 {res['t_seconds'] * 1e3:.4g} ms · {res['n_samples']} 点</div>

<div class="cards">{card_html}</div>

<div class="panel"><h2>链路</h2><ul>{chain_html}</ul></div>

<div class="panel"><h2>误差预算（折算到 ADC 输入）</h2>
<table><thead><tr><th>模块</th><th class="n">噪声</th><th>占比</th><th class="n"></th></tr></thead>
<tbody>{budget_html}</tbody></table></div>

<div class="panel"><h2>频谱</h2>{_spectrum_svg(plot)}</div>

<div class="panel"><h2>提示与未建模项</h2>{notes or "<p class='muted'>无</p>"}</div>

<div class="sub">由 chainlab v1.1 生成</div>
</div></body></html>"""
