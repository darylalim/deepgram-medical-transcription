from unittest.mock import MagicMock, patch

import streamlit_app
from tests.helpers import mock_upload, wav_bytes

FAKE_AUDIO = b"fake-audio-data"


class TestParseUrls:
    def test_empty_text_returns_no_urls(self):
        valid, invalid = streamlit_app._parse_urls("")
        assert valid == []
        assert invalid == []

    def test_blank_lines_are_skipped(self):
        valid, invalid = streamlit_app._parse_urls("  \n\n  \n")
        assert valid == []
        assert invalid == []

    def test_valid_http_url(self):
        valid, invalid = streamlit_app._parse_urls("http://example.com/audio.wav")
        assert valid == ["http://example.com/audio.wav"]
        assert invalid == []

    def test_valid_https_url(self):
        valid, invalid = streamlit_app._parse_urls("https://example.com/audio.wav")
        assert valid == ["https://example.com/audio.wav"]
        assert invalid == []

    def test_invalid_protocol_rejected(self):
        valid, invalid = streamlit_app._parse_urls("ftp://example.com/audio.wav")
        assert valid == []
        assert invalid == ["ftp://example.com/audio.wav"]

    def test_mixed_valid_and_invalid(self):
        text = (
            "https://example.com/a.wav\nftp://bad.com/b.wav\nhttp://example.com/c.mp3"
        )
        valid, invalid = streamlit_app._parse_urls(text)
        assert valid == ["https://example.com/a.wav", "http://example.com/c.mp3"]
        assert invalid == ["ftp://bad.com/b.wav"]


class TestProcessInputs:
    def test_creates_single_client_for_batch(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs("test-key", [("a.wav", b"a"), ("b.wav", b"b")])

        mock_deepgram_cls.assert_called_once_with(api_key="test-key")
        mock_client = mock_deepgram_cls.return_value
        assert mock_client.listen.v1.media.transcribe_file.call_count == 2

    def test_passes_correct_transcribe_options(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs("test-key", [("test.wav", FAKE_AUDIO)])

        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_file.assert_called_once_with(
            request=FAKE_AUDIO,
            model="nova-3-medical",
            smart_format=True,
            profanity_filter=False,
            numerals=False,
        )

    def test_passes_keyterms_when_provided(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs(
            "test-key", [("test.wav", FAKE_AUDIO)], keyterms=["metformin", "aspirin"]
        )

        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_file.assert_called_once_with(
            request=FAKE_AUDIO,
            model="nova-3-medical",
            smart_format=True,
            profanity_filter=False,
            numerals=False,
            keyterm=["metformin", "aspirin"],
        )

    def test_omits_keyterm_when_empty(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs(
            "test-key", [("test.wav", FAKE_AUDIO)], keyterms=[]
        )

        _, kwargs = (
            mock_deepgram_cls.return_value.listen.v1.media.transcribe_file.call_args
        )
        assert "keyterm" not in kwargs

    def test_passes_language_when_provided(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs(
            "test-key", [("test.wav", FAKE_AUDIO)], language="en-GB"
        )

        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_file.assert_called_once_with(
            request=FAKE_AUDIO,
            model="nova-3-medical",
            smart_format=True,
            profanity_filter=False,
            numerals=False,
            language="en-GB",
        )

    def test_omits_language_when_none(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs("test-key", [("test.wav", FAKE_AUDIO)])

        _, kwargs = (
            mock_deepgram_cls.return_value.listen.v1.media.transcribe_file.call_args
        )
        assert "language" not in kwargs

    def test_disables_smart_format_when_off(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs(
            "test-key", [("test.wav", FAKE_AUDIO)], smart_format=False
        )

        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_file.assert_called_once_with(
            request=FAKE_AUDIO,
            model="nova-3-medical",
            profanity_filter=False,
            numerals=False,
            smart_format=False,
        )

    def test_enables_profanity_filter_when_on(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs(
            "test-key", [("test.wav", FAKE_AUDIO)], profanity_filter=True
        )

        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_file.assert_called_once_with(
            request=FAKE_AUDIO,
            model="nova-3-medical",
            smart_format=True,
            numerals=False,
            profanity_filter=True,
        )

    def test_enables_numerals_when_on(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs(
            "test-key", [("test.wav", FAKE_AUDIO)], numerals=True
        )

        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_file.assert_called_once_with(
            request=FAKE_AUDIO,
            model="nova-3-medical",
            smart_format=True,
            profanity_filter=False,
            numerals=True,
        )

    def test_stores_responses_in_session_state(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs("test-key", [("test.wav", FAKE_AUDIO)])

        responses = mock_st.session_state["responses"]
        assert len(responses) == 1
        assert responses[0][0] == "test.wav"

    def test_stores_audio_sources_in_session_state(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs("test-key", [("a.wav", b"a"), ("b.wav", b"b")])

        assert mock_st.session_state["audio_sources"] == [b"a", b"b"]

    def test_large_file_dropped_from_playback(self, mock_deepgram_cls, mock_st):
        with patch.object(streamlit_app, "MAX_PLAYBACK_BYTES", 2):
            streamlit_app._process_inputs(
                "test-key", [("big.wav", b"big"), ("small.wav", b"a")]
            )

        assert mock_st.session_state["audio_sources"] == [None, b"a"]

    def test_continues_after_single_file_failure(self, mock_deepgram_cls, mock_st):
        mock_client = mock_deepgram_cls.return_value
        good_response = MagicMock()

        def fake_transcribe(request, **_):
            if request == b"bad":
                raise Exception("API error")
            return good_response

        mock_client.listen.v1.media.transcribe_file.side_effect = fake_transcribe

        streamlit_app._process_inputs(
            "test-key", [("bad.wav", b"bad"), ("good.wav", b"good")]
        )

        mock_st.error.assert_called_once_with(
            "Transcription failed for bad.wav: API error"
        )
        assert mock_st.session_state["responses"] == [("good.wav", good_response)]
        assert mock_st.session_state["audio_sources"] == [b"good"]

    def test_middle_file_failure_keeps_alignment(self, mock_deepgram_cls, mock_st):
        mock_client = mock_deepgram_cls.return_value
        resp_a, resp_c = MagicMock(), MagicMock()

        def fake_transcribe(request, **_):
            if request == b"b":
                raise Exception("boom")
            return resp_a if request == b"a" else resp_c

        mock_client.listen.v1.media.transcribe_file.side_effect = fake_transcribe

        streamlit_app._process_inputs(
            "test-key", [("a.wav", b"a"), ("b.wav", b"b"), ("c.wav", b"c")]
        )

        assert [n for n, _ in mock_st.session_state["responses"]] == ["a.wav", "c.wav"]
        assert mock_st.session_state["audio_sources"] == [b"a", b"c"]

    def test_all_files_failing_clears_session_state(self, mock_deepgram_cls, mock_st):
        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_file.side_effect = Exception("fail")

        streamlit_app._process_inputs("test-key", [("a.wav", b"a"), ("b.wav", b"b")])

        assert mock_st.error.call_count == 2
        assert mock_st.session_state["responses"] == []
        assert mock_st.session_state["audio_sources"] == []

    def test_clears_stale_results_on_total_failure(self, mock_deepgram_cls, mock_st):
        mock_st.session_state["responses"] = [("old.wav", MagicMock())]
        mock_st.session_state["audio_sources"] = [b"old"]
        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_file.side_effect = Exception("fail")

        streamlit_app._process_inputs("test-key", [("a.wav", b"a")])

        assert mock_st.session_state["responses"] == []
        assert mock_st.session_state["audio_sources"] == []

    def test_stores_all_successful_responses(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs(
            "test-key", [("a.wav", b"a"), ("b.wav", b"b"), ("c.wav", b"c")]
        )

        responses = mock_st.session_state["responses"]
        assert len(responses) == 3
        assert [name for name, _ in responses] == ["a.wav", "b.wav", "c.wav"]

    def test_preserves_input_order_under_reversed_completion(
        self, mock_deepgram_cls, mock_st
    ):
        mock_client = mock_deepgram_cls.return_value

        def dispatch(request, **_):
            resp = MagicMock()
            resp.tag = request
            return resp

        mock_client.listen.v1.media.transcribe_file.side_effect = dispatch

        with patch("streamlit_app.as_completed", side_effect=lambda fs: list(fs)[::-1]):
            streamlit_app._process_inputs(
                "test-key", [("a.wav", b"a"), ("b.wav", b"b"), ("c.wav", b"c")]
            )

        responses = mock_st.session_state["responses"]
        assert [n for n, _ in responses] == ["a.wav", "b.wav", "c.wav"]
        assert [r.tag for _, r in responses] == [b"a", b"b", b"c"]
        assert mock_st.session_state["audio_sources"] == [b"a", b"b", b"c"]

    def test_error_message_includes_filename_and_exception(
        self, mock_deepgram_cls, mock_st
    ):
        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_file.side_effect = Exception("timeout")

        streamlit_app._process_inputs("test-key", [("bad.wav", b"bad")])

        mock_st.error.assert_called_once_with(
            "Transcription failed for bad.wav: timeout"
        )

    def test_uses_progress_bar_not_spinner(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_inputs("test-key", [("a.wav", b"a"), ("b.wav", b"b")])

        mock_st.progress.assert_called()
        mock_st.spinner.assert_not_called()


class TestProcessUrls:
    def test_creates_single_client_for_batch(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_urls(
            "test-key", ["https://example.com/a.wav", "https://example.com/b.wav"]
        )

        mock_deepgram_cls.assert_called_once_with(api_key="test-key")
        mock_client = mock_deepgram_cls.return_value
        assert mock_client.listen.v1.media.transcribe_url.call_count == 2

    def test_passes_correct_transcribe_options(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_urls("test-key", ["https://example.com/test.wav"])

        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_url.assert_called_once_with(
            url="https://example.com/test.wav",
            model="nova-3-medical",
            smart_format=True,
            profanity_filter=False,
            numerals=False,
        )

    def test_passes_keyterms_when_provided(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_urls(
            "test-key", ["https://example.com/test.wav"], keyterms=["metformin"]
        )

        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_url.assert_called_once_with(
            url="https://example.com/test.wav",
            model="nova-3-medical",
            smart_format=True,
            profanity_filter=False,
            numerals=False,
            keyterm=["metformin"],
        )

    def test_omits_keyterm_when_empty(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_urls(
            "test-key", ["https://example.com/test.wav"], keyterms=[]
        )

        _, kwargs = (
            mock_deepgram_cls.return_value.listen.v1.media.transcribe_url.call_args
        )
        assert "keyterm" not in kwargs

    def test_passes_language_when_provided(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_urls(
            "test-key", ["https://example.com/test.wav"], language="en-GB"
        )

        _, kwargs = (
            mock_deepgram_cls.return_value.listen.v1.media.transcribe_url.call_args
        )
        assert kwargs["language"] == "en-GB"

    def test_stores_responses_in_session_state(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_urls("test-key", ["https://example.com/test.wav"])

        responses = mock_st.session_state["responses"]
        assert len(responses) == 1
        assert responses[0][0] == "https://example.com/test.wav"

    def test_stores_audio_sources_in_session_state(self, mock_deepgram_cls, mock_st):
        urls = ["https://example.com/a.wav", "https://example.com/b.wav"]
        streamlit_app._process_urls("test-key", urls)

        assert mock_st.session_state["audio_sources"] == urls

    def test_continues_after_single_url_failure(self, mock_deepgram_cls, mock_st):
        mock_client = mock_deepgram_cls.return_value
        good_response = MagicMock()

        def fake_transcribe(url, **_):
            if url == "https://example.com/bad.wav":
                raise Exception("API error")
            return good_response

        mock_client.listen.v1.media.transcribe_url.side_effect = fake_transcribe

        streamlit_app._process_urls(
            "test-key",
            ["https://example.com/bad.wav", "https://example.com/good.wav"],
        )

        mock_st.error.assert_called_once_with(
            "Transcription failed for https://example.com/bad.wav: API error"
        )
        assert mock_st.session_state["responses"] == [
            ("https://example.com/good.wav", good_response)
        ]
        assert mock_st.session_state["audio_sources"] == [
            "https://example.com/good.wav"
        ]

    def test_all_urls_failing_clears_session_state(self, mock_deepgram_cls, mock_st):
        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_url.side_effect = Exception("fail")

        streamlit_app._process_urls(
            "test-key",
            ["https://example.com/a.wav", "https://example.com/b.wav"],
        )

        assert mock_st.error.call_count == 2
        assert mock_st.session_state["responses"] == []
        assert mock_st.session_state["audio_sources"] == []

    def test_stores_all_successful_responses(self, mock_deepgram_cls, mock_st):
        urls = [
            "https://example.com/a.wav",
            "https://example.com/b.wav",
            "https://example.com/c.wav",
        ]
        streamlit_app._process_urls("test-key", urls)

        responses = mock_st.session_state["responses"]
        assert [name for name, _ in responses] == urls

    def test_error_message_includes_url_and_exception(self, mock_deepgram_cls, mock_st):
        mock_client = mock_deepgram_cls.return_value
        mock_client.listen.v1.media.transcribe_url.side_effect = Exception("timeout")

        streamlit_app._process_urls("test-key", ["https://example.com/bad.wav"])

        mock_st.error.assert_called_once_with(
            "Transcription failed for https://example.com/bad.wav: timeout"
        )

    def test_uses_progress_bar_not_spinner(self, mock_deepgram_cls, mock_st):
        streamlit_app._process_urls(
            "test-key",
            ["https://example.com/a.wav", "https://example.com/b.wav"],
        )

        mock_st.progress.assert_called()
        mock_st.spinner.assert_not_called()


class TestRun:
    def test_uploads_take_priority(self, mock_deepgram_cls, mock_st):
        rec = MagicMock()
        rec.getvalue.return_value = wav_bytes(1)
        streamlit_app._run(
            "key", [mock_upload("a.wav", b"a")], rec, "https://example.com/x.wav"
        )

        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_file.assert_called_once()
        media.transcribe_url.assert_not_called()

    def test_recording_used_when_no_files(self, mock_deepgram_cls, mock_st):
        rec = MagicMock()
        rec.getvalue.return_value = wav_bytes(1)
        streamlit_app._run("key", [], rec, "")

        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_file.assert_called_once()
        assert mock_st.session_state["responses"][0][0] == "Recording"

    def test_urls_used_when_no_files_or_recording(self, mock_deepgram_cls, mock_st):
        streamlit_app._run("key", [], None, "https://example.com/x.wav")

        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_url.assert_called_once()
        media.transcribe_file.assert_not_called()

    def test_no_input_is_noop(self, mock_deepgram_cls, mock_st):
        streamlit_app._run("key", [], None, "   ")

        mock_st.error.assert_not_called()
        mock_st.warning.assert_not_called()
        mock_deepgram_cls.assert_not_called()
        assert "responses" not in mock_st.session_state
        assert "audio_sources" not in mock_st.session_state

    def test_too_many_files_errors_and_skips(self, mock_deepgram_cls, mock_st):
        files = [
            mock_upload(f"f{i}.wav", b"x") for i in range(streamlit_app.MAX_UPLOADS + 1)
        ]
        streamlit_app._run("key", files, None, "")

        mock_st.error.assert_called_once_with(
            "Too many files. Maximum is 100 per batch."
        )
        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_file.assert_not_called()

    def test_oversized_files_skipped_but_others_run(self, mock_deepgram_cls, mock_st):
        big = mock_upload("big.wav", b"x", size=3 * 1024 * 1024 * 1024)
        ok = mock_upload("ok.wav", b"ok")
        streamlit_app._run("key", [big, ok], None, "")

        mock_st.error.assert_called_once_with("Skipped (exceeds 2 GB): big.wav")
        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_file.assert_called_once()
        assert mock_st.session_state["responses"][0][0] == "ok.wav"

    def test_recording_too_long_errors(self, mock_deepgram_cls, mock_st):
        rec = MagicMock()
        rec.getvalue.return_value = wav_bytes(streamlit_app.MAX_RECORDING_SECONDS + 100)
        streamlit_app._run("key", [], rec, "")

        mock_st.error.assert_called_once_with("Recording exceeds the 10-minute limit.")
        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_file.assert_not_called()

    def test_recording_at_exact_limit_is_accepted(self, mock_deepgram_cls, mock_st):
        rec = MagicMock()
        rec.getvalue.return_value = wav_bytes(streamlit_app.MAX_RECORDING_SECONDS)
        streamlit_app._run("key", [], rec, "")

        mock_st.error.assert_not_called()
        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_file.assert_called_once()

    def test_unreadable_recording_errors(self, mock_deepgram_cls, mock_st):
        rec = MagicMock()
        rec.getvalue.return_value = b"not-a-wav"
        streamlit_app._run("key", [], rec, "")

        mock_st.error.assert_called_once_with("Could not read the recording.")
        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_file.assert_not_called()

    def test_invalid_urls_error(self, mock_deepgram_cls, mock_st):
        streamlit_app._run("key", [], None, "ftp://bad.com/a.wav")

        mock_st.error.assert_called_once_with("Invalid URL(s): ftp://bad.com/a.wav")
        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_url.assert_not_called()

    def test_url_without_audio_extension_warns_but_runs(
        self, mock_deepgram_cls, mock_st
    ):
        streamlit_app._run("key", [], None, "https://example.com/audio")

        warning = mock_st.warning.call_args.args[0]
        assert "https://example.com/audio" in warning
        assert "mp3" in warning
        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_url.assert_called_once()

    def test_url_mixed_extension_warns_only_on_extensionless(
        self, mock_deepgram_cls, mock_st
    ):
        streamlit_app._run(
            "key", [], None, "https://example.com/a.mp3\nhttps://example.com/audio"
        )

        warning = mock_st.warning.call_args.args[0]
        assert "https://example.com/audio" in warning
        assert "https://example.com/a.mp3" not in warning
        media = mock_deepgram_cls.return_value.listen.v1.media
        assert media.transcribe_url.call_count == 2

    def test_url_with_query_string_extension_no_warning(
        self, mock_deepgram_cls, mock_st
    ):
        streamlit_app._run("key", [], None, "https://example.com/audio.mp3?token=x")

        mock_st.warning.assert_not_called()
        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_url.assert_called_once()

    def test_multiple_inputs_notify_and_keep_priority(self, mock_deepgram_cls, mock_st):
        rec = MagicMock()
        rec.getvalue.return_value = wav_bytes(1)
        streamlit_app._run(
            "key", [mock_upload("a.wav", b"a")], rec, "https://example.com/x.wav"
        )

        info = mock_st.info.call_args.args[0]
        assert "Upload" in info and "Record" in info and "URL" in info
        media = mock_deepgram_cls.return_value.listen.v1.media
        media.transcribe_file.assert_called_once()
        media.transcribe_url.assert_not_called()

    def test_single_input_no_notice(self, mock_deepgram_cls, mock_st):
        streamlit_app._run("key", [], None, "https://example.com/x.wav")

        mock_st.info.assert_not_called()


class TestDisplayAudio:
    def test_bytes_source_uses_mime_from_extension(self, mock_st):
        streamlit_app._display_audio("dictation.mp3", b"audio-bytes")

        mock_st.audio.assert_called_once_with(b"audio-bytes", format="audio/mpeg")

    def test_bytes_source_without_extension_defaults_to_wav(self, mock_st):
        streamlit_app._display_audio("Recording", b"wav-bytes")

        mock_st.audio.assert_called_once_with(b"wav-bytes", format="audio/wav")

    def test_url_source_passed_through(self, mock_st):
        streamlit_app._display_audio(
            "https://example.com/a.mp3", "https://example.com/a.mp3"
        )

        mock_st.audio.assert_called_once_with("https://example.com/a.mp3")


class TestDisplayTranscript:
    def test_renders_plain_transcript(self, mock_deepgram_cls, mock_st):
        response = (
            mock_deepgram_cls.return_value.listen.v1.media.transcribe_file.return_value
        )

        streamlit_app._display_transcript(response)

        mock_st.markdown.assert_called_once_with("Life moves pretty fast really.")

    def test_no_highlighting_metrics_or_json(self, mock_deepgram_cls, mock_st):
        response = (
            mock_deepgram_cls.return_value.listen.v1.media.transcribe_file.return_value
        )

        streamlit_app._display_transcript(response)

        (markdown_arg,), markdown_kwargs = mock_st.markdown.call_args
        assert "<mark>" not in markdown_arg
        assert "unsafe_allow_html" not in markdown_kwargs
        mock_st.expander.assert_not_called()
        mock_st.json.assert_not_called()

    def test_escapes_markdown_metacharacters(self, mock_st):
        response = MagicMock()
        response.results.channels[0].alternatives[0].transcript = "take *2* `mg` of x_y"

        streamlit_app._display_transcript(response)

        mock_st.markdown.assert_called_once_with("take \\*2\\* \\`mg\\` of x\\_y")


class TestDisplayJson:
    def test_renders_raw_json(self, mock_deepgram_cls, mock_st):
        response = (
            mock_deepgram_cls.return_value.listen.v1.media.transcribe_file.return_value
        )

        streamlit_app._display_json(response)

        mock_st.json.assert_called_once_with(response.model_dump_json())

    def test_minimal_no_markdown_expander_or_downloads(
        self, mock_deepgram_cls, mock_st
    ):
        response = (
            mock_deepgram_cls.return_value.listen.v1.media.transcribe_file.return_value
        )

        streamlit_app._display_json(response)

        mock_st.markdown.assert_not_called()
        mock_st.expander.assert_not_called()
        mock_st.download_button.assert_not_called()


class TestOutputPanel:
    def test_shows_placeholder_when_empty(self, mock_st):
        render = MagicMock()

        streamlit_app._output_panel([], [], render)

        mock_st.caption.assert_called_once_with(streamlit_app.PLACEHOLDER)
        render.assert_not_called()

    def test_single_result_has_player_and_no_divider(self, mock_st):
        response = MagicMock()
        render = MagicMock()

        streamlit_app._output_panel([("a.mp3", response)], [b"a"], render)

        render.assert_called_once_with(response)
        mock_st.audio.assert_called_once()
        mock_st.divider.assert_not_called()
        mock_st.caption.assert_not_called()

    def test_multiple_results_labeled_with_dividers(self, mock_st):
        render = MagicMock()
        responses = [("a.mp3", MagicMock()), ("b.mp3", MagicMock())]

        streamlit_app._output_panel(responses, [b"a", b"b"], render)

        assert render.call_count == 2
        assert mock_st.audio.call_count == 2
        mock_st.divider.assert_called_once()
        labels = [c.args[0] for c in mock_st.markdown.call_args_list]
        assert any("a.mp3" in m for m in labels)
        assert any("b.mp3" in m for m in labels)

    def test_single_none_source_renders_no_player(self, mock_st):
        response = MagicMock()
        render = MagicMock()

        streamlit_app._output_panel([("big.wav", response)], [None], render)

        mock_st.audio.assert_not_called()
        render.assert_called_once_with(response)

    def test_none_source_skipped_among_multiple(self, mock_st):
        render = MagicMock()
        responses = [("big.wav", MagicMock()), ("small.wav", MagicMock())]

        streamlit_app._output_panel(responses, [None, b"a"], render)

        mock_st.audio.assert_called_once_with(b"a", format="audio/wav")
        assert render.call_count == 2


class TestFeatureOpts:
    def test_defaults_when_session_empty(self, mock_st):
        assert streamlit_app._feature_opts() == {
            "keyterms": [],
            "language": "en",
            "smart_format": True,
            "profanity_filter": False,
            "numerals": False,
        }

    def test_reads_values_from_session_state(self, mock_st):
        mock_st.session_state.update(
            {
                "keyterms": ["metformin"],
                "language": "en-GB",
                "smart_format": False,
                "profanity_filter": True,
                "numerals": True,
            }
        )

        assert streamlit_app._feature_opts() == {
            "keyterms": ["metformin"],
            "language": "en-GB",
            "smart_format": False,
            "profanity_filter": True,
            "numerals": True,
        }

    def test_partial_session_state_mixes_values_and_defaults(self, mock_st):
        mock_st.session_state.update({"language": "en-GB", "numerals": True})

        assert streamlit_app._feature_opts() == {
            "keyterms": [],
            "language": "en-GB",
            "smart_format": True,
            "profanity_filter": False,
            "numerals": True,
        }
