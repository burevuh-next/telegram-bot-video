import asyncio
import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


class FrigateEvent:
    def __init__(self, data: dict):
        self.id: str = data["id"]
        self.camera: str = data["camera"]
        self.label: str = data["label"]
        self.start_time: float = data.get("start_time", 0)
        self.end_time: float | None = data.get("end_time")
        self.has_clip: bool = data.get("has_clip", False)
        self.has_snapshot: bool = data.get("has_snapshot", False)
        self.top_score: float | None = data.get("top_score")
        self.raw = data

    @property
    def start_time_dt(self) -> datetime:
        return datetime.fromtimestamp(self.start_time)


class FrigateClient:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def _request(self, method: str, path: str, retries: int = 3, **kwargs) -> httpx.Response:
         """Выполнить HTTP-запрос с exponential backoff.
         
         Args:
             method: HTTP метод (GET, POST, etc)
             path: Путь API
             retries: Количество попыток переподключения
             **kwargs: Аргументы для httpx.request()
             
         Returns:
             HTTP-ответ
             
         Raises:
             httpx.HTTPError: Если все попытки исчерпаны
         """
         last_exc: Exception | None = None
         max_delay = 32  # Максимальная задержка 32 секунды
         
         for attempt in range(retries):
             try:
                 resp = await self.client.request(method, path, **kwargs)
                 resp.raise_for_status()
                 return resp
             except httpx.HTTPStatusError as exc:
                 if exc.response.status_code < 500:
                     # Не перезапускаем для 4xx ошибок (клиентские ошибки)
                     raise
                 last_exc = exc
             except httpx.RequestError as exc:
                 last_exc = exc
             
             # Если это не последняя попытка, ждём перед повтором
             if attempt < retries - 1:
                 # exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s (max)
                 delay = min(1.0 * (2 ** attempt), max_delay)
                 logger.warning("Retry %d/%d for %s %s after %.1fs: %s", 
                               attempt + 1, retries, method, path, delay, last_exc)
                 await asyncio.sleep(delay)
         
         # Если дошли сюда, все попытки исчерпаны
         if last_exc:
             raise last_exc
         raise RuntimeError(f"Unexpected error in {method} {path}")

    async def get_events(
         self,
         label: str | None = None,
         camera: str | None = None,
         limit: int = 50,
         after: float | None = None,
     ) -> list[FrigateEvent]:
         params: dict = {"limit": limit}
         if label:
             params["label"] = label
         if camera:
             params["camera"] = camera
         if after:
             params["after"] = after
         try:
             resp = await self._request("GET", "/api/events", params=params)
             return [FrigateEvent(e) for e in resp.json()]
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get events: %s", exc)
             return []
         except (ValueError, KeyError) as exc:
             logger.error("Invalid event data: %s", exc)
             return []

    async def get_thumbnail(self, event_id: str) -> bytes | None:
         try:
             resp = await self._request("GET", f"/api/events/{event_id}/thumbnail.jpg")
             return resp.content
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get thumbnail for %s: %s", event_id, exc)
             return None

    async def get_snapshot(self, event_id: str) -> bytes | None:
         try:
             resp = await self._request("GET", f"/api/events/{event_id}/snapshot.jpg")
             return resp.content
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get snapshot for %s: %s", event_id, exc)
             return None

    async def get_clip(self, event_id: str) -> bytes | None:
         try:
             resp = await self._request("GET", f"/api/events/{event_id}/clip.mp4")
             return resp.content
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get clip for %s: %s", event_id, exc)
             return None

    async def get_latest_snapshot(self, camera: str) -> bytes | None:
         try:
             resp = await self._request("GET", f"/api/{camera}/latest.jpg")
             return resp.content
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get latest snapshot for %s: %s", camera, exc)
             return None

    async def get_cameras(self) -> list[dict]:
         try:
             resp = await self._request("GET", "/api/config")
             config = resp.json()
             return [
                 {"name": name, "enabled": cfg.get("enabled", True)}
                 for name, cfg in config.get("cameras", {}).items()
             ]
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get cameras: %s", exc)
             return []
         except (ValueError, KeyError) as exc:
             logger.error("Invalid config data: %s", exc)
             return []

    async def get_event(self, event_id: str) -> FrigateEvent | None:
         try:
             resp = await self._request("GET", f"/api/events/{event_id}")
             return FrigateEvent(resp.json())
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get event %s: %s", event_id, exc)
             return None
         except (ValueError, KeyError) as exc:
             logger.error("Invalid event data for %s: %s", event_id, exc)
             return None

    async def get_version(self) -> str | None:
         try:
             resp = await self._request("GET", "/api/version")
             return resp.text.strip()
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get version: %s", exc)
             return None

    async def get_stats(self) -> dict | None:
         try:
             resp = await self._request("GET", "/api/stats")
             return resp.json()
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get stats: %s", exc)
             return None
         except (ValueError, KeyError) as exc:
             logger.error("Invalid stats data: %s", exc)
             return None

    async def ptz(self, camera: str, action: str, **params) -> bool:
         try:
             await self._request("POST", f"/api/{camera}/ptz", json={"action": action, **params})
             return True
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("PTZ failed for %s: %s", camera, exc)
             return False

    async def recording_start(self, camera: str) -> bool:
         try:
             await self._request("POST", f"/api/recordings/start/{camera}")
             return True
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to start recording for %s: %s", camera, exc)
             return False

    async def recording_stop(self, camera: str) -> bool:
         try:
             await self._request("POST", f"/api/recordings/stop/{camera}")
             return True
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to stop recording for %s: %s",       except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get cameras: %s", exc)
             return []
         except (ValueError, KeyError) as exc:
             logger.error("Invalid config data: %s", exc)
             return []

    async def get_event(self, event_id: str) -> FrigateEvent | None:
         try:
             resp = await self._request("GET", f"/api/events/{event_id}")
             return FrigateEvent(resp.json())
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get event %s: %s", event_id, exc)
             return None
         except (ValueError, KeyError) as exc:
             logger.error("Invalid event data for %s: %s", event_id, exc)
             return None

    async def get_version(self) -> str | None:
         try:
             resp = await self._request("GET", "/api/version")
             return resp.text.strip()
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get version: %s", exc)
             return None

    async def get_stats(self) -> dict | None:
         try:
             resp = await self._request("GET", "/api/stats")
             return resp.json()
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to get stats: %s", exc)
             return None
         except (ValueError, KeyError) as exc:
             logger.error("Invalid stats data: %s", exc)
             return None

    async def ptz(self, camera: str, action: str, **params) -> bool:
         try:
             await self._request("POST", f"/api/{camera}/ptz", json={"action": action, **params})
             return True
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("PTZ failed for %s: %s", camera, exc)
             return False

    async def recording_start(self, camera: str) -> bool:
         try:
             await self._request("POST", f"/api/recordings/start/{camera}")
             return True
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to start recording for %s: %s", camera, exc)
             return False

    async def recording_stop(self, camera: str) -> bool:
         try:
             await self._request("POST", f"/api/recordings/stop/{camera}")
             return True
         except (httpx.RequestError, httpx.HTTPStatusError) as exc:
             logger.warning("Failed to stop recording for %s: %s", camera, exc)
             return False

    async def close(self):
        await self.client.aclose()
