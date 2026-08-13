import re

ANSI_RE = re.compile(
    r"\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[()#][\x30-\x7e]"
    r"|\x1b[\x40-\x5f]"
    r"|\x9b[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
    r"|\x1b"
)
C0_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_ansi(text: str) -> str:
    if not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "")
    text = ANSI_RE.sub("", text)
    return C0_RE.sub("", text)
