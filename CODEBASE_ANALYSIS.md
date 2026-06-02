# Telegram Bot Video Surveillance - Comprehensive Codebase Analysis

## 1. PROJECT STRUCTURE OVERVIEW

### Directory Layout
```
telegram-bot-video/
├── app/
│   ├── __init__.py          (empty)
│   ├── main.py              (83 lines)  - Entry point, event loop setup
│   ├── bot.py               (463 lines) - Telegram bot handlers and notifications
│   ├── config.py            (66 lines)  - Configuration loading and validation
│   ├── frigate.py           (165 lines) - Frigate API client with retry logic
│   └── monitor.py           (161 lines) - Event polling and filtering
├── config.yml               - Runtime configuration
├── config.yml.example       - Configuration template
├── requirements.txt         - Python dependencies
├── Dockerfile               - Container image
├── docker-compose.yml       - Docker orchestration
├── .gitignore               - Git ignore rules
└── .dockerignore           - Docker build ignore rules
```

### File Statistics
- Total Python Code: 938 lines
- Modules: 5 (main, bot, config, frigate, monitor)
- Python Version: 3.12+
- No test files present

---

## 2. FILE PURPOSES AND RELATIONSHIPS

### Architecture Overview
```
main.py (Entry Point)
  ├── Creates Config from file/env
  ├── Initializes FrigateClient (API calls to Frigate)
  ├── Initializes EventMonitor (polling and filtering)
  ├── Initializes TelegramNotifier (bot handlers + notifications)
  └── Manages lifecycle (startup, shutdown, signal handling)

config.py (Configuration)
  └── Loads from YAML and environment variables
      └── Validates required fields (telegram_token, chat_id)

frigate.py (API Client)
  └── FrigateClient class
      ├── Manages httpx.AsyncClient
      ├── Implements retry logic with exponential backoff
      └── Provides methods for events, snapshots, clips, stats

monitor.py (Event Polling)
  └── EventMonitor class
      ├── Polls Frigate API at intervals
      ├── Filters events by camera/label
      ├── Maintains event history in state file
      └── Triggers callbacks for new events

bot.py (Telegram Interface)
  └── TelegramNotifier class
      ├── Command handlers (/start, /cameras, /subscribe, etc.)
      ├── Sends notifications for detected events
      ├── Manages quiet hours and mute status
      └── Dynamic camera-specific commands
```

### Data Flow
1. **Event Detection Loop**: `monitor.py:_poll()` → Frigate API → New events detected
2. **Notification**: `monitor._on_event` callback → `bot.send_event_notification()`
3. **State Persistence**: `monitor._seen_ids` → `state.json` (5000 most recent)
4. **User Commands**: Telegram message → `bot.cmd_*()` → Frigate API responses

---

## 3. CODE QUALITY ISSUES AND BUGS

### CRITICAL ISSUES

#### 1. **Race Condition in EventMonitor** 
**File**: `app/monitor.py` (Lines 69-117)
**Severity**: HIGH

Non-async methods modify shared state that's accessed by the async polling loop:
```python
# UNSAFE - These are sync methods called from Telegram handlers
def set_include_labels(self, labels: list[str]):
    self.include_labels = labels  # Modified while _poll() reads it

def add_include_label(self, label: str) -> str:
    if label in self.include_labels:  # Read check
        return "уже есть"
    self.include_labels.append(label)  # Write operation
```

**Problem**: 
- `TelegramNotifier.cmd_subscribe()` calls `monitor.add_include_label()` (sync)
- Meanwhile, `monitor._poll()` (async loop) reads `self.include_labels`
- No locking mechanism exists

**Risk**: 
- List corruption
- Missed event filtering
- Potential crashes from concurrent modifications

**Impact**: Medium - Unlikely in practice due to Python's GIL, but architecturally incorrect

---

#### 2. **Hardcoded Retry Logic Without Configuration**
**File**: `app/frigate.py` (Lines 32-49)
**Severity**: MEDIUM

```python
async def _request(self, method: str, path: str, retries: int = 3, **kwargs):
    for attempt in range(retries):
        try:
            # ... request logic ...
            if attempt < retries - 1:
                delay = 1.0 * (2 ** attempt)  # Hardcoded: 1, 2, 4 seconds
                await asyncio.sleep(delay)
```

**Problems**:
- No exponential backoff cap (could hit Python's timeout limits)
- `retries` default hard to customize
- Retries only on 5xx errors, not network timeouts
- No jitter to prevent thundering herd

---

#### 3. **Bare Exception Catching Throughout Frigate Client**
**File**: `app/frigate.py` (Lines 68, 76, 84, 92, 100, 112, 120, 128, 136, 144, 152, 160)
**Severity**: MEDIUM

```python
async def get_events(...):
    try:
        resp = await self._request("GET", "/api/events", params=params)
        return [FrigateEvent(e) for e in resp.json()]
    except Exception as exc:  # TOO BROAD
        logger.warning("Failed to get events: %s", exc)
        return []
```

**Problems**:
- Catches `KeyboardInterrupt`, `SystemExit`, `MemoryError`, `asyncio.CancelledError`
- Hides unexpected exceptions
- Makes debugging difficult
- Could suppress resource exhaustion errors

**Fix**: Catch specific exceptions (`httpx.HTTPError`, `ValueError`, `KeyError`)

---

#### 4. **Quiet Hours Logic Bug (Midnight Wrap-Around)**
**File**: `app/bot.py` (Lines 327-336)
**Severity**: LOW

```python
if self._quiet_start and self._quiet_end:
    now = datetime.now().time()
    if self._quiet_start <= self._quiet_end:  # Normal case: 10:00 - 18:00
        if self._quiet_start <= now <= self._quiet_end:
            return
    else:  # Midnight wrap: 22:00 - 06:00
        if now >= self._quiet_start or now <= self._quiet_end:
            return
```

**Issue**: Logic is correct but could be clearer. The comparison works because Python's `time` objects are orderable, but this is subtle.

---

#### 5. **Missing Null Checks Before Dereference**
**File**: `app/bot.py` (Line 339-341)
**Severity**: LOW

```python
async def send_event_notification(self, event: FrigateEvent):
    # ...
    ts = event.start_time_dt.strftime('%d.%m.%Y %H:%M:%S')
```

**Issue**: 
- `FrigateEvent.start_time` comes from Frigate API
- No check if `start_time` is 0 or missing
- `datetime.fromtimestamp(0)` returns 1970-01-01 (not an error, but wrong)

**Risk**: Very low - API should always provide start_time

---

#### 6. **Type Hint Inconsistency**
**File**: `app/monitor.py` (Lines 32, 37)
**Severity**: LOW

```python
self._on_event: list[callable] = []  # ❌ 'callable' is not a proper type

def on_event(self, callback: callable):  # ❌ Should be Callable[[FrigateEvent], Awaitable[None]]
    self._on_event.append(callback)
```

**Fix**: Use `Callable[[FrigateEvent], Awaitable[None]]` from `typing`

---

### MEDIUM ISSUES

#### 7. **No Connection Pool Configuration**
**File**: `app/frigate.py` (Line 30)
**Severity**: LOW-MEDIUM

```python
self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
```

**Issues**:
- Default connection limits may be too low for high-event scenarios
- No pool size configuration
- Could hit "too many open connections" errors

**Fix**: Add `limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)`

---

#### 8. **No Timeout on Event Polling Loop**
**File**: `app/monitor.py` (Line 145)
**Severity**: LOW

```python
await asyncio.sleep(self.poll_interval)
```

**Issue**: If `client.get_events()` hangs, it blocks the entire polling loop
**Fix**: Add timeout to `_request()` calls

---

#### 9. **State File Memory Leak Mitigation is Weak**
**File**: `app/monitor.py` (Lines 54, 138-139)
**Severity**: LOW

```python
path.write_text(json.dumps({"seen_ids": list(self._seen_ids)[-5000:]}))
```

**Issue**:
- Keeps 10,000 IDs in memory before truncation
- With long-running bots, this could grow significantly
- No LRU cache or time-based expiration

---

#### 10. **No Input Validation on Command Arguments**
**File**: `app/bot.py` (Lines 145-150)
**Severity**: LOW

```python
if context.args[0] == "camera" and len(context.args) > 1:
    result = self.monitor.add_include_camera(context.args[1])
```

**Issue**: 
- `context.args[1]` accessed without checking if camera exists in Frigate
- Could silently add invalid camera names
- No feedback if camera doesn't exist

**Fix**: Validate against `frigate.get_cameras()` before adding

---

#### 11. **Missing Config Validation for Chat ID**
**File**: `app/config.py` (Lines 56-61)
**Severity**: LOW

```python
chat_id = cfg.telegram_chat_id
if isinstance(chat_id, str):
    if chat_id.startswith("-") and chat_id[1:].isdigit():
        cfg.telegram_chat_id = int(chat_id)
    elif chat_id.isdigit():
        cfg.telegram_chat_id = int(chat_id)
# If conversion fails, chat_id stays as string or becomes 0
```

**Issue**: If string chat_id doesn't match either pattern, it's never converted, causing type mismatch later

**Fix**: Add explicit else clause raising ValueError

---

### LOW SEVERITY ISSUES

#### 12. **Documentation Missing**
- **Files**: All Python files
- **Issue**: No docstrings, no module-level documentation
- **Impact**: Difficult to understand purpose of private methods

**Example**:
```python
def _should_process(self, event: FrigateEvent) -> bool:  # No docstring
    # What does "process" mean here?
    if "all" not in self.include_cameras and event.camera not in self.include_cameras:
        return False
    # ...
```

---

#### 13. **No Logging Configuration for File Output**
**File**: `app/main.py` (Lines 73-76)
**Severity**: LOW

```python
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
```

**Issue**: Logs go to stdout only, no persistent logging to files
**Risk**: Cannot debug issues after container restart

---

#### 14. **No Health Check Heartbeat**
**File**: All
**Severity**: LOW

**Issue**: Container only responds to health check with exit code 0 (always healthy)
**Fix**: Implement actual health check (e.g., verify API connectivity)

---

#### 15. **Incomplete Error Messages**
**File**: `app/bot.py` (Lines 338-369)
**Severity**: LOW

```python
except Exception as exc:
    logger.error("Failed to send notification: %s", exc)
    # No context about which event or why it failed
```

**Issue**: Error messages don't include event ID, missing what was being sent

---

## 4. SUGGESTED IMPROVEMENTS AND REFACTORING OPPORTUNITIES

### HIGH PRIORITY

#### 1. **Add Thread-Safe State Management**
```python
# app/monitor.py - Add locking for state modifications
import asyncio

class EventMonitor:
    def __init__(self, ...):
        # ...
        self._filter_lock = asyncio.Lock()
    
    async def add_include_label(self, label: str) -> str:
        async with self._filter_lock:
            if label in self.include_labels:
                return "уже есть"
            self.include_labels.append(label)
            return "добавлена"
```

**Benefit**: Eliminates race conditions between Telegram handlers and polling loop

---

#### 2. **Implement Proper Exception Handling**
```python
# app/frigate.py
async def get_events(...):
    try:
        resp = await self._request("GET", "/api/events", params=params)
        return [FrigateEvent(e) for e in resp.json()]
    except httpx.HTTPStatusError as exc:
        logger.error("Frigate API error: %s %s", exc.response.status_code, exc)
        return []
    except httpx.RequestError as exc:
        logger.error("Network error getting events: %s", exc)
        return []
    except (ValueError, KeyError) as exc:
        logger.error("Invalid response format: %s", exc)
        return []
```

**Benefit**: Better error identification, easier debugging

---

#### 3. **Add Exponential Backoff with Jitter**
```python
# app/frigate.py
import random

async def _request(self, method: str, path: str, retries: int = 3, **kwargs):
    last_exc = None
    max_delay = 32
    
    for attempt in range(retries):
        try:
            resp = await self.client.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            last_exc = exc
        except httpx.RequestError as exc:
            last_exc = exc
        
        if attempt < retries - 1:
            delay = min(1.0 * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)
            wait_time = delay + jitter
            logger.warning("Retry %d/%d for %s %s (waiting %.1fs): %s", 
                         attempt + 1, retries, method, path, wait_time, last_exc)
            await asyncio.sleep(wait_time)
    
    raise last_exc
```

**Benefit**: Prevents DDoS-like behavior on Frigate server, reduces connection thundering

---

#### 4. **Enhance Type Safety**
```python
# app/monitor.py
from typing import Callable, Awaitable

class EventMonitor:
    def __init__(self, ...):
        self._on_event: list[Callable[[FrigateEvent], Awaitable[None]]] = []
    
    def on_event(self, callback: Callable[[FrigateEvent], Awaitable[None]]) -> None:
        self._on_event.append(callback)
```

**Benefit**: IDE autocomplete, type checking with mypy

---

### MEDIUM PRIORITY

#### 5. **Add Comprehensive Logging**
```python
# app/bot.py - Add context to error logs
async def send_event_notification(self, event: FrigateEvent):
    try:
        ts = event.start_time_dt.strftime('%d.%m.%Y %H:%M:%S')
        score = f"\n📊 Уверенность: {event.top_score:.0%}" if event.top_score else ""
        caption = f"🚨 Обнаружен {event.label}\n📷 Камера: {event.camera}\n⏱ {ts}{score}"
        
        logger.debug("Sending notification for event %s (label=%s, camera=%s)",
                    event.id, event.label, event.camera)
        
        if self.send_snapshot:
            thumbnail = await self.frigate.get_thumbnail(event.id)
            if thumbnail:
                logger.debug("Sending photo notification for %s", event.id)
                await self.application.bot.send_photo(...)
                return
            else:
                logger.debug("No thumbnail available for %s", event.id)
    except Exception as exc:
        logger.error("Failed to send notification for event %s: %s", event.id, exc, exc_info=True)
```

**Benefit**: Better observability, easier to diagnose issues

---

#### 6. **Add Persistent Logging to File**
```python
# app/main.py
import logging.handlers

def main():
    config = Config.load(sys.argv[1] if len(sys.argv) > 1 else "/config/config.yml")
    
    log_level = logging.DEBUG if config.debug else logging.INFO
    
    # Create logger
    logger = logging.getLogger()
    logger.setLevel(log_level)
    
    # Console handler
    console = logging.StreamHandler()
    console.setLevel(log_level)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    console.setFormatter(formatter)
    logger.addHandler(console)
    
    # File handler (if in container)
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            "/data/bot.log",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        logger.warning("Could not setup file logging: %s", e)
    
    asyncio.run(run(config))
```

**Benefit**: Persistent logs for debugging, configurable retention

---

#### 7. **Add Configuration Validation Schema**
```python
# app/config.py
from pydantic import BaseModel, Field, field_validator

class Config(BaseModel):
    telegram_token: str
    telegram_chat_id: int
    frigate_url: str = "http://localhost:5000"
    poll_interval: int = Field(5, ge=1, le=60)
    event_limit: int = Field(50, ge=1, le=500)
    state_file: str = "/data/state.json"
    include_cameras: list[str] = Field(default_factory=lambda: ["all"])
    exclude_cameras: list[str] = Field(default_factory=list)
    include_labels: list[str] = Field(default_factory=lambda: ["person"])
    exclude_labels: list[str] = Field(default_factory=list)
    send_snapshot: bool = True
    send_video: bool = False
    debug: bool = False
    
    @field_validator('frigate_url')
    @classmethod
    def validate_url(cls, v):
        if not v.startswith(('http://', 'https://')):
            raise ValueError('Must start with http:// or https://')
        return v
```

**Benefit**: Runtime validation, better error messages

---

#### 8. **Implement Graceful Degradation**
```python
# app/bot.py - Handle missing Frigate gracefully
async def cmd_cameras(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    cameras = await self.frigate.get_cameras()
    if not cameras:
        await self._reply(update, "⚠️ Не удалось получить список камер. Frigate может быть недоступна.")
        # Still functional, just missing camera list
        return
    # ... rest of implementation
```

**Benefit**: Better user experience during outages

---

#### 9. **Add Metrics/Stats Collection**
```python
# app/monitor.py
from collections import Counter
from datetime import datetime, timedelta

class EventMonitor:
    def __init__(self, ...):
        # ...
        self._stats = {
            "events_seen": 0,
            "events_processed": 0,
            "last_poll": None,
            "errors": Counter(),
        }
    
    async def _poll(self):
        while self._running:
            try:
                self._stats["last_poll"] = datetime.now()
                events = await self.client.get_events(limit=self.event_limit)
                self._stats["events_seen"] += len(events)
                
                for event in events:
                    if event.id not in self._seen_ids:
                        self._seen_ids.add(event.id)
                        if self._should_process(event):
                            self._stats["events_processed"] += 1
                            # ... rest of logic
```

**Benefit**: Can expose stats via `/stats` command

---

### LOW PRIORITY

#### 10. **Add Unit Tests**
```python
# tests/test_config.py
import pytest
from app.config import Config

def test_load_from_dict():
    data = {
        "telegram_token": "123:ABC",
        "telegram_chat_id": "123456",
    }
    config = Config.load_from_dict(data)
    assert config.telegram_token == "123:ABC"
    assert config.telegram_chat_id == 123456

def test_invalid_token():
    with pytest.raises(ValueError):
        Config.load_from_dict({"telegram_chat_id": 123})
```

**Benefit**: Catch regressions, clarify expected behavior

---

#### 11. **Extract Constants**
```python
# app/bot.py
QUIET_HOURS_FORMAT = "%H:%M"
COMMAND_TIMEOUT = 10  # seconds
MAX_SNAPSHOTS = 10  # concurrent snapshots
TELEGRAM_API_ERRORS_TO_IGNORE = (
    "chat_id_invalid",
    "message_text_empty",
)
```

**Benefit**: Easier to maintain, less magic numbers

---

#### 12. **Add Retry Decorator**
```python
# app/utils.py
import functools
from typing import Callable, TypeVar

T = TypeVar('T')

def retry(max_attempts: int = 3, backoff: float = 1.0):
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(backoff ** attempt)
            raise last_exc
        return wrapper
    return decorator

# Usage
@retry(max_attempts=3, backoff=1.0)
async def get_version(self) -> str | None:
    resp = await self.client.request("GET", "/api/version")
    return resp.text.strip()
```

**Benefit**: Reduces code duplication

---

## 5. BEST PRACTICES MISSING

### 1. **No Input Sanitization**
- Camera/label names from user input aren't validated
- Could allow injection into API calls (though unlikely with Frigate)

**Fix**: 
```python
import re

def validate_camera_name(name: str) -> bool:
    return bool(re.match(r'^[a-zA-Z0-9_-]+$', name))
```

---

### 2. **No Rate Limiting on Telegram Commands**
- User could spam `/snapall` and exhaust resources
- Each call fetches from all cameras concurrently

**Fix**:
```python
from datetime import datetime, timedelta

class TelegramNotifier:
    def __init__(self, ...):
        self._command_times = {}  # user_id -> datetime
    
    async def _check_rate_limit(self, user_id: int, max_per_minute: int = 5):
        now = datetime.now()
        user_times = self._command_times.get(user_id, [])
        user_times = [t for t in user_times if now - t < timedelta(minutes=1)]
        
        if len(user_times) >= max_per_minute:
            raise ValueError(f"Too many commands, wait {(timedelta(minutes=1) - (now - user_times[0]))}")
        
        user_times.append(now)
        self._command_times[user_id] = user_times
```

---

### 3. **No Circuit Breaker Pattern**
- If Frigate is down, every command will timeout after 30 seconds
- No feedback that service is unavailable

**Fix**: Implement circuit breaker to fail fast

---

### 4. **No Async Context Manager for Resources**
```python
# Current - may leak resources
frigate = FrigateClient(config.frigate_url)
# ...
await frigate.close()

# Better
class FrigateClient:
    async def __aenter__(self):
        await self.client.aopen()
        return self
    
    async def __aexit__(self, *args):
        await self.close()

# Usage
async with FrigateClient(url) as frigate:
    # ...
```

---

### 5. **No Dependency Injection**
- Hard to test, tightly coupled
- `TelegramNotifier` creates Application internally

**Fix**:
```python
class TelegramNotifier:
    def __init__(self, token: str, chat_id: int, ..., application: Application | None = None):
        self.application = application or Application.builder().token(token).build()
```

---

### 6. **No Environment Variable Parsing for Lists**
```python
# Current - doesn't support lists from env
include_cameras = config.include_cameras  # Always from YAML

# Better
os.environ.get("INCLUDE_CAMERAS", "all,front,back").split(",")
```

---

### 7. **No Graceful Shutdown of Telegram Polling**
```python
# Current - may interrupt in-flight requests
await monitor.stop()
await notifier.stop()

# Better - with timeout
async def async_timeout(awaitable, seconds=30):
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except asyncio.TimeoutError:
        logger.error("Operation timed out after %ds", seconds)
        raise

await async_timeout(monitor.stop(), 10)
await async_timeout(notifier.stop(), 10)
```

---

### 8. **No Health Check Endpoint**
- Docker healthcheck just checks if Python runs
- Should verify Frigate connectivity

**Fix**: Expose health check via HTTP or file

---

### 9. **No Metrics Export**
- Cannot monitor bot uptime, event rates, error rates
- Only logs are available

**Fix**: Export Prometheus metrics
```python
from prometheus_client import Counter, Gauge, start_http_server

events_processed = Counter('events_processed_total', 'Total events processed')
notification_errors = Counter('notification_errors_total', 'Failed notifications')
```

---

### 10. **No Database Backup for State**
- State file is single point of failure
- No history of events

**Fix**: Consider SQLite for persistent storage
```python
import sqlite3

class StateDB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                camera TEXT,
                label TEXT,
                timestamp REAL,
                processed BOOLEAN DEFAULT 0
            )
        """)
```

---

## 6. SUMMARY TABLE OF ISSUES

| Issue | File | Line(s) | Severity | Type | Effort |
|-------|------|---------|----------|------|--------|
| Race condition in state modifications | monitor.py | 69-117 | HIGH | Bug | High |
| Bare Exception catching | frigate.py | 68+ | MEDIUM | Quality | Medium |
| No exponential backoff cap | frigate.py | 46 | MEDIUM | Quality | Low |
| Quiet hours logic unclear | bot.py | 327-336 | LOW | Quality | Low |
| Type hints too broad | monitor.py | 32,37 | LOW | Quality | Low |
| No persistent logging | main.py | 73-76 | LOW | Missing Feature | Medium |
| No health check | Dockerfile | 18-19 | LOW | Missing Feature | Low |
| No input validation | bot.py | 145-150 | LOW | Missing Feature | Low |
| No docstrings | all | - | LOW | Documentation | High |
| No unit tests | - | - | MEDIUM | Testing | High |

---

## 7. RECOMMENDATIONS FOR NEXT STEPS

### Phase 1 (Critical - Do First)
1. [ ] Add asyncio.Lock for EventMonitor state modifications
2. [ ] Replace bare Exception with specific exception types
3. [ ] Add exponential backoff cap and jitter to retry logic
4. [ ] Fix Config type conversion for invalid chat_id

### Phase 2 (Important - Do Soon)
1. [ ] Add comprehensive logging with file output
2. [ ] Add type hints using `Callable` from typing module
3. [ ] Validate camera/label inputs from Telegram
4. [ ] Add tests for config and monitor modules

### Phase 3 (Nice to Have)
1. [ ] Add docstrings to all public methods
2. [ ] Implement circuit breaker pattern
3. [ ] Add Prometheus metrics
4. [ ] Implement rate limiting on commands
5. [ ] Use Pydantic for config validation

### Phase 4 (Future Enhancements)
1. [ ] SQLite for persistent event history
2. [ ] Web dashboard for stats
3. [ ] Multiple Telegram chat support
4. [ ] Event replay functionality
5. [ ] S3/cloud backup for snapshots/clips

