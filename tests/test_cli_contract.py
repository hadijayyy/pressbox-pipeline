import subprocess
import sys
from pathlib import Path


PIPELINE = Path(__file__).parent.parent / "pressbox-mvp.py"


def test_help_exits_without_running_pipeline():
    result = subprocess.run(
        [sys.executable, str(PIPELINE), "--help"],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
    assert "Skip — volume gate" not in result.stderr


def test_unknown_flag_fails_closed():
    result = subprocess.run(
        [sys.executable, str(PIPELINE), "--definitely-invalid"],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr


def test_wrappers_share_publisher_lock_and_post_marker_contract():
    root = Path(__file__).parent.parent
    runner = (root / "scripts" / "run-mvp.sh").read_text()
    watchdog = (root / "watchdog-pressbox.sh").read_text()
    assert 'POST_MARKER="/tmp/pressbox-posted-this-run"' in runner
    assert 'rm -f "$POST_MARKER"' in runner
    assert '[ -f "$POST_MARKER" ]' in runner
    assert 'bash "$HOME/.hermes/scripts/run-mvp.sh"' in watchdog
    assert 'python3 -u pressbox-mvp.py' not in watchdog
    assert 'echo "ok $(date -Iseconds)"' not in watchdog
    assert "--watchdog" in watchdog
    assert "--watchdog" in runner
    assert 'flock -n 200 || exit 75' in runner
    assert 'flock -n 200 || exit' not in watchdog
    assert 'retryable_failure()' in runner
    assert 'HTTP 429|HTTP 5[0-9][0-9]|rate.?limited|RATE_LIMITED|timeout|timed out|connection reset|temporarily unavailable' in runner
    pipeline = PIPELINE.read_text()
    assert 'POST_MARKER = "/tmp/pressbox-posted-this-run"' in pipeline
    assert 'with open(POST_MARKER, "w") as f:' in pipeline


def test_publisher_wrapper_reports_stage_reason_and_never_uses_stale_report():
    runner = (Path(__file__).parent.parent / "scripts" / "run-mvp.sh").read_text()
    assert 'STAGE_REASON=$(grep -E' in runner
    assert 'tail -20 /tmp/pressbox-mvp.log >&2' in runner
    assert '[ -f "$POST_MARKER" ] && [ -f /tmp/pressbox-last-report ]' in runner
    assert 'rm -f "$POST_MARKER"' in runner
    assert 'RUNNING $(date -Iseconds)' in runner
    assert 'last successful report visible' in runner
    assert 'exit 75' in runner
    assert 'flock -n 200 || exit 0' not in runner


def test_wrapper_extracts_timestamped_skip_reason():
    runner = (Path(__file__).parent.parent / "scripts" / "run-mvp.sh").read_text()
    assert "grep -E 'Skip —'" in runner
    assert "normal_skip" not in runner
    assert "NO_SAFE_CANDIDATE" in runner


def test_watchdog_does_not_retry_active_run():
    watchdog = (Path(__file__).parent.parent.parent / "watchdog-pressbox.sh").read_text()
    assert '[[ "$LABEL" == "RUNNING"* ]]' in watchdog
    assert 'pipeline running' in watchdog
