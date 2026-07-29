import os
import platform
from datetime import datetime
from zoneinfo import ZoneInfo

from src.types.tools import Tool


def execute(timezone: str = "Asia/Shanghai", **kwargs) -> str:
    try:
        now = datetime.now(ZoneInfo(timezone))
        time_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        time_str = f"{datetime.now():%Y-%m-%d %H:%M:%S} (timezone error: {timezone})"

    return (
        f"Current time ({timezone}): {time_str}\n"
        f"OS: {platform.system()} {platform.release()}\n"
        f"Architecture: {platform.machine()}\n"
        f"Hostname: {platform.node()}\n"
        f"Working directory: {os.getcwd()}"
    )


tool = Tool(
    name="get_env_info",
    description="""Get current environment information: time, OS, architecture, hostname, and working directory.

Usage notes:
  - ALWAYS use this tool when handling any time-sensitive content such as news to get the accurate current time.
  - The environment info reflects the actual runtime environment and helps produce more accurate commands""",
    parameters={
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone for displaying current time, e.g. Asia/Shanghai, America/New_York (default: Asia/Shanghai)",
            }
        },
        "required": [],
    },
    execute=execute,
)
