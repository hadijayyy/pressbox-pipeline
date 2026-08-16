import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import threads_poster as tp


def test_post_thread_recovers_reply_committed_before_api_error(monkeypatch):
    poster = tp.ThreadsPoster("token", "123")
    monkeypatch.setattr(tp.time, "sleep", lambda *_: None)
    calls = []

    def post_single(text, reply_to_id=None, image_url=None):
        calls.append((text, reply_to_id))
        if text == "S2":
            raise tp.ThreadsAPIError("retry later")
        return "root"

    poster.post_single = post_single
    monkeypatch.setattr(poster, "_find_existing_reply", lambda parent_id, text, image_url=None: (
        tp.ThreadPostResult(text=text, post_id="s2") if text == "S2" else None
    ))

    result = poster.post_thread(["S1", "S2"])

    assert [item.post_id for item in result] == ["root", "s2"]
    assert calls == [("S1", None), ("S2", "root")]


def test_post_thread_retries_only_after_existing_reply_check(monkeypatch):
    poster = tp.ThreadsPoster("token", "123")
    monkeypatch.setattr(tp.time, "sleep", lambda *_: None)
    attempts = []

    def post_single(text, reply_to_id=None, image_url=None):
        attempts.append(text)
        if len(attempts) == 2:
            raise tp.ThreadsAPIError("temporary")
        return "root" if text == "S1" else "s2"

    poster.post_single = post_single
    poster._find_existing_reply = lambda *_: None

    result = poster.post_thread(["S1", "S2"])

    assert [item.post_id for item in result] == ["root", "s2"]
    assert attempts == ["S1", "S2", "S2"]


def test_post_thread_recovers_root_after_publish_error(monkeypatch):
    poster = tp.ThreadsPoster("token", "123")
    monkeypatch.setattr(tp.time, "sleep", lambda *_: None)
    poster.post_single = lambda *args, **kwargs: (_ for _ in ()).throw(tp.ThreadsAPIError("timeout"))
    monkeypatch.setattr(poster, "_find_existing_reply", lambda parent_id, text, image_url=None: (
        tp.ThreadPostResult(text=text, post_id="root") if parent_id is None else None
    ))

    result = poster.post_thread(["S1"])

    assert [item.post_id for item in result] == ["root"]


def test_post_thread_rejects_over_limit_without_truncating(monkeypatch):
    poster = tp.ThreadsPoster("token", "123")
    called = []
    poster.post_single = lambda *args, **kwargs: called.append(args) or "root"

    import pytest
    with pytest.raises(ValueError, match="exceeds Threads limit"):
        poster.post_thread(["x" * 501])
    assert called == []