"""Tests for the quote checker. Run with:  python -m unittest discover tests"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quote_check import (  # noqa: E402
    INTERVIEWER_WORDS, NOT_FOUND, UNKNOWN_PARTICIPANT, VERIFIED, WRONG_PARTICIPANT,
    check_quote, load_transcripts,
)

TRANSCRIPTS = load_transcripts(ROOT / "sample_transcripts")


class QuoteCheckTests(unittest.TestCase):
    def check(self, quote, pid):
        return check_quote(quote, pid, TRANSCRIPTS).status

    def test_loads_all_participants(self):
        self.assertEqual(list(TRANSCRIPTS), ["P01", "P02", "P03", "P04", "P05", "P06"])

    def test_exact_quote_is_verified(self):
        self.assertEqual(self.check("Bookings that nobody shows up for.", "P01"), VERIFIED)

    def test_curly_quotes_and_extra_spaces_still_match(self):
        self.assertEqual(self.check("I’d rather get too many   than miss one.", "P01"), VERIFIED)

    def test_shortened_quote_with_ellipsis(self):
        quote = "The mobile site is just the desktop site shrunk down... I have to go back on my laptop to fix it."
        self.assertEqual(self.check(quote, "P02"), VERIFIED)

    def test_ellipsis_pieces_out_of_order_fail(self):
        quote = "I have to go back on my laptop to fix it... The mobile site is just the desktop site shrunk down."
        self.assertEqual(self.check(quote, "P02"), NOT_FOUND)

    def test_paraphrase_is_not_found(self):
        self.assertEqual(self.check("Nobody ever shows up for their bookings.", "P01"), NOT_FOUND)

    def test_changed_word_is_not_found(self):
        self.assertEqual(self.check("Bookings that no one shows up for.", "P01"), NOT_FOUND)

    def test_wrong_participant(self):
        self.assertEqual(self.check("Bookings that nobody shows up for.", "P02"), WRONG_PARTICIPANT)

    def test_interviewer_words_are_rejected(self):
        self.assertEqual(self.check("Walk me through the last time you used LabLoop.", "P01"), INTERVIEWER_WORDS)

    def test_header_text_is_not_participant_speech(self):
        self.assertEqual(self.check("Fictional sample transcript created for testing", "P01"), NOT_FOUND)

    def test_unknown_participant(self):
        self.assertEqual(self.check("Bookings that nobody shows up for.", "P99"), UNKNOWN_PARTICIPANT)


if __name__ == "__main__":
    unittest.main()
