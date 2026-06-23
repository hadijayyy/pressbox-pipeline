#!/usr/bin/env python3
"""Hermes Circuit Breaker — reusable across all cron jobs.

States per job:
  CLOSED    → normal, all calls allowed
  OPEN      → tripped, calls blocked, cooldown timer running
  HALF_OPEN → cooldown elapsed, next call is a probe

Trip rule: 2 consecutive failures within 10 minutes → OPEN for 60 minutes.
After cooldown: HALF_OPEN probe → success = CLOSED, fail = OPEN again.

State files: /tmp/hermes-cb/<job_id>.json (one per job)
Usage:
  from hermes_circuit_breaker import CircuitBreaker
  cb = CircuitBreaker("my_job_id")
  if not cb.allow(): sys.exit(77)   # skip, circuit open
  try:
      run_pipeline()
      cb.record_success()
  except Exception:
      cb.record_failure()
      raise
"""
import json
import os
import time
from dataclasses import dataclass, field
from typing import List, Tuple

STATE_DIR = "/tmp/hermes-cb"


@dataclass
class State:
    state: str = "CLOSED"          # CLOSED | OPEN | HALF_OPEN
    failures: List[float] = field(default_factory=list)  # unix timestamps
    opened_at: float = 0.0
    last_transition: float = 0.0
    history: List[dict] = field(default_factory=list)     # append-only audit log

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "failures": self.failures,
            "opened_at": self.opened_at,
            "last_transition": self.last_transition,
            "history": self.history[-50:],   # cap log size
        }

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        return cls(
            state=d.get("state", "CLOSED"),
            failures=d.get("failures", []),
            opened_at=d.get("opened_at", 0.0),
            last_transition=d.get("last_transition", 0.0),
            history=d.get("history", []),
        )


class CircuitBreaker:
    """File-backed circuit breaker. Thread-safe via atomic write (tmp+rename)."""

    DEFAULT_THRESHOLD = 2        # failures before trip
    DEFAULT_WINDOW = 600         # seconds (10 min)
    DEFAULT_COOLDOWN = 3600      # seconds (1 hour)

    def __init__(self, job_id: str, threshold: int = None,
                 window: int = None, cooldown: int = None):
        self.job_id = job_id
        self.threshold = threshold or self.DEFAULT_THRESHOLD
        self.window = window or self.DEFAULT_WINDOW
        self.cooldown = cooldown or self.DEFAULT_COOLDOWN
        self.path = f"{STATE_DIR}/{job_id}.json"

    def load(self) -> State:
        try:
            with open(self.path, "r") as f:
                return State.from_dict(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return State()

    def save(self, st: State) -> None:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            json.dump(st.to_dict(), f, indent=2)
        os.replace(tmp, self.path)   # atomic on POSIX

    def allow(self) -> Tuple[bool, str]:
        """Check whether the job is allowed to run. Returns (allow, reason)."""
        st = self.load()
        now = time.time()

        if st.state == "OPEN":
            elapsed = now - st.opened_at
            if elapsed >= self.cooldown:
                st.state = "HALF_OPEN"
                st.last_transition = now
                st.history.append({
                    "ts": now, "from": "OPEN", "to": "HALF_OPEN",
                    "reason": f"cooldown elapsed ({elapsed:.0f}s >= {self.cooldown}s)",
                })
                self.save(st)
                return (True, f"HALF_OPEN — cooldown elapsed ({elapsed:.0f}s), probing")
            remaining = int(self.cooldown - elapsed)
            return (False, f"OPEN — circuit tripped, {remaining}s cooldown remaining ({remaining // 60}m {remaining % 60}s)")

        # CLOSED or HALF_OPEN → allow
        return (True, st.state)

    def record_success(self) -> Tuple[str, str]:
        """Record successful run. Returns (new_state, message)."""
        st = self.load()
        now = time.time()
        prev = st.state
        st.state = "CLOSED"
        st.failures = []
        st.opened_at = 0.0
        st.last_transition = now
        st.history.append({
            "ts": now, "from": prev, "to": "CLOSED",
            "reason": "success",
        })
        self.save(st)
        return ("CLOSED", f"success — state reset to CLOSED (was {prev})")

    def record_failure(self, error: str = "") -> Tuple[str, str]:
        """Record failed run. Returns (new_state, message)."""
        st = self.load()
        now = time.time()
        prev = st.state

        # Window-based failure list (only relevant when CLOSED)
        if prev == "CLOSED":
            st.failures = [t for t in st.failures if now - t < self.window]
        st.failures.append(now)

        if prev == "HALF_OPEN":
            # Probe failed → re-OPEN
            st.state = "OPEN"
            st.opened_at = now
            st.last_transition = now
            st.history.append({
                "ts": now, "from": "HALF_OPEN", "to": "OPEN",
                "reason": f"probe failed: {error[:80]}",
            })
            self.save(st)
            return ("OPEN", f"HALF_OPEN probe failed → re-OPEN for {self.cooldown // 60}m: {error[:80]}")

        # CLOSED → check threshold
        if len(st.failures) >= self.threshold:
            st.state = "OPEN"
            st.opened_at = now
            st.last_transition = now
            st.history.append({
                "ts": now, "from": "CLOSED", "to": "OPEN",
                "reason": f"threshold reached ({len(st.failures)}/{self.threshold} failures in {self.window // 60}m): {error[:80]}",
            })
            self.save(st)
            return ("OPEN", f"tripped — {len(st.failures)} failures in {self.window // 60}m → OPEN for {self.cooldown // 60}m")

        self.save(st)
        return ("CLOSED", f"failure recorded ({len(st.failures)}/{self.threshold}): {error[:80]}")

    def status(self) -> dict:
        """Return current status snapshot for /status or debugging."""
        st = self.load()
        now = time.time()
        info = {
            "job_id": self.job_id,
            "state": st.state,
            "failures_in_window": len([t for t in st.failures if now - t < self.window]),
            "threshold": self.threshold,
            "window_min": self.window // 60,
            "cooldown_min": self.cooldown // 60,
            "opened_at": st.opened_at,
            "last_transition": st.last_transition,
            "recent_history": st.history[-5:],
        }
        if st.state == "OPEN":
            info["cooldown_remaining_sec"] = max(0, int(self.cooldown - (now - st.opened_at)))
        return info

    def reset(self) -> None:
        """Manual reset (admin only)."""
        if os.path.exists(self.path):
            os.remove(self.path)


# --- CLI helper ---
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  hermes_circuit_breaker.py status <job_id>")
        print("  hermes_circuit_breaker.py allow <job_id>")
        print("  hermes_circuit_breaker.py record success|failure <job_id> [error_msg]")
        print("  hermes_circuit_breaker.py reset <job_id>")
        sys.exit(2)

    cmd = sys.argv[1]
    if cmd == "status":
        job_id = sys.argv[2]
        print(json.dumps(CircuitBreaker(job_id).status(), indent=2))
    elif cmd == "allow":
        job_id = sys.argv[2]
        cb = CircuitBreaker(job_id)
        ok, reason = cb.allow()
        print(f"{'ALLOW' if ok else 'BLOCK'}: {reason}")
        sys.exit(0 if ok else 77)
    elif cmd == "record":
        result = sys.argv[2]    # success | failure
        job_id = sys.argv[3]
        error = sys.argv[4] if len(sys.argv) > 4 else ""
        cb = CircuitBreaker(job_id)
        if result == "success":
            state, msg = cb.record_success()
        else:
            state, msg = cb.record_failure(error)
        print(f"{state}: {msg}")
    elif cmd == "reset":
        job_id = sys.argv[2]
        CircuitBreaker(job_id).reset()
        print(f"reset: {job_id}")
    else:
        print(f"unknown command: {cmd}")
        sys.exit(2)
