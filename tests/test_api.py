import asyncio
import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

import api.main as main
from tests.helpers import mock_word

AUTH = {"Authorization": "Bearer secret-token"}
URL = "https://example.com/visit.mp3"


def make_response(
    transcript="hello world", words=None, duration=3.5, request_id="dg-1", raw=None
):
    """A ListenV1Response-shaped mock with only the attributes the API reads."""
    resp = MagicMock()
    alt = MagicMock()
    alt.transcript = transcript
    alt.words = [] if words is None else words
    resp.results.channels = [MagicMock(alternatives=[alt])]
    resp.metadata.duration = duration
    resp.metadata.request_id = request_id
    resp.model_dump.return_value = {"transcript": transcript} if raw is None else raw
    return resp


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKENS", "secret-token")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
    monkeypatch.delenv("MAX_REQUEST_BYTES", raising=False)


@pytest.fixture
def mock_dg(env):
    with patch("nova.transcribe.DeepgramClient") as cls:
        media = cls.return_value.listen.v1.media
        media.transcribe_url.return_value = make_response()
        media.transcribe_file.return_value = make_response()
        yield cls


@pytest.fixture
def client(mock_dg):
    return TestClient(main.app)


def _media(mock_dg):
    return mock_dg.return_value.listen.v1.media


def _post_urls(client, urls=None, **opts):
    body = {"urls": urls if urls is not None else [URL], **opts}
    return client.post("/v1/transcriptions/urls", json=body, headers=AUTH)


class TestHealthAndAuth:
    def test_healthz_needs_no_auth(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert resp.headers["X-Request-ID"]

    def test_missing_token_is_401(self, client):
        resp = client.post("/v1/transcriptions/urls", json={"urls": [URL]})
        assert resp.status_code == 401
        err = resp.json()["error"]
        assert err["type"] == "unauthorized"
        assert err["code"] == "missing_token"
        assert err["request_id"]
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    def test_invalid_token_is_401(self, client):
        resp = client.post(
            "/v1/transcriptions/urls",
            json={"urls": [URL]},
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_token"

    def test_no_tokens_configured_is_503(self, client, monkeypatch):
        monkeypatch.delenv("API_AUTH_TOKENS", raising=False)
        resp = _post_urls(client)
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "missing_auth_tokens"

    def test_missing_deepgram_key_is_503(self, client, monkeypatch):
        monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
        resp = _post_urls(client)
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "missing_deepgram_key"

    def test_auth_failure_never_calls_deepgram(self, client, mock_dg):
        client.post("/v1/transcriptions/urls", json={"urls": [URL]})
        _media(mock_dg).transcribe_url.assert_not_called()


class TestOptionPassthroughUrls:
    def test_defaults_send_model_smart_format_language_timeout(self, client, mock_dg):
        _post_urls(client)
        kwargs = _media(mock_dg).transcribe_url.call_args.kwargs
        assert kwargs["url"] == URL
        assert kwargs["model"] == "nova-3-medical"
        assert kwargs["smart_format"] is True
        assert kwargs["language"] == "en"
        assert kwargs["request_options"] == {"timeout_in_seconds": 600}
        for absent in (
            "diarize",
            "measurements",
            "dictation",
            "punctuate",
            "keyterm",
            "redact",
        ):
            assert absent not in kwargs

    def test_smart_format_off(self, client, mock_dg):
        _post_urls(client, smart_format=False)
        assert _media(mock_dg).transcribe_url.call_args.kwargs["smart_format"] is False

    def test_diarize_on(self, client, mock_dg):
        _post_urls(client, diarize=True)
        assert _media(mock_dg).transcribe_url.call_args.kwargs["diarize"] is True

    def test_measurements_on(self, client, mock_dg):
        _post_urls(client, measurements=True)
        assert _media(mock_dg).transcribe_url.call_args.kwargs["measurements"] is True

    def test_dictation_forces_punctuate(self, client, mock_dg):
        _post_urls(client, dictation=True)
        kwargs = _media(mock_dg).transcribe_url.call_args.kwargs
        assert kwargs["dictation"] is True
        assert kwargs["punctuate"] is True

    def test_keyterms_passed(self, client, mock_dg):
        _post_urls(client, keyterms=["metformin", "aspirin"])
        assert _media(mock_dg).transcribe_url.call_args.kwargs["keyterm"] == [
            "metformin",
            "aspirin",
        ]

    def test_language_passed(self, client, mock_dg):
        _post_urls(client, language="en-GB")
        assert _media(mock_dg).transcribe_url.call_args.kwargs["language"] == "en-GB"

    def test_redact_and_timeout_share_request_options(self, client, mock_dg):
        _post_urls(client, redact=["pii", "phi"])
        kwargs = _media(mock_dg).transcribe_url.call_args.kwargs
        assert kwargs["request_options"] == {
            "additional_query_parameters": {"redact": ["pii", "phi"]},
            "timeout_in_seconds": 600,
        }
        assert "redact" not in kwargs


class TestOptionPassthroughFiles:
    def test_form_fields_map_to_same_options(self, client, mock_dg):
        resp = client.post(
            "/v1/transcriptions/files",
            headers=AUTH,
            files=[("files", ("visit.wav", b"audio", "audio/wav"))],
            data={
                "dictation": "true",
                "keyterms": ["metformin", "aspirin"],
                "redact": "pii",
            },
        )
        assert resp.status_code == 200
        kwargs = _media(mock_dg).transcribe_file.call_args.kwargs
        assert kwargs["request"] == b"audio"
        assert kwargs["dictation"] is True and kwargs["punctuate"] is True
        assert kwargs["keyterm"] == ["metformin", "aspirin"]
        assert kwargs["request_options"]["additional_query_parameters"] == {
            "redact": ["pii"]
        }


class TestBatchSemantics:
    def test_preserves_input_order(self, client, mock_dg):
        urls = ["https://e.com/a.mp3", "https://e.com/b.mp3", "https://e.com/c.mp3"]
        _media(mock_dg).transcribe_url.side_effect = lambda url, **_: make_response(
            transcript=url
        )
        resp = _post_urls(client, urls=urls)
        body = resp.json()
        assert body["status"] == "completed"
        assert [r["index"] for r in body["results"]] == [0, 1, 2]
        assert [r["name"] for r in body["results"]] == urls
        assert [r["transcript"] for r in body["results"]] == urls

    def test_partial_failure_is_200_partially_completed(self, client, mock_dg):
        def dispatch(url, **_):
            if url.endswith("bad.mp3"):
                raise Exception("API error 400")
            return make_response(transcript=url)

        _media(mock_dg).transcribe_url.side_effect = dispatch
        resp = _post_urls(
            client, urls=["https://e.com/good.mp3", "https://e.com/bad.mp3"]
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "partially_completed"
        assert body["summary"] == {"total": 2, "succeeded": 1, "failed": 1}
        bad = body["results"][1]
        assert bad["status"] == "error"
        assert bad["error"]["type"] == "upstream_error"
        assert bad["error"]["code"] == "deepgram_request_failed"
        assert "API error 400" in bad["error"]["message"]

    def test_all_failed_is_status_failed(self, client, mock_dg):
        _media(mock_dg).transcribe_url.side_effect = Exception("boom")
        resp = _post_urls(client, urls=["https://e.com/a.mp3", "https://e.com/b.mp3"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert body["summary"] == {"total": 2, "succeeded": 0, "failed": 2}

    def test_segments_are_zero_based(self, client, mock_dg):
        words = [mock_word("Hi", 0.9, speaker=0), mock_word("There", 0.9, speaker=1)]
        _media(mock_dg).transcribe_url.return_value = make_response(words=words)
        resp = _post_urls(client, diarize=True)
        segments = resp.json()["results"][0]["segments"]
        assert segments == [
            {"speaker": 0, "text": "Hi"},
            {"speaker": 1, "text": "There"},
        ]

    def test_include_words_off_by_default(self, client, mock_dg):
        _media(mock_dg).transcribe_url.return_value = make_response(
            words=[mock_word("Hi", 0.9, speaker=0)]
        )
        assert _post_urls(client).json()["results"][0]["words"] is None

    def test_include_words_returns_flattened_array(self, client, mock_dg):
        words = [mock_word("Hi", 0.9, speaker=0, start=0.0, end=0.4)]
        _media(mock_dg).transcribe_url.return_value = make_response(words=words)
        out = _post_urls(client, include_words=True).json()["results"][0]["words"]
        assert out == [
            {"text": "Hi", "start": 0.0, "end": 0.4, "confidence": 0.9, "speaker": 0}
        ]

    def test_include_raw_off_by_default(self, client, mock_dg):
        assert _post_urls(client).json()["results"][0]["raw"] is None

    def test_include_raw_returns_model_dump(self, client, mock_dg):
        _media(mock_dg).transcribe_url.return_value = make_response(raw={"foo": "bar"})
        assert _post_urls(client, include_raw=True).json()["results"][0]["raw"] == {
            "foo": "bar"
        }

    def test_success_item_carries_metadata(self, client, mock_dg):
        item = _post_urls(client).json()["results"][0]
        assert item["request_id"] == "dg-1"
        assert item["duration"] == 3.5
        assert item["transcript"] == "hello world"


class TestValidationFailFast:
    def test_invalid_language(self, client, mock_dg):
        resp = _post_urls(client, language="fr")
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "invalid_language"
        _media(mock_dg).transcribe_url.assert_not_called()

    def test_invalid_redact_group(self, client):
        resp = _post_urls(client, redact=["bogus"])
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "invalid_redact_group"

    def test_too_many_keyterms(self, client):
        resp = _post_urls(client, keyterms=[f"k{i}" for i in range(101)])
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "too_many_keyterms"

    def test_too_many_urls(self, client):
        resp = _post_urls(client, urls=[f"https://e.com/{i}.mp3" for i in range(101)])
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "too_many_urls"

    def test_invalid_url_scheme(self, client):
        resp = _post_urls(client, urls=["ftp://e.com/a.mp3"])
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "invalid_url_scheme"

    def test_empty_urls(self, client):
        resp = _post_urls(client, urls=[])
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "no_urls"

    def test_client_model_field_rejected(self, client):
        resp = _post_urls(client, model="whisper")
        assert (
            resp.status_code == 422
        )  # extra fields forbidden -> model is not a parameter

    def test_no_files(self, client):
        resp = client.post("/v1/transcriptions/files", headers=AUTH)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "no_files"

    def test_too_many_files(self, client):
        files = [("files", (f"f{i}.wav", b"x", "audio/wav")) for i in range(101)]
        resp = client.post("/v1/transcriptions/files", headers=AUTH, files=files)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "too_many_files"


class TestSizeLimits:
    def test_oversized_file_is_per_item_error(self, client, mock_dg, monkeypatch):
        monkeypatch.setattr(main, "MAX_FILE_SIZE", 3)
        resp = client.post(
            "/v1/transcriptions/files",
            headers=AUTH,
            files=[
                ("files", ("big.wav", b"too-big", "audio/wav")),
                ("files", ("ok.wav", b"ok", "audio/wav")),
            ],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "partially_completed"
        assert body["results"][0]["error"]["code"] == "file_too_large"
        assert body["results"][1]["status"] == "ok"
        # the oversized file never reached Deepgram
        assert _media(mock_dg).transcribe_file.call_count == 1

    def test_request_body_too_large_is_413(self, client, monkeypatch):
        monkeypatch.setenv("MAX_REQUEST_BYTES", "10")
        resp = _post_urls(client)
        assert resp.status_code == 413
        err = resp.json()["error"]
        assert err["code"] == "request_body_too_large"
        assert err["request_id"]


class TestUrlWarnings:
    def test_url_without_extension_warns(self, client):
        body = _post_urls(client, urls=["https://e.com/audio"]).json()
        assert body["warnings"]
        assert body["status"] == "completed"

    def test_url_with_extension_no_warning(self, client):
        body = _post_urls(client, urls=["https://e.com/audio.mp3"]).json()
        assert body["warnings"] == []


def _upload(data: bytes) -> UploadFile:
    return UploadFile(filename="x.wav", file=io.BytesIO(data))


class TestCappedRead:
    """The parsing-time defense against an absent/falsified Content-Length (§5.1)."""

    def test_within_budget_returns_all_bytes(self):
        assert asyncio.run(main._read_capped(_upload(b"abcdef"), 100)) == b"abcdef"

    def test_over_budget_raises_413(self):
        with pytest.raises(main.ApiError) as excinfo:
            asyncio.run(main._read_capped(_upload(b"x" * 50), 10))
        assert excinfo.value.status_code == 413
        assert excinfo.value.code == "request_body_too_large"


class TestMultiTokenAuth:
    def test_each_configured_token_is_accepted(self, client, monkeypatch):
        monkeypatch.setenv("API_AUTH_TOKENS", "tok-a, tok-b")
        for token in ("tok-a", "tok-b"):
            resp = client.post(
                "/v1/transcriptions/urls",
                json={"urls": [URL]},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, token

    def test_unconfigured_token_still_rejected(self, client, monkeypatch):
        monkeypatch.setenv("API_AUTH_TOKENS", "tok-a, tok-b")
        resp = client.post(
            "/v1/transcriptions/urls",
            json={"urls": [URL]},
            headers={"Authorization": "Bearer tok-c"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_token"
