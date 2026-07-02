from __future__ import annotations

from typing import Any
import requests


class APIClient:
    def __init__(self, base_url: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def call(self, capability: dict, args: dict[str, Any]) -> dict[str, Any]:
        path = capability["path"]
        query: dict[str, Any] = {}
        for key, value in args.items():
            token = "{" + key + "}"
            if token in path:
                path = path.replace(token, str(value))
            else:
                query[key] = value
        url = f"{self.base_url}{path}"
        method = capability["method"].lower()
        if method == "get":
            response = requests.get(url, params=query, timeout=self.timeout)
        else:
            response = requests.request(method, url, json=query, timeout=self.timeout)
        response.raise_for_status()
        return {
            "request": {"method": capability["method"], "url": response.url, "args": args},
            "data": response.json(),
        }
