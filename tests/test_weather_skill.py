from pathlib import Path

from daily_agent.skill_catalog import SkillRecord
from daily_agent.skill_handlers import weather
from daily_agent.skill_runtime import SKILL_STATUS_ERROR, SKILL_STATUS_SUCCESS, SkillExecutionContext


def _context(inputs: dict) -> SkillExecutionContext:
    skill = SkillRecord(
        name="weather",
        description="查询天气",
        path=Path("/tmp/weather"),
        source="workspace",
        status="enabled",
        tools=("weather_query",),
        tool_name="weather_query",
        triggers=("天气",),
        execution={"type": "python_callable", "entrypoint": "daily_agent.skill_handlers.weather:run"},
        interaction_mode="react_tool",
    )
    return SkillExecutionContext(
        query="成都今天天气如何",
        user_id="user-1",
        session_id="session-1",
        skill=skill,
        working_directory="/tmp",
        inputs=inputs,
    )


def test_weather_skill_returns_current_weather_and_forecast(monkeypatch):
    responses = {
        weather.GEOCODING_URL: {
            "results": [
                {
                    "name": "成都",
                    "latitude": 30.67,
                    "longitude": 104.07,
                    "country": "中国",
                    "admin1": "四川",
                }
            ]
        },
        weather.FORECAST_URL: {
            "current": {
                "temperature_2m": 28.4,
                "apparent_temperature": 30.1,
                "relative_humidity_2m": 72,
                "weather_code": 61,
                "wind_speed_10m": 8.5,
            },
            "daily": {
                "time": ["2026-07-17", "2026-07-18"],
                "weather_code": [61, 2],
                "temperature_2m_max": [31.0, 32.5],
                "temperature_2m_min": [24.2, 24.8],
                "precipitation_probability_max": [70, 25],
            },
        },
    }
    monkeypatch.setattr(weather, "_request_json", lambda url, params: responses[url])

    result = weather.run(context=_context({"city": "成都", "forecast_days": 2}))

    assert result.status == SKILL_STATUS_SUCCESS
    assert "中国 · 四川 · 成都天气" in result.response_text
    assert "当前 **小雨，28.4°C**" in result.response_text
    assert "2026-07-18：多云" in result.response_text
    assert result.observation["provider"] == "open-meteo"


def test_weather_skill_reports_unknown_city(monkeypatch):
    monkeypatch.setattr(weather, "_request_json", lambda url, params: {"results": []})

    result = weather.run(context=_context({"city": "不存在的城市"}))

    assert result.status == SKILL_STATUS_ERROR
    assert "没有找到城市" in result.response_text


def test_weather_skill_requires_city():
    result = weather.run(context=_context({}))

    assert result.status == SKILL_STATUS_ERROR
    assert "哪个城市" in result.response_text


def test_weather_skill_rejects_forecast_days_out_of_range():
    result = weather.run(context=_context({"city": "成都", "forecast_days": 30}))

    assert result.status == SKILL_STATUS_ERROR
    assert "1 到 7 天" in result.response_text


def test_weather_skill_prefers_real_city_over_same_name_village(monkeypatch):
    responses = {
        weather.GEOCODING_URL: {
            "results": [
                {
                    "name": "海口",
                    "latitude": 26.1,
                    "longitude": 102.2,
                    "country": "中国",
                    "country_code": "CN",
                    "admin1": "云南",
                    "feature_code": "PPL",
                    "population": 2000,
                },
                {
                    "name": "海口",
                    "latitude": 20.04,
                    "longitude": 110.32,
                    "country": "中国",
                    "country_code": "CN",
                    "admin1": "海南",
                    "feature_code": "PPLA",
                    "population": 2_800_000,
                },
            ]
        },
        weather.FORECAST_URL: {
            "current": {},
            "daily": {},
        },
    }
    monkeypatch.setattr(weather, "_request_json", lambda url, params: responses[url])

    result = weather.run(context=_context({"city": "海口"}))

    assert result.status == SKILL_STATUS_SUCCESS
    assert result.observation["location"]["admin1"] == "海南"
    assert "20, 110.3" in result.response_text


def test_weather_skill_requires_city_for_province_name():
    result = weather.run(context=_context({"city": "海南"}))

    assert result.status == SKILL_STATUS_ERROR
    assert "具体城市" in result.response_text
