"""实时天气查询 skill handler。"""

from __future__ import annotations

from typing import Any

import httpx

from daily_agent.errors import DailyAgentError
from daily_agent.skill_runtime import (
    SKILL_STATUS_ERROR,
    SKILL_STATUS_SUCCESS,
    SkillExecutionContext,
    SkillRunResult,
)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_FORECAST_DAYS = 1
MAX_FORECAST_DAYS = 7
PROVINCE_ONLY_NAMES = {
    "海南", "河南", "河北", "湖南", "湖北", "吉林", "辽宁", "山东", "山西", "陕西",
    "四川", "云南", "贵州", "广东", "广西", "浙江", "江苏", "安徽", "福建", "江西",
    "青海", "甘肃",
}

WEATHER_CODE_LABELS = {
    0: "晴",
    1: "大部晴朗",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "较强毛毛雨",
    56: "轻微冻毛毛雨",
    57: "冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻微冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷雨",
    96: "雷雨伴小冰雹",
    99: "雷雨伴强冰雹",
}


def run(*, context: SkillExecutionContext) -> SkillRunResult:
    """根据城市名查询当前天气和未来几天预报。"""
    inputs = dict(context.inputs)
    city = str(inputs.get("city") or "").strip()
    if not city:
        return SkillRunResult(
            status=SKILL_STATUS_ERROR,
            response_text="请告诉我要查询哪个城市的天气。",
            stop_reason="缺少城市名称",
            inputs=inputs,
            clear_session=True,
        )

    try:
        forecast_days = _normalize_forecast_days(inputs.get("forecast_days"))
        location = _search_location(city)
        forecast = _fetch_forecast(location, forecast_days)
        response = _render_response(location, forecast)
        normalized_inputs = {"city": city, "forecast_days": forecast_days}
        return SkillRunResult(
            status=SKILL_STATUS_SUCCESS,
            response_text=response,
            stop_reason="天气查询完成",
            inputs=normalized_inputs,
            output_text=response,
            observation={
                "provider": "open-meteo",
                "location": location,
                "current": forecast.get("current") or {},
                "daily": forecast.get("daily") or {},
            },
            clear_session=True,
        )
    except (DailyAgentError, httpx.HTTPError, ValueError, TypeError) as exc:
        return SkillRunResult(
            status=SKILL_STATUS_ERROR,
            response_text=f"天气查询失败：{exc}",
            stop_reason="天气服务调用失败",
            inputs=inputs,
            observation={"error": str(exc), "provider": "open-meteo"},
            clear_session=True,
        )


def _normalize_forecast_days(raw_value: Any) -> int:
    if raw_value in (None, ""):
        return DEFAULT_FORECAST_DAYS
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise DailyAgentError("预报天数必须是整数。") from exc
    if value < 1 or value > MAX_FORECAST_DAYS:
        raise DailyAgentError(f"预报天数只能是 1 到 {MAX_FORECAST_DAYS} 天。")
    return value


def _search_location(city: str) -> dict[str, Any]:
    normalized_city = city.strip().removesuffix("省")
    if normalized_city in PROVINCE_ONLY_NAMES:
        raise DailyAgentError(f"“{city}”是省级地区，请提供具体城市，避免静默匹配到同名乡镇。")
    payload = _request_json(
        GEOCODING_URL,
        params={"name": city, "count": 5, "language": "zh", "format": "json"},
    )
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise DailyAgentError(f"没有找到城市“{city}”，请换一个更完整的地名。")
    candidates = [item for item in results if isinstance(item, dict)]
    candidates.sort(key=lambda item: _location_score(city, item), reverse=True)
    location = candidates[0] if candidates else None
    if not isinstance(location, dict) or location.get("latitude") is None or location.get("longitude") is None:
        raise DailyAgentError("天气服务返回的城市坐标不完整。")
    if len(candidates) > 1 and _locations_are_ambiguous(city, candidates[0], candidates[1]):
        first = _location_name(candidates[0])
        second = _location_name(candidates[1])
        raise DailyAgentError(f"地点“{city}”存在歧义：{first}、{second}。请补充省份或国家。")
    return location


def _location_score(city: str, location: dict[str, Any]) -> tuple[int, int]:
    query = city.strip().lower().removesuffix("市")
    name = str(location.get("name") or "").strip().lower().removesuffix("市")
    score = 0
    if name == query:
        score += 100
    elif query and (query in name or name in query):
        score += 45
    if str(location.get("country_code") or "").upper() == "CN" and _contains_chinese(city):
        score += 25
    feature_code = str(location.get("feature_code") or "").upper()
    if feature_code.startswith("PPLA"):
        score += 25
    elif feature_code.startswith("PPL"):
        score += 10
    population = _safe_int(location.get("population"))
    if population >= 1_000_000:
        score += 20
    elif population >= 100_000:
        score += 10
    return score, population


def _locations_are_ambiguous(city: str, first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_score = _location_score(city, first)[0]
    second_score = _location_score(city, second)[0]
    if first_score - second_score >= 20:
        return False
    first_region = (first.get("country_code"), first.get("admin1"), first.get("admin2"))
    second_region = (second.get("country_code"), second.get("admin1"), second.get("admin2"))
    return first_region != second_region


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _fetch_forecast(location: dict[str, Any], forecast_days: int) -> dict[str, Any]:
    return _request_json(
        FORECAST_URL,
        params={
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "current": ",".join(
                [
                    "temperature_2m",
                    "apparent_temperature",
                    "relative_humidity_2m",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "daily": ",".join(
                [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_probability_max",
                ]
            ),
            "timezone": "auto",
            "forecast_days": forecast_days,
        },
    )


def _request_json(url: str, *, params: dict[str, Any]) -> dict[str, Any]:
    # 统一设置短超时，避免第三方天气服务异常时拖住整个 Agent 循环。
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        response = client.get(url, params=params, headers={"User-Agent": "SB-Agent/0.1 weather-tool"})
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise DailyAgentError("天气服务返回了无法识别的数据。")
    if payload.get("error"):
        raise DailyAgentError(str(payload.get("reason") or "天气服务返回错误。"))
    return payload


def _render_response(location: dict[str, Any], forecast: dict[str, Any]) -> str:
    current = forecast.get("current") if isinstance(forecast.get("current"), dict) else {}
    daily = forecast.get("daily") if isinstance(forecast.get("daily"), dict) else {}
    location_name = _location_name(location)
    current_label = _weather_label(current.get("weather_code"))

    lines = [
        f"### {location_name}天气",
        "",
        (
            f"当前 **{current_label}，{_number(current.get('temperature_2m'))}°C**，"
            f"体感 {_number(current.get('apparent_temperature'))}°C，"
            f"湿度 {_number(current.get('relative_humidity_2m'))}%，"
            f"风速 {_number(current.get('wind_speed_10m'))} km/h。"
        ),
    ]

    dates = _as_list(daily.get("time"))
    codes = _as_list(daily.get("weather_code"))
    maximums = _as_list(daily.get("temperature_2m_max"))
    minimums = _as_list(daily.get("temperature_2m_min"))
    rain_probabilities = _as_list(daily.get("precipitation_probability_max"))
    if dates:
        lines.extend(["", "#### 预报"])
        for index, date in enumerate(dates):
            lines.append(
                f"- {date}：{_weather_label(_item(codes, index))}，"
                f"{_number(_item(minimums, index))}～{_number(_item(maximums, index))}°C，"
                f"最高降水概率 {_number(_item(rain_probabilities, index))}%"
            )
    lines.extend(
        [
            "",
            f"解析坐标：{_number(location.get('latitude'))}, {_number(location.get('longitude'))}",
            "数据来源：Open-Meteo，天气会随模型更新而变化。",
        ]
    )
    return "\n".join(lines)


def _location_name(location: dict[str, Any]) -> str:
    names = [location.get("country"), location.get("admin1"), location.get("name")]
    compact = [str(item).strip() for item in names if str(item or "").strip()]
    # 城市名有时和省级行政区同名，去重后显示更自然。
    return " · ".join(dict.fromkeys(compact)) or "所选城市"


def _weather_label(raw_code: Any) -> str:
    try:
        code = int(raw_code)
    except (TypeError, ValueError):
        return "天气状况未知"
    return WEATHER_CODE_LABELS.get(code, f"天气代码 {code}")


def _number(value: Any) -> str:
    if value is None:
        return "--"
    if isinstance(value, float):
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return str(value)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _item(values: list[Any], index: int) -> Any:
    return values[index] if index < len(values) else None
