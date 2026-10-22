"""The research library: every study's findings, ready for the agent to search.

This file uses no AI. It:
- finds the newest saved report for each study in reports/,
- re-checks every quote against that study's transcripts, and drops any that
  fail, so the agent can only ever see (and quote) verified quotes,
- provides the agent's three tools: list_studies, search_findings, get_finding,
- checks the agent's answers: does every finding it cites exist, and does
  every quote it uses really appear in that finding?
"""

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from quote_check import contains_in_order, load_transcripts, normalize, quote_fragments
from research_synth import check_findings

ROOT = Path(__file__).resolve().parent


@dataclass
class Study:
    id: str                 # the transcripts folder name, e.g. "mobile_interviews"
    name: str               # a readable name, e.g. "Mobile interviews"
    answer_path: Path       # the saved Claude answer (.json) this study's findings come from
    transcripts: dict
    findings: list = field(default_factory=list)   # each finding has an "id" like "mobile_interviews/F03"

    @property
    def generated(self) -> str:
        # Report files are named findings_<date>_<time>.json
        stamp = self.answer_path.stem.removeprefix("findings_")
        return stamp.replace("_", " ", 1)

    @property
    def report_html(self) -> Path:
        return self.answer_path.with_suffix(".html")


@dataclass
class Library:
    studies: dict = field(default_factory=dict)    # study id -> Study
    findings: dict = field(default_factory=dict)   # finding id -> (Study, finding)
    warnings: list = field(default_factory=list)   # reports that couldn't be loaded, and why


def load_library(reports_dir: Path) -> Library:
    library = Library()
    answers = sorted(
        p for p in reports_dir.glob("findings_*.json") if not p.name.endswith("_details.json")
    )
    # Sorted oldest first, so a newer report of the same study replaces an older one.
    for answer_path in answers:
        study = load_study(answer_path, library.warnings)
        if study:
            library.studies[study.id] = study

    for study in library.studies.values():
        for finding in study.findings:
            library.findings[finding["id"]] = (study, finding)
    return library


def load_study(answer_path: Path, warnings: list):
    details_path = answer_path.with_name(answer_path.stem + "_details.json")
    try:
        data = json.loads(answer_path.read_text(encoding="utf-8"))
        details = json.loads(details_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        warnings.append(f"Skipped {answer_path.name}: couldn't read it ({e}).")
        return None

    study_info = details.get("study")
    if not study_info:
        warnings.append(
            f"Skipped {answer_path.name}: it doesn't say which transcripts it came from. "
            f"Rebuild it once with research_synth.py --from-json to fix this."
        )
        return None

    folder = Path(study_info["transcripts_folder"])
    if not folder.is_absolute():
        folder = ROOT / folder   # saved relative to the project folder
    if not folder.is_dir():
        folder = ROOT / folder.name   # the project may have been moved
    if not folder.is_dir():
        warnings.append(f"Skipped {answer_path.name}: transcripts folder {folder.name} not found, so quotes can't be checked.")
        return None

    transcripts = load_transcripts(folder)
    try:
        checked = check_findings(data, transcripts)
    except (KeyError, TypeError):
        warnings.append(f"Skipped {answer_path.name}: its findings aren't in the expected format.")
        return None

    study = Study(id=folder.name, name=study_info["name"], answer_path=answer_path, transcripts=transcripts)
    for number, finding in enumerate(checked, 1):
        finding["id"] = f"{study.id}/F{number:02d}"
        finding["number"] = number
        study.findings.append(finding)
    return study


# --- Helpers ----------------------------------------------------------------

def support_text(study: Study, finding: dict) -> str:
    return f"{len(finding['supporting_participants'])} of {len(study.transcripts)} participants"


def role_of(study: Study, participant_id: str) -> str:
    transcript = study.transcripts.get(participant_id)
    return transcript.header.get("Role", "") if transcript else ""


def verified_quotes(finding: dict) -> list:
    """All quotes in a finding that passed the check, supporting and contrasting."""
    quotes = [
        {"participant_id": q["participant_id"], "quote": q["quote"], "kind": "supporting"}
        for q in finding["quotes"] if q["check"].ok
    ]
    quotes += [
        {"participant_id": c["participant_id"], "quote": c["quote"], "kind": "contrasting"}
        for c in finding["contrasting_views"] if c["check"].ok
    ]
    return quotes


# --- The agent's tools --------------------------------------------------------
# Each tool returns plain data (dicts and lists). ask_research.py sends it to
# Claude as JSON.

def list_studies(library: Library) -> dict:
    return {
        "studies": [
            {
                "study_id": s.id,
                "name": s.name,
                "report_generated": s.generated,
                "participants": [{"id": pid, "role": role_of(s, pid)} for pid in s.transcripts],
                "findings": [
                    {"finding_id": f["id"], "headline": f["headline"], "support": support_text(s, f)}
                    for f in s.findings
                ],
            }
            for s in library.studies.values()
        ]
    }


STOPWORDS = set("""
a an and are as at be by did do does for from had has have how i in is it its of on or our
that the their them they this to was we were what when where which who why with you your
about any anything know say said users user people participants participant tell me us
""".split())


def search_terms(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {stem(w) for w in words if w not in STOPWORDS and len(w) > 2}


def stem(word: str) -> str:
    """Crude word stemming, so 'bookings', 'booked' and 'booking' all match 'book'."""
    for suffix in ("ings", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]   # so "price" and "pricing" both become "pric"
    return word


FIELD_WEIGHTS = {"headline": 3, "evidence": 1, "quotes": 1, "contrasting": 1, "interpretation": 0.5}


def finding_terms(f: dict) -> dict:
    terms = {
        "headline": search_terms(f["headline"]),
        "evidence": search_terms(f["evidence"]),
        "quotes": search_terms(" ".join(q["quote"] for q in verified_quotes(f))),
        "contrasting": search_terms(" ".join(c["summary"] for c in f["contrasting_views"])),
        "interpretation": search_terms(f["interpretation"]),
    }
    terms["all"] = set().union(*terms.values())
    return terms


def search_findings(library: Library, query: str, study_id: str = "") -> dict:
    terms = search_terms(query)
    if not terms:
        return {"matches": [], "note": "The query had no searchable words. Try more specific words."}
    if study_id and study_id not in library.studies:
        return {"error": f"No study with id {study_id!r}. Use list_studies to see the ids."}

    # Words that appear in almost every finding (like "book") count for less
    # than rare, specific words (like "double").
    all_terms = {fid: finding_terms(f) for fid, (_, f) in library.findings.items()}
    total = len(all_terms)
    rarity = {
        t: math.log(1 + total / (1 + sum(t in ft["all"] for ft in all_terms.values())))
        for t in terms
    }

    matches = []
    for fid, (study, f) in library.findings.items():
        if study_id and study.id != study_id:
            continue
        score, matched = 0.0, set()
        for name, weight in FIELD_WEIGHTS.items():
            hits = terms & all_terms[fid][name]
            score += weight * sum(rarity[t] for t in hits)
            matched |= hits
        if score:
            matches.append({
                "finding_id": fid,
                "study": study.name,
                "headline": f["headline"],
                "support": support_text(study, f),
                "single_source": len(f["supporting_participants"]) == 1,
                "matched_terms": sorted(matched),
                "_score": score,
            })

    matches.sort(key=lambda m: -m["_score"])
    for m in matches:
        del m["_score"]
    result = {"query": query, "matches": matches[:10]}
    if not matches:
        result["note"] = "No findings matched. Try synonyms, or list_studies to browse every headline."
    return result


def get_finding(library: Library, finding_id: str) -> dict:
    if finding_id not in library.findings:
        return {"error": f"No finding with id {finding_id!r}. Use list_studies or search_findings to find ids."}
    study, f = library.findings[finding_id]
    failed = sum(not q["check"].ok for q in f["quotes"]) + sum(not c["check"].ok for c in f["contrasting_views"])
    return {
        "finding_id": finding_id,
        "study": study.name,
        "headline": f["headline"],
        "support": support_text(study, f),
        "single_source": len(f["supporting_participants"]) == 1,
        "supporting_participants": [
            {"id": pid, "role": role_of(study, pid)} for pid in f["supporting_participants"]
        ],
        "evidence": f["evidence"],
        "verified_quotes": [
            {"participant_id": q["participant_id"], "quote": q["quote"]} for q in f["quotes"] if q["check"].ok
        ],
        "contrasting_views": [
            {
                "participant_id": c["participant_id"],
                "summary": c["summary"],
                "verified_quote": c["quote"] if c["check"].ok else None,
            }
            for c in f["contrasting_views"]
        ],
        "interpretation_by_ai": f["interpretation"],
        "quotes_removed_because_they_failed_verification": failed,
    }


# --- Checking the agent's answer ----------------------------------------------

CITATION_OK = "verified"
FINDING_MISSING = "finding_not_found"
QUOTE_NOT_IN_FINDING = "quote_not_in_finding"
WRONG_PARTICIPANT = "wrong_participant"


def check_answer(answer: dict, library: Library) -> dict:
    """Check every finding and quote the agent cited. Returns the results by position."""
    point_checks = []
    for point in answer.get("points", []):
        findings = [
            {"finding_id": fid, "ok": fid in library.findings} for fid in point.get("finding_ids", [])
        ]
        quotes = [check_cited_quote(q, library) for q in point.get("quotes", [])]
        point_checks.append({"findings": findings, "quotes": quotes})
    failures = sum(not f["ok"] for p in point_checks for f in p["findings"]) + sum(
        q["status"] != CITATION_OK for p in point_checks for q in p["quotes"]
    )
    total = sum(len(p["findings"]) + len(p["quotes"]) for p in point_checks)
    return {"points": point_checks, "failures": failures, "total": total}


def check_cited_quote(quote: dict, library: Library) -> dict:
    fid, pid, text = quote.get("finding_id", ""), quote.get("participant_id", ""), quote.get("quote", "")
    if fid not in library.findings:
        return {"status": FINDING_MISSING, "message": f"Cites finding {fid}, which doesn't exist in the reports."}

    fragments = quote_fragments(text)
    if not fragments:
        return {"status": QUOTE_NOT_IN_FINDING, "message": "The quote is empty."}
    _, finding = library.findings[fid]
    candidates = verified_quotes(finding)
    for q in candidates:
        if contains_in_order(normalize(q["quote"]), fragments):
            if q["participant_id"] == pid:
                return {"status": CITATION_OK, "message": f"Matches a verified quote in {fid}."}
            return {
                "status": WRONG_PARTICIPANT,
                "message": f"This quote is in {fid}, but it's from {q['participant_id']}, not {pid}.",
            }
    return {
        "status": QUOTE_NOT_IN_FINDING,
        "message": f"Not found among the verified quotes in {fid}. It may be paraphrased or invented.",
    }
