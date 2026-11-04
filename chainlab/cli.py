"""命令行入口：python -m chainlab.cli run examples/amp8_chain.yaml"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

import chainlab.blocks  # noqa: F401  导入即注册全部模块
from chainlab.blocks.adc import ADCError
from chainlab.core.graph import ChainError
from chainlab.core.report import format_report
from chainlab.core.runner import load, run


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, float) and not np.isfinite(o):
        return None
    return str(o)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(prog="chainlab", description="信号链路仿真 v1.1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="跑一条链路并输出中文报告")
    p.add_argument("config", help="YAML 配置路径")
    p.add_argument("--json", metavar="PATH", help="同时把结构化结果写到 JSON")
    p.add_argument("--html", metavar="PATH", help="同时生成可双击查看的 HTML 报告")
    p.add_argument("--quiet", action="store_true", help="不打印文字报告")
    args = ap.parse_args(argv)

    try:
        cfg, base = load(args.config)
        res = run(cfg, base)
    except (ChainError, ADCError, ValueError, OSError) as exc:
        print(f"拒绝执行：{exc}")
        return 2

    if not args.quiet:
        print(format_report(res))

    if args.html:
        from chainlab.core.html import render

        path = pathlib.Path(args.html)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(res), encoding="utf-8")
        if not args.quiet:
            print(f"结构化结果已写入：{path}")
        else:
            print(path.resolve())

    if args.json:
        path = pathlib.Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_jsonable(res), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结构化结果已写入：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
