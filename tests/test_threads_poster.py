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


def test_api_error_keeps_http_diagnostics():
    class Response:
        status_code = 400

        def json(self):
            return {"error": {"message": "bad image", "code": 100, "fbtrace_id": "trace"}}

    import pytest
    with pytest.raises(tp.ThreadsAPIError, match=r"HTTP 400: bad image .*code=100") as caught:
        tp.ThreadsPoster._parse_response(Response())
    assert caught.value.status_code == 400
    assert caught.value.payload["error"]["code"] == 100


def test_client_error_is_not_retried(monkeypatch):
    poster = tp.ThreadsPoster("token", "123")
    monkeypatch.setattr(tp.time, "sleep", lambda *_: None)
    attempts = []

    def post_single(*args, **kwargs):
        attempts.append(1)
        raise tp.ThreadsAPIError("bad request", status_code=400)

    poster.post_single = post_single
    poster._find_existing_reply = lambda *_: None
    import pytest
    with pytest.raises(tp.ThreadsAPIError):
        poster.post_thread(["S1"])
    assert attempts == [1]


def test_post_thread_resumes_after_persisted_partial_result(monkeypatch):
    poster = tp.ThreadsPoster("token", "123")
    monkeypatch.setattr(tp.time, "sleep", lambda *_: None)
    calls = []

    def post_single(text, reply_to_id=None, image_url=None):
        calls.append((text, reply_to_id))
        return "s2"

    poster.post_single = post_single
    result = poster.post_thread(
        ["S1", "S2"],
        existing_results=[tp.ThreadPostResult("S1", "root")],
    )
    assert [item.post_id for item in result] == ["root", "s2"]
    assert calls == [("S2", "root")]