"""EyeRest Design Tokens（V0.6「轻奢艺术·温暖治愈」）。

所有页面颜色、字号、圆角、间距统一从这里取，禁止在
Dashboard / Settings / Statistics / MainWindow 散落定义。

配色方向：自然绿主色 + 暖奶油底 + 暖桃强调色 + 深灰文字。
灵感来自植物/自然/手绘插画风格。
"""

from __future__ import annotations

from PySide6.QtGui import QFont

# ----------------------------------------------------------------------
# 颜色（Color）
# ----------------------------------------------------------------------

# 背景
BG_CANVAS = "#FBF5EF"          # 暖奶油主背景（页面底色）
BG_SURFACE = "#FFFFFF"          # 卡片/面板表面
BG_SOFT = "#F5EFE7"             # 弱化底（输入框、禁用区）
BG_RAISED = "#FFFFFF"           # 悬浮态

# 边框
BORDER = "#EAE0D2"              # 暖灰边框
BORDER_SOFT = "#F1E8D9"         # 极淡边框
BORDER_FOCUS = "#26AE89"        # 聚焦边框（主色）

# 文字
TEXT_PRIMARY = "#2D3748"        # 主文字（深灰）
TEXT_SECONDARY = "#718096"      # 次文字
TEXT_MUTED = "#A0AEC0"          # 辅助文字
TEXT_DISABLED = "#CBD5E0"       # 禁用文字
TEXT_INVERTED = "#FFFFFF"       # 按钮/强调块上的反白文字

# 主色（自然绿）
# V0.6.3 校准：对齐展示板「配色方案·主色」实画值 #26AE89（H163.7 S78.2 V68.2）。
# 旧值 #4CCAB8 偏青 7.7°、偏淡 15.8pt、偏亮 11pt，观感是"薄荷青"而非"植物绿"。
# 采样方法与证据见 docs/design/README.md「取色实测 v2」。
PRIMARY = "#26AE89"             # 主色
PRIMARY_HOVER = "#1FA37E"       # 悬浮态
PRIMARY_PRESSED = "#17906E"     # 按下态
PRIMARY_SOFT = "#DDF5EE"        # 极淡主色（卡片背景/Toggle 关闭轨）
PRIMARY_TEXT = "#1F7A61"        # 主色上文字（深绿）

# 辅色（信息蓝——用于远眺/数据点）
INFO = "#5BA8D6"                # 蓝
INFO_SOFT = "#E5F1F9"

# 强调（暖桃——用于活动/激励卡片）
ACCENT = "#FFD9AB"              # 暖桃
ACCENT_DEEP = "#F4B570"         # 深桃
ACCENT_SOFT = "#FFF3E2"         # 极淡桃

# 状态
WARNING = "#E8B86D"             # 暖橙
WARNING_SOFT = "#FCF1DC"
DANGER = "#E57D7D"              # 暖红
DANGER_SOFT = "#FBE7E7"
SUCCESS = "#26AE89"             # 同主色

# 节奏卡片四类（与四层节奏对应）
RHYTHM_BLINK = "#26AE89"        # 眨眼——主色
RHYTHM_LOOK = "#5BA8D6"         # 远眺——信息蓝
RHYTHM_MOVE = "#F4B570"         # 活动——深桃
RHYTHM_DEEP = "#B59CE0"         # 长休——淡紫

# 侧边栏
SIDEBAR_BG = "#F4ECDF"          # 侧边栏暖色底（比主背景略深）
SIDEBAR_ITEM_HOVER = "#EDE0CD"  # 侧边栏项 hover
SIDEBAR_ITEM_ACTIVE_BG = "#FFFFFF"  # 当前页：纯白底
SIDEBAR_ITEM_ACTIVE_TEXT = "#1F7A61"  # 当前页文字：深绿

# 阴影（半透明黑色，柔和）
SHADOW_CARD = "0 2px 8px rgba(45, 55, 72, 0.06)"
SHADOW_RAISED = "0 4px 16px rgba(45, 55, 72, 0.10)"
SHADOW_FOCUS = "0 0 0 3px rgba(38, 174, 137, 0.25)"

# ----------------------------------------------------------------------
# 字体（Typography）
# ----------------------------------------------------------------------

FONT_FAMILY = '"Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif'

# 品牌展示字体（圆体）。由 theme/fonts.load_fonts() 启动时注入真实 family 名，
# 加载失败保持 None（回退 FONT_FAMILY）。
FONT_DISPLAY: str | None = None

DISPLAY = 32   # 大数字（屏幕暴露时长）
H1 = 22        # 页面标题
H2 = 18        # 区块标题
H3 = 16        # 卡片标题
BODY = 14      # 正文
SECONDARY = 13  # 次正文
CAPTION = 12   # 注释

WEIGHT_BOLD = QFont.Weight.Bold          # 600
WEIGHT_MEDIUM = QFont.Weight.Medium       # 500
WEIGHT_REGULAR = QFont.Weight.Normal      # 400

# ----------------------------------------------------------------------
# 间距（Spacing）
# ----------------------------------------------------------------------

SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 20
SPACE_6 = 24
SPACE_8 = 32
SPACE_10 = 40

# ----------------------------------------------------------------------
# 圆角（Radius）
# ----------------------------------------------------------------------

RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14
RADIUS_XL = 20
RADIUS_PILL = 999

# ----------------------------------------------------------------------
# 尺寸（Sizing）
# ----------------------------------------------------------------------

SIDEBAR_WIDTH = 200
WINDOW_DEFAULT_W = 900
WINDOW_DEFAULT_H = 680
WINDOW_MIN_W = 820
WINDOW_MIN_H = 600
