_USER_BG = "#1A1A1A"
_THINKING_TITLE = "#5B9BD5"
_THINKING_BODY = "#9B9B9B"
_TOOL_TITLE = "#808080"
_TOOL_HEADER = "#808080"
_TOOL_ERROR = "#A75252"
_COMPACTING = "#FFA500"
_ERROR = "#FF5555"

_INLINE_CODE_FG = "#6A9955"
_HEADING_FG = "#FFA500"
_HEADING_1_STYLE = "bold #FFA500"
_HEADING_3_FG = "#9CCBFF"
_ITALIC_FG = "#FFB6C1"
_QUOTE_FG = "#9B9B9B"
_HR_FG = "#555555"
_LINK_FG = "#0178D4"
_LINE_NO_FG = "#858585"
_FENCE_BG = "#1A1A1A"
_OPEN_FENCE_FG = "#808080"
_DIFF_DEL_FG = "#FF9E9E"
_DIFF_DEL_BG = "#251F1F"
_DIFF_ADD_FG = "#9FD28A"
_DIFF_ADD_BG = "#1D271D"
_TABLE_BORDER = "#666666"

_MCP_DOT = "●"

_LOGO_LAYER_COLORS = {
    1: "#00ffff",
    2: "#00f0ff",
    3: "#00e0ff",
    4: "#00ccff",
    5: "#00bcff",
    6: "#00acff",
    7: "#0099ff",
}

_LOGO_DEFAULT_COLOR = "#5B5B5B"


def _lerp_hex(c1: str, c2: str, t: float) -> str:
    r = int(c1[1:3], 16) + (int(c2[1:3], 16) - int(c1[1:3], 16)) * t
    g = int(c1[3:5], 16) + (int(c2[3:5], 16) - int(c1[3:5], 16)) * t
    b = int(c1[5:7], 16) + (int(c2[5:7], 16) - int(c1[5:7], 16)) * t
    return f"#{round(r):02x}{round(g):02x}{round(b):02x}"
