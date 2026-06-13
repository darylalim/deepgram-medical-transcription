import threading
from unittest.mock import MagicMock, patch

from nova.transcribe import build_options, transcribe_batch

OPTS = build_options()


def _files(*pairs):
    return [(name, {"request": data}) for name, data in pairs]


class TestBuildOptions:
    def test_defaults_send_only_model_and_smart_format(self):
        assert build_options() == {"model": "nova-3-medical", "smart_format": True}

    def test_off_features_omitted_by_default(self):
        opts = build_options()
        for absent in (
            "diarize",
            "measurements",
            "dictation",
            "punctuate",
            "keyterm",
            "language",
            "request_options",
        ):
            assert absent not in opts

    def test_smart_format_off(self):
        assert build_options(smart_format=False)["smart_format"] is False

    def test_diarize_on(self):
        assert build_options(diarize=True)["diarize"] is True

    def test_measurements_on(self):
        assert build_options(measurements=True)["measurements"] is True

    def test_dictation_forces_punctuate(self):
        opts = build_options(dictation=True)
        assert opts["dictation"] is True
        assert opts["punctuate"] is True

    def test_dictation_forces_punctuate_even_with_smart_format_off(self):
        opts = build_options(dictation=True, smart_format=False)
        assert opts["smart_format"] is False
        assert opts["dictation"] is True
        assert opts["punctuate"] is True

    def test_keyterms_present(self):
        assert build_options(keyterms=["metformin", "aspirin"])["keyterm"] == [
            "metformin",
            "aspirin",
        ]

    def test_keyterms_omitted_when_empty(self):
        assert "keyterm" not in build_options(keyterms=[])

    def test_language_present(self):
        assert build_options(language="en-GB")["language"] == "en-GB"

    def test_language_omitted_when_none(self):
        assert "language" not in build_options(language=None)

    def test_redact_single_group_via_request_options(self):
        opts = build_options(redact=["phi"])
        assert opts["request_options"] == {
            "additional_query_parameters": {"redact": ["phi"]}
        }
        assert "redact" not in opts  # not a top-level kwarg

    def test_redact_multiple_groups(self):
        assert build_options(redact=["phi", "pii"])["request_options"] == {
            "additional_query_parameters": {"redact": ["phi", "pii"]}
        }

    def test_redact_omitted_when_empty(self):
        assert "request_options" not in build_options(redact=[])

    def test_timeout_merges_into_request_options(self):
        assert build_options(timeout_in_seconds=600)["request_options"] == {
            "timeout_in_seconds": 600
        }

    def test_timeout_and_redact_share_one_request_options(self):
        assert build_options(redact=["pii"], timeout_in_seconds=600)[
            "request_options"
        ] == {
            "additional_query_parameters": {"redact": ["pii"]},
            "timeout_in_seconds": 600,
        }


class TestTranscribeBatch:
    def test_creates_single_client_for_batch(self):
        with patch("nova.transcribe.DeepgramClient") as cls:
            transcribe_batch(
                "k",
                _files(("a.wav", b"a"), ("b.wav", b"b")),
                "transcribe_file",
                options=OPTS,
            )
            cls.assert_called_once_with(api_key="k")
            assert cls.return_value.listen.v1.media.transcribe_file.call_count == 2

    def test_merges_options_onto_each_call(self):
        opts = build_options(diarize=True, keyterms=["x"])
        with patch("nova.transcribe.DeepgramClient") as cls:
            transcribe_batch("k", [("u", {"url": "u"})], "transcribe_url", options=opts)
            kwargs = cls.return_value.listen.v1.media.transcribe_url.call_args.kwargs
            assert kwargs == {
                "url": "u",
                "model": "nova-3-medical",
                "smart_format": True,
                "diarize": True,
                "keyterm": ["x"],
            }

    def test_returns_results_in_input_order(self):
        with patch("nova.transcribe.DeepgramClient") as cls:
            cls.return_value.listen.v1.media.transcribe_file.side_effect = (
                lambda request, **_: f"resp-{request.decode()}"
            )
            results = transcribe_batch(
                "k",
                _files(("a", b"a"), ("b", b"b"), ("c", b"c")),
                "transcribe_file",
                options=OPTS,
            )
            assert [r.index for r in results] == [0, 1, 2]
            assert [r.label for r in results] == ["a", "b", "c"]
            assert [r.response for r in results] == ["resp-a", "resp-b", "resp-c"]
            assert all(r.error is None for r in results)

    def test_captures_per_item_error_as_bare_str(self):
        def dispatch(request, **_):
            if request == b"bad":
                raise Exception("API error")
            return "ok"

        with patch("nova.transcribe.DeepgramClient") as cls:
            cls.return_value.listen.v1.media.transcribe_file.side_effect = dispatch
            results = transcribe_batch(
                "k",
                _files(("bad.wav", b"bad"), ("good.wav", b"good")),
                "transcribe_file",
                options=OPTS,
            )
        assert results[0].error == "API error"  # str(exc), no prefix
        assert results[0].response is None
        assert results[1].error is None
        assert results[1].response == "ok"

    def test_preserves_input_order_under_reversed_completion(self):
        with (
            patch("nova.transcribe.DeepgramClient") as cls,
            patch(
                "nova.transcribe.as_completed", side_effect=lambda fs: list(fs)[::-1]
            ),
        ):
            cls.return_value.listen.v1.media.transcribe_file.side_effect = (
                lambda request, **_: request
            )
            results = transcribe_batch(
                "k",
                _files(("a", b"a"), ("b", b"b"), ("c", b"c")),
                "transcribe_file",
                options=OPTS,
            )
        assert [r.index for r in results] == [0, 1, 2]
        assert [r.response for r in results] == [b"a", b"b", b"c"]

    def test_explicit_client_cls_overrides_module_default(self):
        # The Streamlit wrapper injects its own DeepgramClient as a seam; the explicit
        # arg must win over this module's call-time default.
        custom = MagicMock()
        with patch("nova.transcribe.DeepgramClient") as default_cls:
            transcribe_batch(
                "k",
                [("u", {"url": "u"})],
                "transcribe_url",
                options=OPTS,
                client_cls=custom,
            )
            custom.assert_called_once_with(api_key="k")
            default_cls.assert_not_called()

    def test_on_progress_fires_once_per_completion(self):
        calls: list[tuple[int, int]] = []
        with patch("nova.transcribe.DeepgramClient"):
            transcribe_batch(
                "k",
                _files(("a", b"a"), ("b", b"b")),
                "transcribe_file",
                options=OPTS,
                on_progress=lambda done, total: calls.append((done, total)),
            )
        assert sorted(calls) == [(1, 2), (2, 2)]

    def test_gate_entered_per_item_without_leaking_into_kwargs(self):
        class Gate:
            def __init__(self) -> None:
                self.lock = threading.Lock()
                self.entered = 0

            def __enter__(self):
                with self.lock:
                    self.entered += 1
                return self

            def __exit__(self, *_exc):
                return False

        gate = Gate()
        with patch("nova.transcribe.DeepgramClient") as cls:
            cls.return_value.listen.v1.media.transcribe_url.return_value = "R"
            transcribe_batch(
                "k",
                [("u1", {"url": "u1"}), ("u2", {"url": "u2"})],
                "transcribe_url",
                options=OPTS,
                gate=gate,
            )
        assert gate.entered == 2
        for call in cls.return_value.listen.v1.media.transcribe_url.call_args_list:
            assert set(call.kwargs) == {"url", "model", "smart_format"}

    def test_gate_permit_released_on_failure(self):
        # The gate's whole purpose: each permit is released even when the upstream call
        # raises. A real BoundedSemaphore(2) must be fully replenished after both items
        # fail — a call moved outside `with gate:` would leak permits and block here.
        sem = threading.BoundedSemaphore(2)
        with patch("nova.transcribe.DeepgramClient") as cls:
            cls.return_value.listen.v1.media.transcribe_url.side_effect = Exception(
                "boom"
            )
            results = transcribe_batch(
                "k",
                [("u1", {"url": "u1"}), ("u2", {"url": "u2"})],
                "transcribe_url",
                options=OPTS,
                gate=sem,
            )
        assert all(r.error == "boom" for r in results)
        assert sem.acquire(blocking=False) is True
        assert sem.acquire(blocking=False) is True
        assert sem.acquire(blocking=False) is False  # no over-release either
