import os
import httpx
from datetime import datetime

from src.types.tools import Tool


def execute(city: str, query_type: str = "now", days: str = "3d", hours: str = "24h",
            date: str = "", lang: str = "zh", unit: str = "m") -> str:
    api_key = os.getenv("WEATHER_API_KEY")
    geo_url = os.getenv("WEATHER_GEO_URL")
    api_host = os.getenv("WEATHER_API_HOST")
    if not all([api_key, geo_url, api_host]):
        return "Weather service not configured. Set WEATHER_API_KEY, WEATHER_GEO_URL, WEATHER_API_HOST"

    with httpx.Client(timeout=10) as client:
        geo_resp = client.get(geo_url, params={"location": city, "key": api_key})
        geo = geo_resp.json()
        if geo.get("code") != "200" or not geo.get("location"):
            return f"City not found: {city}"
        loc_id = geo["location"][0]["id"]
        loc_name = geo["location"][0]["name"]

        endpoint, extra = _resolve_endpoint(query_type, days, hours, date)
        if not endpoint:
            return extra

        params = {"location": loc_id, "key": api_key, "lang": lang, "unit": unit, **extra}
        resp = client.get(f"{api_host}{endpoint}", params=params)
        data = resp.json()
        if data.get("code") != "200":
            return f"Weather query failed: {data.get('code')}"

        return _format(loc_name, query_type, data)


def _resolve_endpoint(query_type: str, days: str, hours: str, date: str):
    if query_type == "now":
        return "/v7/weather/now", {}
    if query_type == "daily":
        if days not in ("3d", "7d", "10d", "15d", "30d"):
            return None, "Invalid days parameter, options: 3d/7d/10d/15d/30d"
        return f"/v7/weather/{days}", {}
    if query_type == "hourly":
        if hours not in ("24h", "72h", "168h"):
            return None, "Invalid hours parameter, options: 24h/72h/168h"
        return f"/v7/weather/{hours}", {}
    if query_type == "history":
        if not date:
            return None, "history mode requires a date parameter (format yyyyMMdd)"
        return "/v7/historical/weather", {"date": date}
    return None, "Invalid query_type, options: now/daily/hourly/history"


def _format(location: str, query_type: str, data: dict) -> str:
    if query_type == "now":
        n = data["now"]
        return (
            f"[{location} Real-time Weather]\n"
            f"Weather: {n['text']}\n"
            f"Temperature: {n['temp']}C, Feels like: {n['feelsLike']}C\n"
            f"Humidity: {n['humidity']}%, Visibility: {n['vis']}km\n"
            f"Wind: {n['windDir']} {n['windScale']} level\n"
            f"Pressure: {n['pressure']}hPa"
        )

    if query_type == "daily":
        lines = [f"[{location} Weather Forecast]"]
        for d in data.get("daily", []):
            lines.append(
                f"\n{d['fxDate']}:\n"
                f"  {d['textDay']}/{d['textNight']}, "
                f"{d['tempMin']}~{d['tempMax']}C\n"
                f"  Wind: {d['windDirDay']} {d['windScaleDay']} level, Humidity: {d['humidity']}%"
            )
        return "\n".join(lines)

    if query_type == "hourly":
        lines = [f"[{location} Hourly Forecast]"]
        for h in data.get("hourly", [])[:12]:
            t = h["fxTime"].split("T")[1][:5]
            lines.append(f"{t} {h['text']} {h['temp']}C {h['windDir']}{h['windScale']} level")
        return "\n".join(lines)

    if query_type == "history":
        d = data.get("weatherDaily", {})
        lines = [
            f"[{location} Historical Weather {d.get('date', '')}]",
            f"Temperature: {d.get('tempMin', '')}~{d.get('tempMax', '')}C",
            f"Humidity: {d.get('humidity', '')}%, Precipitation: {d.get('precip', '0')}mm",
        ]
        hourly = data.get("weatherHourly", [])
        if hourly:
            lines.append("")
            for h in hourly[:6]:
                t = h["time"].split("T")[1][:5] if "T" in h["time"] else h["time"][:5]
                lines.append(f"{t} {h['text']} {h['temp']}C")
        return "\n".join(lines)

    return str(data)


tool = Tool(
    name="get_weather",
    description="Query weather information for a given city",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name, e.g. Beijing, Shanghai, Guangzhou"},
            "query_type": {
                "type": "string",
                "enum": ["now", "daily", "hourly", "history"],
                "description": "Query type: now (real-time), daily (forecast), hourly (hourly), history (historical)",
            },
            "days": {
                "type": "string",
                "enum": ["3d", "7d", "10d", "15d", "30d"],
                "description": "Forecast days for daily mode (default 3d)",
            },
            "hours": {
                "type": "string",
                "enum": ["24h", "72h", "168h"],
                "description": "Forecast hours for hourly mode (default 24h)",
            },
            "date": {
                "type": "string",
                "description": "Historical date in yyyyMMdd format, e.g. 20260525",
            },
            "lang": {"type": "string", "enum": ["zh", "en"], "description": "Language (default zh)"},
            "unit": {"type": "string", "enum": ["m", "i"], "description": "Unit: m (metric) i (imperial) (default m)"},
        },
        "required": ["city"],
    },
    execute=execute,
)
