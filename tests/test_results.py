from types import SimpleNamespace

from nova.results import (
    diarized_segments,
    first_alternative,
    transcript_text,
)
from tests.helpers import mock_word


def _resp(words=None, transcript=None, *, has_results=True):
    """A ListenV1Response-shaped object; `has_results=False` mimics ListenV1AcceptedResponse."""
    if not has_results:
        return SimpleNamespace(request_id="req-1")
    alt = SimpleNamespace(transcript=transcript, words=[] if words is None else words)
    return SimpleNamespace(
        results=SimpleNamespace(channels=[SimpleNamespace(alternatives=[alt])])
    )


class TestFirstAlternative:
    def test_returns_first_alternative(self):
        alt = SimpleNamespace(transcript="hi")
        resp = SimpleNamespace(
            results=SimpleNamespace(channels=[SimpleNamespace(alternatives=[alt])])
        )
        assert first_alternative(resp) is alt

    def test_none_when_no_results(self):
        assert first_alternative(_resp(has_results=False)) is None

    def test_none_when_empty_channels(self):
        assert (
            first_alternative(SimpleNamespace(results=SimpleNamespace(channels=[])))
            is None
        )

    def test_none_when_empty_alternatives(self):
        resp = SimpleNamespace(
            results=SimpleNamespace(channels=[SimpleNamespace(alternatives=[])])
        )
        assert first_alternative(resp) is None


class TestTranscriptText:
    def test_returns_transcript(self):
        assert (
            transcript_text(_resp(transcript="life moves pretty fast"))
            == "life moves pretty fast"
        )

    def test_none_when_no_results(self):
        assert transcript_text(_resp(has_results=False)) is None


class TestDiarizedSegments:
    def test_groups_consecutive_runs_with_zero_based_speakers(self):
        words = [
            mock_word("Hello", 0.9, speaker=0),
            mock_word("doctor.", 0.9, speaker=0),
            mock_word("Hi", 0.9, speaker=1),
            mock_word("there.", 0.9, speaker=1),
            mock_word("Yes?", 0.9, speaker=0),
        ]
        assert diarized_segments(_resp(words)) == [
            (0, "Hello doctor."),
            (1, "Hi there."),
            (0, "Yes?"),
        ]

    def test_single_speaker_one_run(self):
        words = [mock_word("Note.", 0.9, speaker=0), mock_word("Done.", 0.9, speaker=0)]
        assert diarized_segments(_resp(words)) == [(0, "Note. Done.")]

    def test_punctuated_word_falls_back_to_word(self):
        w = SimpleNamespace(punctuated_word=None, word="stat", speaker=0)
        assert diarized_segments(_resp([w])) == [(0, "stat")]

    def test_unlabeled_word_continues_current_run(self):
        words = [
            mock_word("Patient", 0.9, speaker=0),
            mock_word("reports", 0.9, speaker=None),
            mock_word("pain.", 0.9, speaker=0),
        ]
        assert diarized_segments(_resp(words)) == [(0, "Patient reports pain.")]

    def test_none_without_integer_speaker(self):
        assert diarized_segments(_resp([mock_word("hi", 0.9)])) is None

    def test_none_for_empty_words(self):
        assert diarized_segments(_resp([])) is None

    def test_none_for_empty_alternatives(self):
        resp = SimpleNamespace(
            results=SimpleNamespace(channels=[SimpleNamespace(alternatives=[])])
        )
        assert diarized_segments(resp) is None
