import asyncio
import json
import logging
import threading
import time
from collections.abc import Coroutine
from pathlib import Path

from app.frigate import FrigateClient, FrigateEvent

logger = logging.getLogger(__name__)


class EventMonitor:
    def __init__(
        self,
        client: FrigateClient,
        state_file: str,
        poll_interval: int = 5,
        event_limit: int = 50,
        include_labels: list[str] | None = None,
        exclude_labels: list[str] | None = None,
        include_cameras: list[str] | None = None,
        exclude_cameras: list[str] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self.client = client
        self.state_file = state_file
        self.poll_interval = poll_interval
        self.event_limit = event_limit
        self.include_labels = include_labels or ["person"]
        self.exclude_labels = exclude_labels or []
        self.include_cameras = include_cameras or ["all"]
        self.exclude_cameras = exclude_cameras or []
        self._loop = loop
        self._seen_ids: set[str] = set()
        self._on_event: list[callable] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._load_state()

    def on_event(self, callback: callable):
        self._on_event.append(callback)

    def _load_state(self):
        path = Path(self.state_file)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self._seen_ids = set(data.get("seen_ids", []))
                logger.info("Loaded %d seen event IDs", len(self._seen_ids))
            except Exception as exc:
                logger.warning("Failed to load state: %s", exc)

    def _save_state(self):
        try:
            path = Path(self.state_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"seen_ids": list(self._seen_ids)[-5000:]}))
        except Exception as exc:
            logger.warning("Failed to save state: %s", exc)

    def _should_process(self, event: FrigateEvent) -> bool:
        if "all" not in self.include_cameras and event.camera not in self.include_cameras:
            return False
        if event.camera in self.exclude_cameras:
            return False
        if event.label in self.exclude_labels:
            return False
        if self.include_labels and event.label not in self.include_labels:
            return False
        return True

    def _poll(self):
        while self._running:
            try:
                events = self.client.get_events(limit=self.event_limit)
                for event in events:
                    if event.id not in self._seen_ids:
                        self._seen_ids.add(event.id)
                        if self._should_process(event):
                            logger.info(
                                "New event: %s on %s at %s",
                                event.label,
                                event.camera,
                                event.start_time_dt.isoformat(),
                            )
                            for cb in self._on_event:
                                try:
                                    result = cb(event)
                                    if isinstance(result, Coroutine):
                                        if self._loop:
                                            asyncio.run_coroutine_threadsafe(result, self._loop)
                                        else:
                                            logger.warning("No event loop for async callback")
                                except Exception as exc:
                                    logger.error("Callback error: %s", exc)
                if len(self._seen_ids) > 10000:
                    self._seen_ids = set(list(self._seen_ids)[-5000:])
                self._save_state()
            except Exception as exc:
                logger.error("Poll error: %s", exc)
            time.sleep(self.poll_interval)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        logger.info("Monitor started (poll every %ds)", self.poll_interval)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        self._save_state()
        logger.info("Monitor stopped")
