"""本地界面服务：静态页 + 两个 JSON 接口。只用标准库。"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chainlab.blocks  # noqa: F401  注册全部模块
from chainlab.blocks.adc import ADCError
from chainlab.core.graph import ChainError
from chainlab.core.runner import run
from chainlab.core.serialize import jsonable
from chainlab.ui.schema import schema

HERE = pathlib.Path(__file__).resolve().parent
INDEX = HERE / "index.html"


class Handler(BaseHTTPRequestHandler):
    server_version = "ChainLab/1.0"

    def log_message(self, fmt, *args):  # 安静点
        pass

    def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        elif self.path.startswith("/api/schema"):
            self._json(schema())
        elif self.path.startswith("/api/ping"):
            self._json({"ok": True})
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if self.path.startswith("/api/filter/preview"):
            self._filter_preview()
            return
        if self.path.startswith("/api/save"):
            self._save()
            return
        if self.path.startswith("/api/load"):
            self._load()
            return
        if self.path.startswith("/api/algo/upload"):
            self._algo_upload()
            return
        if not self.path.startswith("/api/run"):
            self._json({"ok": False, "error": "not found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            cfg = json.loads(self.rfile.read(n) or b"{}")
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": f"请求解析失败：{exc}"})
            return

        try:
            res = run(cfg)
        except (ChainError, ADCError, ValueError) as exc:
            self._json({"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return

        self._json({"ok": True, "result": jsonable(res)})

    def _algo_upload(self):
        import re

        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
            name = str(payload.get("name") or "algo.py")
            name = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name)
            if not name.endswith(".py"):
                name += ".py"
            out_dir = pathlib.Path(__file__).resolve().parents[2] / "algos"
            out_dir.mkdir(exist_ok=True)
            (out_dir / name).write_text(payload.get("text") or "", encoding="utf-8")
            self._json({"ok": True, "path": f"./algos/{name}"})
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def _save(self):
        import yaml

        try:
            n = int(self.headers.get("Content-Length") or 0)
            cfg = json.loads(self.rfile.read(n) or b"{}")
            text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=100)
            self._json({"ok": True, "yaml": text})
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def _load(self):
        import yaml

        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
            cfg = yaml.safe_load(payload.get("yaml") or "")
            if not isinstance(cfg, dict):
                raise ValueError("文件里不是一份链路配置")
            self._json({"ok": True, "cfg": cfg})
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def _netlist_preview(self, spec):
        """按元件值算频响（用户直接给 Z1..Z5）。"""
        import numpy as np

        from chainlab.core.netlist import parse_elem, solve, stats

        try:
            stages = spec.get("zstages")
            if not isinstance(stages, list) or not stages:
                stages = [{"topo": spec.get("topo") or "sk",
                           "z": list(spec.get("z") or [])}]
            from chainlab.core.topologies import NAME, match

            f = np.logspace(1, 8, 500)
            h = np.ones(f.shape, dtype=complex)
            names = []
            for si, st in enumerate(stages, 1):
                zz = [parse_elem(x) for x in (st.get("z") or [])] + [(None, None)] * 5
                zz = zz[:5]
                combo = str(st.get("combo") or "")
                if combo and "_" in combo:
                    tp, ft_want = combo.split("_", 1)
                    ft_map = {"lp": "lowpass", "hp": "highpass", "bp": "bandpass"}
                    ft = ft_map.get(ft_want, "")
                    from chainlab.core.topologies import STD as _STD, CN
                    want = _STD.get((tp, ft))
                    err = ""
                    if not want:
                        err = f"不认识的组合 {combo}"
                    else:
                        for i, (k, _v) in enumerate(zz, start=1):
                            if k != want[f"z{i}"]:
                                err = (f"不是标准格式：{combo.upper()} 的 Z{i} 应是"
                                       f"{CN[want[f'z{i}']]}，你填的是{CN.get(k, k)}")
                                break
                else:
                    ft, err = match(str(st.get("topo") or "sk"), [k for k, _ in zz])
                if err:
                    prefix = f"第 {si} 级：" if len(stages) > 1 else ""
                    return self._json({"ok": False, "error": prefix + err})
                tp = str(st.get("topo") or (combo.split("_")[0] if combo else "sk")).upper()
                names.append(f"{NAME[ft]}（{tp}）")
                h = h * solve(str(st.get("topo") or "sk").lower(), zz, f)
            topo = str(stages[0].get("topo") or "sk").lower()
            mag = 20 * np.log10(np.maximum(np.abs(h), 1e-12))
            st = stats(f, np.abs(h))
            comp = {"可行": True, "拓扑": ("MFB（反相多反馈）" if topo == "mfb"
                                          else "Sallen-Key（单位增益）"),
                    **({"类型": " + ".join(names)} if names else {})}
            if st:
                kd = st.get("kind", "bandpass")
                comp["实际 fc"] = (st["f_hi"] if kd == "lowpass"
                                  else st["f_lo"] if kd == "highpass"
                                  else st["f_peak"])
                comp["实际 Q"] = st["Q"]
            return self._json({"ok": True, "f": f.tolist(), "mag": mag.tolist(),
                               "components": comp, "actual": st, "stages": []})
        except Exception as exc:  # noqa: BLE001
            return self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def _filter_preview(self):
        import numpy as np

        from chainlab.core.block import num as _num
        from chainlab.core.filterdesign import design_pz, pz_response

        try:
            n = int(self.headers.get("Content-Length") or 0)
            spec = json.loads(self.rfile.read(n) or b"{}")
            if str(spec.get("mode")) == "netlist":
                return self._netlist_preview(spec)

            fc = _num(spec.get("fc"), 1e3)
            ftype = str(spec.get("type", "lowpass"))
            order = int(_num(spec.get("order"), 2))
            rip = _num(spec.get("ripple_db"), 0.5)
            att = _num(spec.get("atten_db"), 40.0)
            gain = _num(spec.get("gain"), 1.0)
            qv = _num(spec.get("q"), 0.7071)
            flo = _num(spec.get("f_lo"), None)
            fhi = _num(spec.get("f_hi"), None)
            resp_s = str(spec.get("response", "butterworth"))
            centre = fc if ftype != "bandpass" else float(np.sqrt(flo * fhi))
            lo = max(centre / 500, 0.1)
            hi = centre * 200
            f = np.logspace(np.log10(lo), np.log10(hi), 500)

            if ftype == "bandpass" and flo and fhi:
                # 带通 = 高通段 × 低通段，阶数平分，最高 8 阶
                hn = (order + 1) // 2
                ln = order - hn
                ph, zh, kh = design_pz("highpass", hn, flo, resp_s, rip, 1.0)
                pl, zl, kl = design_pz("lowpass", ln, fhi, resp_s, rip, 1.0)
                mh = np.abs(pz_response(ph, zh, kh, f))
                ml = np.abs(pz_response(pl, zl, kl, f))
                combo = np.maximum(mh * ml, 1e-12)
                # 两级级联后中心会略低于 0 dB（1 阶约 −0.8 dB），
                # 归一化到峰值 0 dB，通带才是平的
                combo = combo / max(combo.max(), 1e-12)
                mag = 20 * np.log10(combo)
            else:
                poles, zeros, k = design_pz(ftype, order, fc, resp_s,
                                            rip, gain, qv, flo, fhi, att)
                mag = 20 * np.log10(
                    np.maximum(np.abs(pz_response(poles, zeros, k, f)), 1e-12))

            from chainlab.core.filterdesign import (components, mfb_highpass,
                                                        mfb_lowpass)

            qv2 = float(qv) if qv else 0.7071
            topo = str(spec.get("topo") or "sk").lower()
            series = str(spec.get("series") or "E24")
            actual = None
            if topo == "mfb" and ftype in ("lowpass", "highpass") and order <= 2:
                comp = (mfb_lowpass(centre, qv2, gain, series=series)
                        if ftype == "lowpass"
                        else mfb_highpass(centre, qv2, 1.0, series=series))
                if spec.get("gbw_comp") and spec.get("gbw"):
                    from chainlab.core.block import num as _num
                    from chainlab.core.filterdesign import mfb_actual
                    gbw_v = _num(spec["gbw"])
                    if gbw_v and ftype == "lowpass":
                        r = mfb_actual(centre, qv2, gain, 1000.0, float(gbw_v),
                                       float(_num(spec.get("aol")) or 1e6))
                        if r.get("可行"):
                            actual = {"f3": r["f3_actual"], "Q": r["Q_actual"]}
                            # 把修正后的值写回 comp，界面上的「实际 fc/Q」才是真的
                            comp["实际 fc"] = r["f3_actual"]
                            comp["实际 Q"] = r["Q_actual"]
                            comp["说明"] = ("已按运放 GBW 修正：目标 %.3g Hz → 实际 %.3g Hz"
                                          % (centre, r["f3_actual"]))
                    elif gbw_v:
                        comp["说明"] = "MFB 高通暂未做 GBW 修正（只对低通推导过）"
            elif topo == "mfb":
                # MFB 只有低通拓扑；其他类型退回 Sallen-Key，并明确说明
                try:
                    comp = components(ftype, min(order, 2), centre, qv2,
                                      series=series)
                    comp["说明"] = "本工具暂未实现 MFB 高通/带通，已自动用 Sallen-Key"
                except Exception:  # noqa: BLE001
                    comp = components("lowpass", 2, centre, qv2, series=series)
                    comp["说明"] = "本工具暂未实现 MFB 高通/带通，已自动用 Sallen-Key"
            else:
                comp = components(ftype, order, centre, qv2, series=series)
            stages = []
            try:
                from chainlab.core.filterdesign import (bandpass_stages,
                                                        stages_components)

                resp_key = str(spec.get("response") or "butterworth").lower()
                if not resp_key.startswith(("chebyshev2", "bessel", "chebyshev1")):
                    resp_key = "butterworth"
                rip_v = float(_num(spec.get("ripple_db")) or 0.5)
                topo_key = "mfb" if topo == "mfb" else "sk"

                if ftype == "bandpass" and flo and fhi:
                    raw = bandpass_stages(flo, fhi, order, topo_key,
                                          gain, series)
                elif ftype in ("lowpass", "highpass") and order > 2:
                    raw = stages_components(order, centre, resp_key, rip_v,
                                            topo_key, gain, series, ftype)
                else:
                    raw = []

                for st in raw:
                    stages.append({
                        "n": st.get("级"), "order": st.get("阶"), "Q": st.get("Q"),
                        "fc": st.get("fc"), "seg": st.get("seg", "低通"),
                        "topo": st.get("topo", "sk"),
                        "R1": st.get("R1"), "R2": st.get("R2"), "R3": st.get("R3"),
                        "C1": st.get("C1"), "C2": st.get("C2"), "C3": st.get("C3"),
                        "fc_actual": st.get("实际 fc"), "q_actual": st.get("实际 Q"),
                        "note": st.get("note", ""),
                    })
            except Exception:  # noqa: BLE001
                stages = []

            # 级联时用第一级的元件撑起 comp，让图有东西可画
            if stages and not comp.get("R1"):
                st0 = stages[0]
                comp = {"可行": True,
                        "拓扑": ("MFB（反相多反馈）" if st0.get("topo") == "mfb"
                                 else "Sallen-Key（单位增益）"),
                        "R1": st0.get("R1"), "R2": st0.get("R2"),
                        "R3": st0.get("R3"),
                        "C1": st0.get("C1"), "C2": st0.get("C2"),
                        "实际 fc": st0.get("fc"), "实际 Q": st0.get("Q"),
                        "说明": comp.get("说明", "")}

            self._json({"ok": True, "f": f.tolist(), "mag": mag.tolist(),
                        "components": comp, "actual": actual, "stages": stages})
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def _free_port(start: int = 8765, tries: int = 30) -> int:
    """找一个真正没人占用的端口。

    注意不能在探测 socket 上开 SO_REUSEADDR —— Windows 下它允许绑定
    一个已经在 LISTEN 的端口，探测会误判为空闲，结果两个服务抢同一个
    端口，浏览器连到哪个全看运气（这就是界面"看不到更新"的根源）。
    """
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


def serve(port: int | None = None, open_browser: bool = True):
    port = port or _free_port()

    class _Server(ThreadingHTTPServer):
        allow_reuse_address = False        # 端口被占就直接报错，不要静默抢

    httpd = _Server(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    # 记下 pid，方便「停止界面.bat」精确关掉本进程，不误伤别的 python
    try:
        (pathlib.Path.cwd() / ".server.pid").write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    print(f"信号链路仿真界面：{url}", flush=True)
    print("按 Ctrl+C 停止，或直接关掉这个窗口。", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
