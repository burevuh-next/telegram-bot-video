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
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def get_events(
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

        resp = self.client.get("/api/events", params=params)
        resp.raise_for_status()
        return [FrigateEvent(e) for e in resp.json()]

    def get_thumbnail(self, event_id: str) -> bytes | None:
        try:
            resp = self.client.get(f"/api/events/{event_id}/thumbnail.jpg")
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            logger.warning("Failed to get thumbnail for %s: %s", event_id, exc)
            return None

    def get_snapshot(self, event_id: str) -> bytes | None:
        try:
            resp = self.client.get(f"/api/events/{event_id}/snapshot.jpg")
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            logger.warning("Failed to get snapshot for %s: %s", event_id, exc)
            return None

    def get_clip(self, event_id: str) -> bytes | None:
        try:
            resp = self.client.get(f"/api/events/{event_id}/clip.mp4")
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            logger.warning("Failed to get clip for %s: %s", event_id, exc)
            return None

    def get_latest_snapshot(self, camera: str) -> bytes | None:
        try:
            resp = self.client.get(f"/api/{camera}/latest.jpg")
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            logger.warning("Failed to get latest snapshot for %s: %s", camera, exc)
            return None

    def get_cameras(self) -> list[dict]:
        try:
            resp = self.client.get("/api/config")
            resp.raise_for_status()
            config = resp.json()
            return [
                {"name": name, "enabled": cfg.get("enabled", True)}
                for name, cfg in config.get("cameras", {}).items()
            ]
        except Exception as exc:
            logger.warning("Failed to get cameras: %s", exc)
            return []

    def get_event(self, event_id: str) -> FrigateEvent | None:
        try:
            resp = self.client.get(f"/api/events/{event_id}")
            resp.raise_for_status()
            return FrigateEvent(resp.json())
        except Exception as exc:
            logger.warning("Failed to get event %s: %s", event_id, exc)
            return None

    def get_version(self) -> str | None:
        try:
            resp = self.client.get("/api/version")
            resp.raise_for_status()
            return resp.text.strip()
        except Exception as exc:
            logger.warning("Failed to get version: %s", exc)
            return None

    def get_stats(self) -> dict | None:
        try:
            resp = self.client.get("/api/stats")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Failed to get stats: %s", exc)
            return None

    def ptz(self, camera: str, action: str, **params) -> bool:
        try:
            resp = self.client.post(f"/api/{camera}/ptz", json={"action": action, **params})
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("PTZ failed for %s: %s", camera, exc)
            return False

    def recording_start(self, camera: str) -> bool:
        try:
            resp = self.client.post(f"/api/recordings/start/{camera}")
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("Failed to start recording for %s: %s", camera, exc)
            return False

    def recording_stop(self, camera: str) -> bool:
        try:
            resp = self.client.post(f"/api/recordings/stop/{camera}")
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("Failed to stop recording for %s: %s", camera, exc)
            return False

    def close(self):
        self.client.close()
