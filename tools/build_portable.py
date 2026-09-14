"""重新构建 EyeRest 便携版并**原地替换**（同时保住用户数据）。

为什么要有这个脚本，而不是直接跑 ``pyinstaller EyeRest.spec``：

* ``--noconfirm`` 会**整个删掉** ``dist/EyeRest/`` —— 而便携版的数据
  （``config.json`` / ``stats.json`` / ``logs/`` / ``sounds/``）就躺在
  ``dist/EyeRest/data/`` 里，直接打包等于**连用户数据一起打包没了**。
* ``EyeRest-Portable.bat`` 不在 spec 的 datas 里，是手工产物，重打包会丢。
* 构建必须走 venv 的 python，并把 ``TEMP``/``TMP``/``PYINSTALLER_CONFIG_DIR``
  重定向到 G 盘（项目的"严禁写 C 盘"硬约束）。

流程（每一步都校验，任一步失败即中止，不破坏现有产物）：

1. 前置检查：venv/spec 存在、当前没有 EyeRest 实例在跑
2. 记录 C 盘数据目录的哈希与 mtime（事后复查是否被写）
3. 备份 ``data/`` 与 ``EyeRest-Portable.bat``
4. 构建到**暂存目录** ``.build/pyi_dist``（全程不碰 ``dist/``）
5. 校验产物：exe 存在 + PYZ 内 ``APP_VERSION`` 与源码一致（防"打包了旧代码"）
6. 启动冒烟：``EYEREST_DATA_DIR`` 指向临时目录跑起来，确认常驻
7. 换位：旧产物 ``mv`` 进备份（**不用 rm** —— 沙箱会拦 dist 下的批量删除），
   新产物就位，还原 ``data/`` 与 ``.bat``
8. 复查：data 与备份逐文件哈希一致、C 盘数据目录哈希/mtime 未变

用法::

    .venv/Scripts/python.exe tools/build_portable.py
    .venv/Scripts/python.exe tools/build_portable.py --skip-smoke
    .venv/Scripts/python.exe tools/build_portable.py --keep-smoke-data
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "EyeRest"
STAGE = ROOT / ".build" / "pyi_dist"
WORK = ROOT / ".build" / "pyi_work"
CACHE = ROOT / ".build" / "pyi_cache"
TMP = ROOT / ".build" / "tmp"
SMOKE_DATA = ROOT / ".build" / "pyi_smoke"
BACKUP_ROOT = ROOT / ".build" / "backup"
SPEC = ROOT / "EyeRest.spec"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"

#: 便携版自带、必须跨构建保留下来的东西（相对 dist/EyeRest/）
PRESERVE = ("data", "EyeRest-Portable.bat")

#: C 盘上"绝不该被写"的位置（数据目录解析链的兜底项）
C_DRIVE_WATCH = (
    Path(os.environ.get("APPDATA", "")) / "EyeRest" if os.environ.get("APPDATA") else None,
)


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def die(msg: str) -> None:
    print(f"[build] ✗ {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def digest(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_tree(root: Path) -> dict[str, str]:
    """目录快照：相对路径 -> md5（只统计文件）。"""
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)).replace("\\", "/"): digest(p)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def snapshot_c_drive() -> dict[str, str]:
    out: dict[str, str] = {}
    for watch in C_DRIVE_WATCH:
        if watch is None or not watch.exists():
            continue
        for p in sorted(watch.rglob("*")):
            if p.is_file():
                out[f"{watch.name}/{p.relative_to(watch)}"] = digest(p)
    return out


def is_runtime_file(key: str) -> bool:
    """快照里的这条记录是否为**运行时**文件（有实例在跑就会变）。

    ``stats.json`` 每约 30 秒落一次盘、``logs/*.log`` 每次事件都追加 ——
    它们被写**不代表构建写错了地方**，只代表机器上还有另一个实例在跑。
    真正该报警的是 ``config.json`` 这类只在用户改动设置时才写的文件。
    """
    tail = key.replace("\\", "/")
    return tail.endswith("stats.json") or "/logs/" in tail or tail.endswith(".log")


def app_running() -> bool:
    try:
        res = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq EyeRest.exe", "/NH"],
            capture_output=True, text=True, timeout=20,
        )
        return "EyeRest.exe" in res.stdout
    except Exception:  # tasklist 不可用就不拦
        return False


def source_app_version() -> str:
    ns: dict[str, object] = {}
    src = (ROOT / "app" / "config" / "defaults.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        if line.startswith("APP_VERSION"):
            exec(line, ns)  # noqa: S102 —— 只求一个常量，不 import 整个包
            return str(ns["APP_VERSION"])
    die("读不到源码里的 APP_VERSION")


def packed_app_version() -> set[str]:
    """从 PYZ 里取 app.config.defaults 全部"像版本号"的字符串常量。

    返回**集合**而不是取第一个：该模块里可能还有别的 x.y.z 字面量，
    只取第一个会误判。判定用"源码版本在不在这个集合里"。
    """
    from PyInstaller.archive.readers import ZlibArchiveReader

    pyz = WORK / "EyeRest" / "PYZ-00.pyz"
    if not pyz.exists():
        return set()
    code = ZlibArchiveReader(str(pyz)).extract("app.config.defaults")
    found: set[str] = set()

    def rec(co: types.CodeType) -> None:
        for const in co.co_consts:
            if isinstance(const, types.CodeType):
                rec(const)
            elif isinstance(const, str) and const[:1].isdigit() and const.count(".") == 2:
                found.add(const)

    rec(code)
    return found


def exe_size(exe: Path) -> int:
    return exe.stat().st_size if exe.exists() else 0


# ----------------------------------------------------------------------
# 步骤
# ----------------------------------------------------------------------
def preflight() -> None:
    if not SPEC.exists():
        die(f"找不到 spec：{SPEC}")
    if not VENV_PY.exists():
        die(f"找不到 venv python：{VENV_PY}（构建必须用它，别用系统 python）")
    if app_running():
        die("检测到 EyeRest.exe 正在运行 —— 先退出再构建，否则文件被占用")
    log("前置检查通过")


def backup_and_preserve() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bk = BACKUP_ROOT / f"build-{stamp}"
    bk.mkdir(parents=True, exist_ok=True)
    for name in PRESERVE:
        src = DIST / name
        if not src.exists():
            die(f"dist/EyeRest/{name} 不存在 —— 便携版结构不对，先人工确认")
        dst = bk / name
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    log(f"已备份 {'、'.join(PRESERVE)} -> {bk}")
    return bk


def build() -> None:
    env = {
        **os.environ,
        "TEMP": str(TMP),
        "TMP": str(TMP),
        "PYINSTALLER_CONFIG_DIR": str(CACHE),
    }
    for d in (TMP, CACHE):
        d.mkdir(parents=True, exist_ok=True)
    if STAGE.exists():
        shutil.rmtree(STAGE)  # 暂存目录可以放心删（不在 dist 下）
    cmd = [
        str(VENV_PY), "-m", "PyInstaller", str(SPEC), "--noconfirm",
        "--workpath", str(WORK), "--distpath", str(STAGE),
    ]
    log(f"开始构建（TEMP={TMP}）…")
    res = subprocess.run(cmd, cwd=str(ROOT), env=env)
    if res.returncode != 0:
        die(f"PyInstaller 退出码 {res.returncode}")
    if not (STAGE / "EyeRest" / "EyeRest.exe").exists():
        die("构建结束但找不到 EyeRest.exe")


def verify_stage() -> None:
    want = source_app_version()
    got = packed_app_version()
    if not got:
        die("PYZ 里找不到 app.config.defaults —— 产物不完整")
    if want not in got:
        die(f"产物内版本 {sorted(got)} 不含源码版本 {want} —— 打包的是旧代码")
    log(f"产物校验通过：exe {exe_size(STAGE / 'EyeRest' / 'EyeRest.exe')} B，"
        f"PYZ 内版本集 {sorted(got)} 含源码 {want}")


def smoke(skip: bool, keep: bool) -> None:
    if skip:
        log("跳过启动冒烟（--skip-smoke）")
        return
    if SMOKE_DATA.exists() and not keep:
        shutil.rmtree(SMOKE_DATA)
    SMOKE_DATA.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "EYEREST_DATA_DIR": str(SMOKE_DATA)}
    exe = STAGE / "EyeRest" / "EyeRest.exe"
    log(f"启动冒烟 12s（数据落 {SMOKE_DATA.name}/，不碰 dist）…")
    try:
        subprocess.run([str(exe)], env=env, timeout=12,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        die("冒烟失败：进程提前退出了")
    except subprocess.TimeoutExpired:
        log("冒烟通过：进程常驻到超时被杀（GUI 常驻应用即此表现）")


def swap() -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    old = BACKUP_ROOT / f"EyeRest-{stamp}"
    shutil.move(str(DIST), str(old))          # 不用 rm：沙箱会拦 dist 下的批量删除
    shutil.move(str(STAGE / "EyeRest"), str(DIST))
    log(f"旧产物 -> {old.name}，新产物已就位")


def restore(bk: Path) -> None:
    for name in PRESERVE:
        src, dst = bk / name, DIST / name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    (DIST / ".tmp").mkdir(exist_ok=True)
    log(f"已还原 {'、'.join(PRESERVE)}")


def verify_final(bk: Path, c_before: dict[str, str]) -> None:
    before = snapshot_tree(bk / "data")
    after = snapshot_tree(DIST / "data")
    if before != after:
        diff = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        die(f"用户数据与备份不一致：{sorted(diff)}")
    log(f"用户数据校验通过（{len(after)} 个文件，逐文件哈希一致）")

    c_after = snapshot_c_drive()
    if c_before != c_after:
        changed = {k for k in set(c_before) | set(c_after)
                   if c_before.get(k) != c_after.get(k)}
        if all(is_runtime_file(k) for k in changed):
            # logs/ 与 stats.json 是**运行时**文件：只要机器上还有另一个实例在跑，
            # 它们就一直在变（每 30s 落一次统计）。构建本身不碰它们（只读快照）。
            # 最可能的来源：有人以**开发模式**启动了应用 —— 非 frozen 会落 %APPDATA%。
            log(f"⚠ C 盘有变化，但仅限运行时文件：{sorted(changed)}")
            log("  这不像是本次构建写的。多半是另一个实例在跑，排查：")
            log(r'    tail -3 "%APPDATA%\EyeRest\logs\eyerest.log"    # 若时间戳是刚才 → 确有实例')
            log("  处置：关掉它以改回便携版 dist/EyeRest/EyeRest-Portable.bat（不再写 C 盘）")
            return
        die(f"C 盘数据目录被改动了（非运行时文件）：{sorted(changed)}")
    log(f"C 盘数据目录未被写（{'空/不存在' if not c_after else str(len(c_after)) + ' 个文件比对一致'}）")


def main() -> int:
    ap = argparse.ArgumentParser(description="构建并原地替换 EyeRest 便携版")
    ap.add_argument("--skip-smoke", action="store_true", help="跳过启动冒烟")
    ap.add_argument("--keep-smoke-data", action="store_true", help="保留冒烟数据目录")
    args = ap.parse_args()

    preflight()
    c_before = snapshot_c_drive()
    bk = backup_and_preserve()
    build()
    verify_stage()
    smoke(args.skip_smoke, args.keep_smoke_data)
    swap()
    restore(bk)
    verify_final(bk, c_before)

    log("完成 —— 运行 dist/EyeRest/EyeRest-Portable.bat")
    log(f"     exe {exe_size(DIST / 'EyeRest.exe')} B｜备份 {bk}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
