"""器件表体检：每个 opamp yaml 能不能读、数值在不在合理区间。"""
import pathlib
import sys

import yaml

from chainlab.core.block import num

D = pathlib.Path("chainlab/devices")

BOUNDS = {
    "en": (1e-10, 1e-6, "V/√Hz"),      # 0.1nV ~ 1µV
    "in": (1e-17, 1e-9, "A/√Hz"),      # 0.01fA ~ 1nA
    "gbw": (1e5, 2e10, "Hz"),          # 100kHz ~ 20GHz
    "sr": (0.05, 2e4, "V/µs"),
}


def main():
    bad = 0
    rows = []
    for p in sorted(D.glob("*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not d or d.get("class") != "opamp":
            continue
        op = d.get("opamp") or {}
        name = d.get("name", p.stem)
        if not op:
            continue
        issues = []
        for k, (lo, hi, unit) in BOUNDS.items():
            if k not in op or op[k] in (None, ""):
                continue
            try:
                v = num(op[k])
            except (TypeError, ValueError):
                issues.append(f"{k}={op[k]!r} 不是数字")
                continue
            if not (lo <= v <= hi):
                issues.append(f"{k}={v:.3g} 超出 {lo:.1g}~{hi:.1g} {unit}")
        rows.append((name, op.get("en"), op.get("in"), op.get("gbw"), op.get("sr"),
                     d.get("verified"), issues))
        if issues:
            bad += 1

    print(f"{'器件':<14}{'en':>10}{'in':>10}{'gbw':>10}{'sr':>9}  核对  问题")
    for name, en, inn, gbw, sr, ver, issues in rows:
        def f(x):
            return "—" if x in (None, "") else f"{num(x):.3g}"
        print(f"{str(name):<14}{f(en):>10}{f(inn):>10}{f(gbw):>10}{f(sr):>9}  "
              f"{'✓' if ver else '✗'}    {'; '.join(issues)}")
    print(f"\n共 {len(rows)} 个运放器件，{bad} 个有问题")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
