# Issues Organized by File

## app/monitor.py - 161 lines

### CRITICAL ISSUE: Race Conditions (Lines 69-117)
**Severity: HIGH**

Non-async methods modify state accessed by async polling loop:
- Line 69: `set_include_labels()` - Sync method modifies while async _poll() reads
- Line 72: `set_include_cameras()` - Sync method modifies while async _poll() reads  
- Line 83: `add_include_label()` - Checks then modifies without locking
- Line 92: `remove_include_label()` - Checks then removes without locking
- Line 101: `add_include_camera()` - Checks then modifies without locking
- Line 110: `remove_include_camera()` - Checks then removes without locking

**Root Cause**: Called from `bot.cmd_subscribe()` (sync) while `_poll()` (async) reads `include_labels`
**Risk**: List corruption, missed event filtering, potential crashes
**Fix**: Add `asyncio.Lock` for all filter modifications

---

### Type Hint Issue (Lines 32, 37)
**Severity: LOW**
```python
Line 32: self._on_event: list[callable] = []  # ❌ 'callable' is not a type
Line 37: def on_event(self, callback: callable):  # ❌ Should be Callable[[...], Awaitable[...]]
```
**Fix**: Import `from typing import Callable, Awaitable` and use proper generic types

---

### Memory Management (Lines 54, 138-139)
**Severity: LOW**
```python
Line 54:   path.write_text(json.dumps({"seen_ids": list(self._seen_ids)[-5000:]}))
Line 138: if len(self._seen_ids) > 10000:
Line 139:     self._seen_ids = set(list(self._seen_ids)[-5000:])
```
**Issue**: Keeps 10,000 IDs in memory before truncation, could grow with long-running bots
**Fix**: Implement time-based expiration or LRU cache

---

## app/frigate.py - 165 lines

### CRITICAL ISSUE: Bare Exception Catching (13 instances)
**Severity: MEDIUM**
```
Line 68:  except Exception as exc: (get_events)
Line 76:  except Exception as exc: (get_thumbnail)
Line 84:  except Exception as exc: (get_snapshot)
Line 92:  except Exception as exc: (get_clip)
Line 100: except Exception as exc: (get_latest_snapshot)
Line 112: except Exception as exc: (get_cameras)
Line 120: except Exception as exc: (get_event)
Line 128: except Exception as exc: (get_version)
Line 136: except Exception as exc: (get_stats)
Line 144: except Exception as exc: (ptz)
Line 152: except Exception as exc: (recording_start)
Line 160: except Exception as exc: (recording_stop)
```

**Problem**: Catches `KeyboardInterrupt`, `SystemExit`, `MemoryError`, `asyncio.CancelledError`
**Fix**: Catch specific exceptions (`httpx.HTTPStatusError`, `httpx.RequestError`, `ValueError`, `KeyError`)

---

### Retry Logic Issues (Lines 32-49)
**Severity: MEDIUM**
```python
Line 32: async def _request(self, method: str, path: str, retries: int = 3, **kwargs)
Line 46: delay = 1.0 * (2 ** attempt)  # Hardcoded: 1, 2, 4 seconds
```

**Problems**:
1. No exponential backoff cap (could exceed Python timeout limits)
2. Hardcoded delays not configurable
3. No jitter to prevent thundering herd
4. Only retries on 5xx errors, not network timeouts

**Fix**: Add max_delay, jitter, and configuration options:
```python
delay = min(1.0 * (2 ** attempt), 32)  # Cap at 32 seconds
jitter = random.uniform(0, delay * 0.1)
```

---

### No Connection Pool Configuration (Line 30)
**Severity: LOW-MEDIUM**
```python
Line 30: self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
```
**Issue**: Default connection limits may be insufficient for high-event scenarios
**Fix**: Add `limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)`

---

## app/bot.py - 463 lines

### Quiet Hours Logic (Lines 327-336)
**Severity: LOW**
```python
Line 327: if self._quiet_start and self._quiet_end:
Line 329: if self._quiet_start <= self._quiet_end:  # Normal: 10:00-18:00
Line 330: if self._quiet_start <= now <= self._quiet_end:
Line 334: else:  # Midnight wrap: 22:00-06:00
Line 334: if now >= self._quiet_start or now <= self._quiet_end:
```

**Issue**: Logic is correct but subtle - relies on `time` object ordering
**Impact**: Could be confusing in code reviews
**Fix**: Add comments explaining midnight wrap-around case clearly

---

### Missing Null Checks (Line 339-341)
**Severity: LOW**
```python
Line 339: ts = event.start_time_dt.strftime('%d.%m.%Y %H:%M:%S')
```
**Issue**: No check if `start_time` is 0 or missing - `datetime.fromtimestamp(0)` returns 1970-01-01
**Risk**: Very low - API should always provide start_time, but good defensive practice

---

### Bare Exception in Notifications (Line 368)
**Severity: LOW**
```python
Line 368: except Exception as exc:
Line 369:     logger.error("Failed to send notification: %s", exc)
```
**Issue**: No context about which event or what failed
**Fix**: Include event.id and more details:
```python
logger.error("Failed to send notification for event %s (camera=%s, label=%s): %s",
            event.id, event.camera, event.label, exc, exc_info=True)
```

---

### No Input Validation on Commands (Lines 145-150, 160-165)
**Severity: LOW**
```python
Line 145: if context.args[0] == "camera" and len(context.args) > 1:
Line 146:     result = self.monitor.add_include_camera(context.args[1])
Line 160: if context.args[0] == "camera" and len(context.args) > 1:
Line 161:     result = self.monitor.remove_include_camera(context.args[1])
```

**Issues**:
1. `context.args[1]` accessed without validating if camera exists in Frigate
2. Could silently add invalid camera names
3. No feedback if camera doesn't exist

**Fix**: Validate against `frigate.get_cameras()` before adding

---

### Missing Documentation/Docstrings (All)
**Severity: LOW**
- No docstrings for any methods
- No module-level documentation
- No comments explaining complex logic

**Example** (Line 371-388):
```python
def _make_cam_handler(self, camera_name: str):  # No docstring - what does this do?
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # ... implementation
```

---

## app/config.py - 66 lines

### Type Conversion Issue (Lines 56-61)
**Severity: LOW**
```python
Line 56: chat_id = cfg.telegram_chat_id
Line 57: if isinstance(chat_id, str):
Line 58:     if chat_id.startswith("-") and chat_id[1:].isdigit():
Line 59:         cfg.telegram_chat_id = int(chat_id)
Line 60:     elif chat_id.isdigit():
Line 61:         cfg.telegram_chat_id = int(chat_id)
```

**Problem**: If chat_id string doesn't match either pattern, it's never converted
**Result**: Type mismatch later when used (expected int, got string)
**Fix**: Add explicit else clause:
```python
else:
    raise ValueError(f"Invalid telegram_chat_id format: {chat_id}")
```

---

## app/main.py - 83 lines

### No Persistent Logging (Lines 73-76)
**Severity: LOW**
```python
Line 73: logging.basicConfig(
Line 74:     level=log_level,
Line 75:     format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
Line 76:     datefmt="%Y-%m-%d %H:%M:%S",
Line 77: )
```

**Issue**: 
- Logs go to stdout only
- No persistent logging to files
- Cannot debug issues after container restart

**Fix**: Add file handler with rotation:
```python
import logging.handlers
# Add RotatingFileHandler to /data/bot.log with 10MB limit, 5 backups
```

---

## Dockerfile - 21 lines

### Ineffective Health Check (Lines 18-19)
**Severity: LOW**
```dockerfile
Line 18: HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
Line 19:     CMD python -c "import sys; sys.exit(0)"
```

**Issue**: Always returns 0 (success) - doesn't check if bot is actually running
**Fix**: Check Frigate connectivity or monitor process

---

## Summary by Severity

### CRITICAL (Fix immediately):
1. **app/monitor.py:69-117** - Race conditions in state modifications

### HIGH (Fix soon):
1. **app/frigate.py:68+** - Bare exception catching (13 locations)
2. **app/frigate.py:32-49** - Broken retry logic

### MEDIUM (Fix in next release):
1. **app/config.py:56-61** - Type conversion issue
2. **app/bot.py:145-150** - No input validation
3. **app/monitor.py** - Type hints issues
4. **All files** - Missing documentation

### LOW (Nice to have):
1. **app/main.py** - No file logging
2. **Dockerfile** - Ineffective health check
3. **app/bot.py** - Incomplete error messages

