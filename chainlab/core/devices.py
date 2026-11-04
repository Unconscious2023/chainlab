"""器件档加载：一个型号一个 YAML，加型号不改代码。"""

from __future__ import annotations

import pathlib

import yaml

DEVICE_DIR = pathlib.Path(__file__).resolve().parent.parent / "devices"

_cache: dict[str, dict] = {}


class DeviceError(KeyError):
    pass


def path_of(name: str) -> pathlib.Path:
    return DEVICE_DIR / f"{name.lower()}.yaml"


def load(name: str) -> dict:
    key = name.lower()
    if key in _cache:
        return _cache[key]
    if name in ("__custom__",):
        return {}          # 界面上选的「自定义」：全部参数自己填

    p = path_of(name)
    if not p.exists():
        # 允许用 YAML 里的中文名直接引用
        for cand in DEVICE_DIR.glob("*.yaml"):
            try:
                head = cand.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in head.splitlines()[:5]:
                if line.strip() == f"name: {name}" or line.strip() == f"name: \"{name}\"":
                    p = cand
                    break
            if p.exists() and p == cand:
                break
    if not p.exists():
        raise DeviceError(f"找不到器件档：{name}（期望 {p}）")
    with p.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("name", name)
    _cache[key] = data
    return data


def available() -> list[str]:
    return sorted(p.stem for p in DEVICE_DIR.glob("*.yaml"))
