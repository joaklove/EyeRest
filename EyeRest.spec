# -*- mode: python ; coding: utf-8 -*-
# EyeRest PyInstaller 打包配置（V1.0：One-dir 模式）
#
# 打包命令（在 apps/EyeRest 目录下，必须使用项目 venv）：
#     .venv\Scripts\python.exe -m PyInstaller EyeRest.spec --noconfirm
#
# 产物：dist/EyeRest/ 目录（EyeRest.exe + _internal/ 依赖文件夹）
#
# 为什么从 One-file 改为 One-dir（V1.0）：
# - One-file 每次启动都要把 ~115MB 解压到临时目录（默认 C 盘 %TEMP%），
#   产生 _MEIxxxx 垃圾目录，被强杀时还不会自清。
# - One-dir 启动即运行，无解压过程；即使走系统 TEMP 也几乎无写入。
# - 便携性由数据目录机制保证：数据库/日志始终落在 exe 同级 data/。
#
# 说明：
# - console=False：GUI 应用不显示控制台窗口
# - SVG 图标依赖 PySide6.QtSvg 模块（hiddenimports 显式声明）
# - 资源打包至 _internal/app/assets/，代码内 get_resource_path() 兼容

import os

from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['app/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app/assets', 'app/assets'),  # 图标资源（eyerest.svg）
    ],
    hiddenimports=[
        'PySide6.QtSvg',  # SVG 图标支持
        'PySide6.QtMultimedia',  # 提示音播放（QSoundEffect）
        *collect_submodules('pynput'),  # pynput 平台后端为动态导入
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'sqlalchemy'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # One-dir：二进制交给 COLLECT 落盘
    name='EyeRest',
    debug=False,
    strip=False,
    upx=False,
    console=False,  # GUI 应用不显示控制台
    icon='app/assets/icons/eyerest.ico' if os.path.exists('app/assets/icons/eyerest.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='EyeRest',  # 产物目录名 dist/EyeRest/
)
