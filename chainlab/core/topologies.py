"""标准拓扑白名单：只有这些组合才算「标准格式」，其余直接拒绝。"""

# 每个拓扑：Z1..Z5 应有的元件类型（None 表示该位不装）
# 由手册/教材图确定，用途是拦住「随手填的组合」
STD = {
    ("sk", "lowpass"):  {"z1": "R", "z2": "R", "z3": "C", "z4": "C", "z5": None},
    ("sk", "highpass"): {"z1": "C", "z2": "C", "z3": "R", "z4": "R", "z5": None},
    ("mfb", "lowpass"): {"z1": "R", "z2": "R", "z3": "R", "z4": "C", "z5": "C"},
    ("mfb", "highpass"): {"z1": "C", "z2": "C", "z3": "C", "z4": "R", "z5": "R"},
    # 二阶 MFB 带通（教材 18.5.1）：R1 串、C 串到 −、反馈 C 到 A、R3 下地、R2 反馈
    ("mfb", "bandpass"): {"z1": "R", "z2": "C", "z3": "C", "z4": "R", "z5": "R"},
}

CN = {"R": "电阻", "C": "电容", None: "空（开路）"}


def check(topo: str, ftype: str, kinds: list) -> str:
    """kinds = [Z1..Z5 的类型字符]。返回错误说明，空字符串表示通过。"""
    key = ((topo or "sk").lower(), (ftype or "lowpass").lower())
    if key not in STD:
        return f"不是标准格式：{topo} 拓扑不支持 {ftype} 类型"
    want = STD[key]
    for i, k in enumerate(kinds[:5], start=1):
        exp = want[f"z{i}"]
        if k != exp:
            return (f"不是标准格式：{(topo or 'sk').upper()} "
                    f"{ftype} 的 Z{i} 应是{CN[exp]}，你填的是{CN.get(k, k)}")
    return ""


def match(topo: str, kinds: list):
    """按 5 个 Z 的类型反推这是哪种标准滤波器。返回 (ftype, 错误说明)。"""
    t = (topo or "sk").lower()
    cands = {k[1]: v for k, v in STD.items() if k[0] == t}
    if not cands:
        return "", f"不是标准格式：不认识的拓扑 {topo}"
    for ft, want in cands.items():
        if all(kinds[i] == want[f"z{i+1}"] for i in range(5)):
            return ft, ""
        # SK 允许退化成一阶：Z2、Z3 都不装，只剩 Z1 串 + Z4 下地
        if t == "sk" and kinds[1] is None and kinds[2] is None \
                and kinds[0] == want["z1"] and kinds[3] == want["z4"]:
            return ft, ""
    best = None
    for ft, want in cands.items():
        diff = sum(1 for i in range(5) if kinds[i] != want[f"z{i+1}"])
        if best is None or diff < best[0]:
            best = (diff, ft, want)
    _, ft, want = best
    for i in range(5):
        if kinds[i] != want[f"z{i+1}"]:
            return "", (f"不是标准格式：{t.upper()} {ft} 的 Z{i+1} 应是"
                        f"{CN[want[f'z{i+1}']]}，你填的是{CN.get(kinds[i], kinds[i])}")
    return ft, ""


NAME = {"lowpass": "低通", "highpass": "高通", "bandpass": "带通"}
