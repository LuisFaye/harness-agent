from __future__ import annotations

import httpx

from daily_agent.errors import ExternalServiceError


def raise_for_api_error(response: httpx.Response, service_name: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            payload = response.json()
            detail = f": {payload}"
        except ValueError:
            if response.text:
                detail = f": {response.text[:300]}"
        raise ExternalServiceError(
            f"{service_name} 接口返回 {response.status_code}{detail}"
        ) from exc
