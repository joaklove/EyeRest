"""主色校准前后对比渲染（真机装配，不进入事件循环）。

用于验证 tokens 主色系改动：同一装配流程渲染两次，只需在两次之间
用 ``git stash push -- app/ui/theme/tokens.py app/ui/visual_cue.py app/assets/illustrations``
切换旧/新配色。

用法::

    .venv/Scripts/python.exe tools/render_theme_compare.py after
    .venv/Scripts/python.exe tools/render_theme_compare.py before

产出::

    .build/tmp/<prefix>_main.png        MainWindow 首页
    .build/tmp/<prefix>_cue.png         VisualCuePopup（眨眼提示）
    .build/tmp/<prefix>_swatch.png      主色系色块卡（对照展示板色卡）

⚠️ 必须用默认 windows 平台（不要设 QT_QPA_PLATFORM=offscreen），
否则 CJK 字形缺失会渲染成方块；窗口用 WA_DontShowOnScreen 避免闪窗。
数据目录重定向到 .build/render_data，避免写入 C 盘。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.pop("QT_QPA_PLATFORM", None)  # 用默认 windows 平台，保证 CJK 字体渲染

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_DATA_DIR = _ROOT / ".build" / "render_data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ["EYEREST_DATA_DIR"] = str(_DATA_DIR)

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.blink_engine import BlinkEngine  # noqa: E402
from app.core.break_engine import BreakEngine  # noqa: E402
from app.core.clock import FakeClock  # noqa: E402
from app.core.event_bus import EventBus  # noqa: E402
from app.core.move_engine import MoveEngine  # noqa: E402
from app.core.screen_session import ScreenSessionEngine  # noqa: E402
from app.services.break_service import BreakService  # noqa: E402
from app.services.stats_store import get_stats_store  # noqa: E402
from app.ui.dashboard import Dashboard  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.theme import tokens  # noqa: E402
from app.ui.visual_cue import VisualCuePopup  # noqa: E402

_OUT = _ROOT / ".build" / "tmp"
_OUT.mkdir(parents=True, exist_ok=True)


class _FakeTimer:
    def __init__(self) -> None:
        self.active_seconds = 1680.0  # 28 分钟，让首页有内容

    def get_active_seconds(self) -> float:
        return self.active_seconds

    def get_app_uptime(self) -> float:
        return 3600.0

    def reset(self) -> None:
        self.active_seconds = 0.0


class _FakeUsage:
    @staticmethod
    def format_duration(seconds: float) -> str:
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @staticmethod
    def format_short_duration(seconds: float) -> str:
        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"

    @staticmethod
    def get_today_summary() -> dict:
        return {"break_count": 12, "skipped_breaks": 1}


def _swatch_card() -> QPixmap:
    """把当前 tokens 的主色系画成一张对照卡（与展示板色卡同构）。"""
    items = [
        ("PRIMARY", tokens.PRIMARY),
        ("PRIMARY_HOVER", tokens.PRIMARY_HOVER),
        ("PRIMARY_PRESSED", tokens.PRIMARY_PRESSED),
        ("PRIMARY_SOFT", tokens.PRIMARY_SOFT),
        ("PRIMARY_TEXT", tokens.PRIMARY_TEXT),
        ("ACCENT", tokens.ACCENT),
        ("TEXT_PRIMARY", tokens.TEXT_PRIMARY),
    ]
    w, h, pad = 620, 150, 24
    pm = QPixmap(w, h)
    pm.fill(QColor("#FFFFFF"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    step = (w - pad * 2) // len(items)
    for i, (name, hexv) in enumerate(items):
        cx = pad + i * step
        p.setBrush(QColor(hexv))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx, 30, 44, 44))
        f = QFont("Segoe UI", 8)
        p.setFont(f)
        p.setPen(QColor("#2D3748"))
        p.drawText(QRectF(cx - 18, 82, 84, 16), Qt.AlignmentFlag.AlignCenter, name)
        p.setPen(QColor("#718096"))
        p.drawText(QRectF(cx - 18, 100, 84, 16), Qt.AlignmentFlag.AlignCenter, hexv)
    p.end()
    return pm


def main() -> int:
    prefix = sys.argv[1] if len(sys.argv) > 1 else "render"

    app = QApplication([])
    from app.ui.theme.fonts import load_fonts

    load_fonts()
    bus = EventBus()
    svc = BreakService(event_bus=bus)
    settings = svc.get_all_settings()

    clock = FakeClock()
    session = ScreenSessionEngine(clock=clock, idle_provider=lambda: 0.0, event_bus=bus)
    blink = BlinkEngine(
        clock=clock, session_engine=session, event_bus=bus,
        cycle_seconds=settings["blink_cycle_seconds"],
        cue_interval=settings["blink_cue_interval"],
    )
    move = MoveEngine(
        clock=clock, session_engine=session, event_bus=bus,
        interval=settings["move_interval"],
    )
    timer = _FakeTimer()
    engine = BreakEngine(
        timer_engine=timer, state_machine=None, event_bus=bus, session_engine=session,
    )
    engine.start()
    for _ in range(300):
        clock.advance(1.0)
        session.tick()
        blink.tick()
        move.tick()
        engine.tick()

    stats = get_stats_store()
    window = MainWindow(
        timer_engine=timer,
        break_engine=engine,
        state_machine=None,
        usage_service=_FakeUsage(),
        break_service=svc,
        event_bus=bus,
        screen_session_engine=session,
        blink_engine=blink,
        move_engine=move,
        blink_stats_provider=lambda: stats.today().get("blink_cue", 0),
    )
    window.connect_dashboard_actions()
    window.resize(900, 680)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    app.processEvents()
    if window._dashboard is not None:
        window._dashboard.update_display()
    app.processEvents()
    p1 = _OUT / f"{prefix}_main.png"
    window.grab().save(str(p1))

    cue = VisualCuePopup()
    cue.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    cue.apply_appearance(skin="cartoon", intensity="standard", position_locked=False)
    cue.show_cue(kind="blink", duration=3.0)
    app.processEvents()
    p2 = _OUT / f"{prefix}_cue.png"
    cue.grab().save(str(p2))
    cue.hide_cue()

    p3 = _OUT / f"{prefix}_swatch.png"
    _swatch_card().save(str(p3))

    print(f"[{prefix}] PRIMARY={tokens.PRIMARY} HOVER={tokens.PRIMARY_HOVER} "
          f"PRESSED={tokens.PRIMARY_PRESSED} SOFT={tokens.PRIMARY_SOFT} "
          f"TEXT={tokens.PRIMARY_TEXT}")
    for p in (p1, p2, p3):
        print("  ->", p.relative_to(_ROOT), p.stat().st_size, "bytes")
    window.hide()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
