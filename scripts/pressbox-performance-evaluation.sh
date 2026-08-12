#!/bin/bash
set -euo pipefail
cd /home/ubuntu/pressbox-pipeline
exec flock -n /tmp/pressbox-performance-evaluation.lock python3 -u - <<'PY'
import importlib.util
import json

spec = importlib.util.spec_from_file_location("pressbox_mvp", "pressbox-mvp.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

token, user_id = mod.load_threads_token()
if not token or not user_id:
    print("CONFIG_FAILURE: Pressbox Threads token unavailable")
    raise SystemExit(1)

from threads_poster import ThreadsPoster
poster = ThreadsPoster(access_token=token, user_id=user_id)
mod.pull_engagement(poster)
try:
    with open(mod.POSTED) as f:
        topics = json.load(f).get("topics", [])
except (FileNotFoundError, json.JSONDecodeError):
    topics = []
mod._update_ring(topics)
summary = mod.get_analytics_summary()
measured = sum(1 for t in topics if t.get("views") is not None)
failed = sum(1 for t in topics if t.get("metrics_failed"))
print(
    "Pressbox daily performance evaluation: "
    f"measured={measured}, metrics_failed={failed}, "
    f"analytics_posts={summary.get('total_posts_with_metrics', 0)}, "
    f"avg_views={summary.get('avg_views', 0):.0f}"
)
PY
/home/ubuntu/.hermes/scripts/pressbox-engagement-report.sh >/dev/null
printf 'Pressbox daily performance report sent\n'
