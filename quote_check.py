"""Load interview transcripts and check that quotes really appear in them.

This file uses no AI. It is plain Python, so its answers are exact and
repeatable: a quote either appears in a participant's own words or it doesn't.

Expected transcript format (one .txt file per participant):

    Participant ID: P01
    Role: ...any other "Key: value" header lines...
    ---
    Interviewer: A question?
    P01: An answer.

The header (everything above the "---" line) is optional. If there is no
"Participant ID:" line, the file name (e.g. P01.txt -> P01) is used instead.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Speaker labels that mean "this line was said by the researcher, not the participant".
INTERVIEWER_LABELS = {"interviewer", "moderator", "researcher", "facilitator", "q"}

# A line like "P01: some words" or "Interviewer: some words".
TURN_PATTERN = re.compile(r"^([A-Za-z][\w .'-]{0,40}):\s*(.*)$")


@dataclass
class Transcript:
    participant_id: str
    path: Path
    header: dict = field(default_factory=dict)
    # Each turn is (speaker label, what they said).
    turns: list = field(default_factory=list)
    raw_text: str = ""

    def participant_speech(self) -> str:
        """Everything the participant said, joined together."""
        return " ".join(text for speaker, text in self.turns if not is_interviewer(speaker))

    def interviewer_speech(self) -> str:
        return " ".join(text for speaker, text in self.turns if is_interviewer(speaker))


def is_interviewer(speaker: str) -> bool:
    return speaker.strip().lower() in INTERVIEWER_LABELS


def load_transcripts(folder: Path) -> dict:
    """Read every .txt file in `folder`. Returns {participant_id: Transcript}."""
    transcripts = {}
    for path in sorted(folder.glob("*.txt")):
        transcript = parse_transcript(path)
        if transcript.participant_id in transcripts:
            raise ValueError(
                f"Two transcripts use the participant ID {transcript.participant_id!r}: "
                f"{transcripts[transcript.participant_id].path.name} and {path.name}"
            )
        transcripts[transcript.participant_id] = transcript
    return transcripts


def parse_transcript(path: Path) -> Transcript:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()

    header = {}
    body_lines = lines
    divider = next((i for i, line in enumerate(lines) if line.strip() == "---"), None)
    if divider is not None:
        for line in lines[:divider]:
            if ":" in line:
                key, value = line.split(":", 1)
                header[key.strip()] = value.strip()
        body_lines = lines[divider + 1:]

    participant_id = header.get("Participant ID") or path.stem

    turns = []
    for line in body_lines:
        if not line.strip():
            continue
        match = TURN_PATTERN.match(line)
        if match:
            turns.append([match.group(1).strip(), match.group(2).strip()])
        elif turns:
            # A line with no "Speaker:" label continues the previous person's turn.
            turns[-1][1] += " " + line.strip()
        else:
            turns.append([participant_id, line.strip()])

    return Transcript(
        participant_id=participant_id,
        path=path,
        header=header,
        turns=[tuple(t) for t in turns],
        raw_text=raw,
    )


def normalize(text: str) -> str:
    """Smooth over typography so it doesn't cause false alarms.

    Only cosmetic differences are removed: curly vs. straight quote marks,
    long vs. short dashes, and extra spaces or line breaks. Words, spelling,
    and capitalisation must still match exactly.
    """
    text = unicodedata.normalize("NFKC", text)
    replacements = {
        "‘": "'", "’": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "…": "...",
    }
    for fancy, plain in replacements.items():
        text = text.replace(fancy, plain)
    return re.sub(r"\s+", " ", text).strip()


def quote_fragments(quote: str) -> list:
    """Split a quote on '...' so a shortened quote can be checked piece by piece.

    Leading/trailing punctuation and quote marks are trimmed from each piece,
    so a quote that ends at a comma in the transcript still matches.
    """
    pieces = normalize(quote).split("...")
    cleaned = [p.strip(" \"'.,;:!?-") for p in pieces]
    return [p for p in cleaned if p]


def contains_in_order(haystack: str, fragments: list) -> bool:
    position = 0
    for fragment in fragments:
        found = haystack.find(fragment, position)
        if found == -1:
            return False
        position = found + len(fragment)
    return True


# Possible results of a quote check.
VERIFIED = "verified"
WRONG_PARTICIPANT = "wrong_participant"
INTERVIEWER_WORDS = "interviewer_words"
UNKNOWN_PARTICIPANT = "unknown_participant"
NOT_FOUND = "not_found"


@dataclass
class QuoteCheck:
    status: str
    message: str
    shortened: bool = False  # True if the quote used "..." to skip words

    @property
    def ok(self) -> bool:
        return self.status == VERIFIED


def check_quote(quote: str, participant_id: str, transcripts: dict) -> QuoteCheck:
    fragments = quote_fragments(quote)
    shortened = len(fragments) > 1
    if not fragments:
        return QuoteCheck(NOT_FOUND, "Quote is empty.")

    if participant_id not in transcripts:
        return QuoteCheck(
            UNKNOWN_PARTICIPANT,
            f"There is no transcript for participant {participant_id!r}.",
            shortened,
        )

    if contains_in_order(normalize(transcripts[participant_id].participant_speech()), fragments):
        note = " (shortened with '...'; every piece matched, in order)" if shortened else ""
        return QuoteCheck(VERIFIED, f"Found word-for-word in {participant_id}'s transcript{note}.", shortened)

    others = [
        pid for pid, t in transcripts.items()
        if pid != participant_id and contains_in_order(normalize(t.participant_speech()), fragments)
    ]
    if others:
        return QuoteCheck(
            WRONG_PARTICIPANT,
            f"Not said by {participant_id}, but found in the words of: {', '.join(others)}.",
            shortened,
        )

    if any(contains_in_order(normalize(t.interviewer_speech()), fragments) for t in transcripts.values()):
        return QuoteCheck(
            INTERVIEWER_WORDS,
            "These words were said by the interviewer, not a participant.",
            shortened,
        )

    return QuoteCheck(
        NOT_FOUND,
        "Not found in any transcript. It may be paraphrased or invented. Do not use it as a quote.",
        shortened,
    )
