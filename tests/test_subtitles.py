from unittest.mock import MagicMock

from nova.subtitles import _format_timestamp, to_srt
from tests.helpers import mock_word


def _response(words):
    """A minimal response whose first alternative carries `words`."""
    response = MagicMock()
    response.results.channels = [MagicMock(alternatives=[MagicMock(words=words)])]
    return response


class TestFormatTimestamp:
    def test_zero(self):
        assert _format_timestamp(0.0) == "00:00:00,000"

    def test_hours_minutes_seconds_millis(self):
        assert _format_timestamp(3661.5) == "01:01:01,500"

    def test_sub_second_millis(self):
        assert _format_timestamp(65.25) == "00:01:05,250"


class TestToSrt:
    def test_diarized_two_speakers_split_on_speaker_change(self):
        words = [
            mock_word("Hello.", 0.9, speaker=0, start=0.0, end=1.0),
            mock_word("Hi.", 0.9, speaker=1, start=1.0, end=2.0),
        ]

        assert to_srt(_response(words)) == (
            "1\n00:00:00,000 --> 00:00:01,000\nSpeaker 1: Hello.\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nSpeaker 2: Hi."
        )

    def test_flat_transcript_splits_on_sentence_end(self):
        # No integer speakers -> no "Speaker N:" prefix; a cue flushes after a
        # sentence-ending token ("there.") rather than running on.
        words = [
            mock_word("Hello", 0.9, start=0.0, end=1.0),
            mock_word("there.", 0.9, start=1.0, end=2.0),
            mock_word("How", 0.9, start=2.0, end=3.0),
            mock_word("are", 0.9, start=3.0, end=4.0),
            mock_word("you?", 0.9, start=4.0, end=5.0),
        ]

        assert to_srt(_response(words)) == (
            "1\n00:00:00,000 --> 00:00:02,000\nHello there.\n\n"
            "2\n00:00:02,000 --> 00:00:05,000\nHow are you?"
        )

    def test_word_count_cap_starts_a_new_cue(self):
        # 13 words with tiny durations (so only the word cap, not the duration cap,
        # fires) -> a 12-word cue then a 1-word cue.
        words = [
            mock_word(f"w{i}", 0.9, speaker=0, start=i * 0.1, end=i * 0.1 + 0.05)
            for i in range(13)
        ]

        assert to_srt(_response(words)).count("-->") == 2

    def test_words_without_numeric_timing_are_skipped(self):
        # A word missing numeric start/end cannot anchor a cue and is dropped.
        dropped = MagicMock()
        dropped.punctuated_word = "dropped"
        dropped.word = "dropped"
        dropped.speaker = 0
        dropped.start = None
        dropped.end = None
        words = [dropped, mock_word("kept", 0.9, speaker=0, start=1.0, end=2.0)]

        srt = to_srt(_response(words))
        assert "dropped" not in srt
        assert srt == "1\n00:00:01,000 --> 00:00:02,000\nSpeaker 1: kept"

    def test_results_less_response_is_empty(self):
        response = MagicMock(spec=["request_id"])

        assert to_srt(response) == ""

    def test_empty_channels_is_empty(self):
        response = MagicMock()
        response.results.channels = []

        assert to_srt(response) == ""
