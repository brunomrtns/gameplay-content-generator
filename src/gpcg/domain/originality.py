"""Originality checker — deterministic n-gram overlap between script and sources.

Compares the final script against the source documents (and extracted fact
claims) to detect verbatim or near-verbatim copying. Returns an originality
score (0-100, higher = more original) and the longest matching n-grams.

This is a SAFETY NET on top of the LLM's own anti-plagiarism instructions.
Even if the LLM is told to rewrite, we verify programmatically.

Algorithm:
  1. Normalize text (lowercase, strip accents, collapse whitespace, strip punctuation)
  2. Build n-gram sets (n=3,4,5,6,7 words) for script and each source
  3. Compute Jaccard-like overlap: |intersection| / |script_ngrams|
  4. Originality score = 100 * (1 - max_overlap)
  5. Report the longest matching sequences for transparency
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from gpcg.infrastructure.document_parser import DocumentParseError, parse_document


@dataclass
class OriginalityReport:
    """Result of an originality check."""

    score: float  # 0-100, higher = more original
    max_overlap: float  # 0-1, fraction of script n-grams found in a source
    matched_source: str | None  # which source had the highest overlap
    longest_matches: list[str] = field(default_factory=list)  # longest verbatim sequences
    n_gram_size: int = 5
    sources_checked: int = 0
    threshold: float = 70.0  # minimum score to be considered original

    @property
    def is_original(self) -> bool:
        """True if the script is sufficiently original (score >= threshold)."""
        return self.score >= self.threshold

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 2),
            "max_overlap": round(self.max_overlap, 4),
            "matched_source": self.matched_source,
            "longest_matches": self.longest_matches[:10],
            "n_gram_size": self.n_gram_size,
            "sources_checked": self.sources_checked,
            "threshold": self.threshold,
            "is_original": self.is_original,
        }


# ── Text normalization ─────────────────────────────────────────────────────


def _strip_accents(s: str) -> str:
    """Remove diacritics: 'narracão' → 'narracao'."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _normalize(text: str, *, language: str = "pt-BR") -> str:
    """Normalize for n-gram comparison: lowercase, no accents, no punctuation, single spaces.

    For Latin-script languages (pt, en, es, fr, de, it), strips accents and
    keeps only a-z0-9. For CJK/Arabic/Cyrillic, preserves Unicode letters.
    """
    s = text.lower()
    # Only strip accents for Latin scripts (accent stripping is harmful for
    # languages where diacritics change meaning, e.g. Arabic)
    latin_scripts = {"pt-BR", "en-US", "es-ES", "es-MX", "fr-FR", "de-DE", "it-IT"}
    if language in latin_scripts:
        s = _strip_accents(s)
        # Replace any non-alphanumeric with space
        s = re.sub(r"[^a-z0-9\s]", " ", s)
    else:
        # For CJK/Arabic/Cyrillic: keep Unicode letters and numbers
        s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokenize(text: str, language: str = "pt-BR") -> list[str]:
    """Split normalized text into word tokens.

    For CJK languages (zh/ja/ko), whitespace-based splitting doesn't work
    because words aren't separated by spaces. Instead, tokenize by character
    and build bigrams for better plagiarism detection.
    """
    if not text:
        return []
    base = language.split("-")[0].lower()
    if base in ("zh", "ja", "ko"):
        # CJK: tokenize by character, then build bigrams
        # Strip common CJK punctuation
        cjk_punct = "。，！？、；：「」『』（）【】《》…—·"
        chars = [c for c in text if c.strip() and c not in cjk_punct]
        if len(chars) < 2:
            return chars
        return [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    # Latin scripts: split by whitespace
    return text.split()


def _ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    """Build the set of n-grams (as tuples) from a token list."""
    if len(tokens) < n:
        return set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


# ── Core comparison ────────────────────────────────────────────────────────


def _find_longest_common_sequences(
    script_tokens: list[str],
    source_tokens: list[str],
    min_len: int = 5,
    max_len: int = 30,
) -> list[str]:
    """Find the longest verbatim word sequences shared between script and source.

    Uses a sliding-window approach: for each starting position in the script,
    find the longest match in the source.
    """
    if not script_tokens or not source_tokens:
        return []

    # Build source token positions index for fast lookup
    source_index: dict[tuple[str, ...], list[int]] = {}
    for n in range(min_len, max_len + 1):
        for i in range(len(source_tokens) - n + 1):
            key = tuple(source_tokens[i : i + n])
            source_index.setdefault(key, []).append(i)

    matches: list[str] = []
    seen_starts: set[int] = set()

    # Greedy: find longest matches first, skip overlapping ones
    for n in range(max_len, min_len - 1, -1):
        for i in range(len(script_tokens) - n + 1):
            if i in seen_starts:
                continue
            key = tuple(script_tokens[i : i + n])
            if key in source_index:
                matches.append(" ".join(script_tokens[i : i + n]))
                # Mark these positions as consumed
                for j in range(i, i + n):
                    seen_starts.add(j)
                if len(matches) >= 10:
                    return matches
    return matches


def compare_texts(script: str, source: str, n: int = 5, *, language: str = "pt-BR") -> tuple[float, list[str]]:
    """Compare script vs. a single source text.

    Returns (overlap_fraction, longest_matches).
    overlap_fraction = fraction of script n-grams that appear in the source.
    """
    # For CJK languages (character bigrams), use a smaller n-gram size.
    # A word-level n=5 in Latin scripts roughly corresponds to ~5 words;
    # for CJK bigrams, n=3 (6 chars) is a comparable plagiarism signal.
    base = language.split("-")[0].lower()
    if base in ("zh", "ja", "ko"):
        n = min(n, 3)
    script_tokens = _tokenize(_normalize(script, language=language), language=language)
    source_tokens = _tokenize(_normalize(source, language=language), language=language)

    if not script_tokens or not source_tokens:
        return 0.0, []

    script_ngrams = _ngrams(script_tokens, n)
    source_ngrams = _ngrams(source_tokens, n)

    if not script_ngrams:
        return 0.0, []

    overlap = script_ngrams & source_ngrams
    overlap_fraction = len(overlap) / len(script_ngrams)

    min_match_len = 3 if base in ("zh", "ja", "ko") else 5
    longest = _find_longest_common_sequences(script_tokens, source_tokens, min_len=min_match_len)
    return overlap_fraction, longest


# ── High-level API ─────────────────────────────────────────────────────────


def check_originality(
    script: str,
    source_texts: Iterable[tuple[str, str]],
    n: int = 5,
    threshold: float = 70.0,
    *,
    language: str = "pt-BR",
) -> OriginalityReport:
    """Check script originality against multiple source texts.

    Args:
        script: The final narration script.
        source_texts: Iterable of (source_name, source_text) tuples.
        n: n-gram size (default 5 words).
        threshold: Minimum score to be considered original (default 70).
        language: BCP-47 tag for language-aware normalization.

    Returns:
        OriginalityReport with the worst-case (highest) overlap across all sources.
    """
    sources = list(source_texts)
    if not sources:
        return OriginalityReport(
            score=100.0,
            max_overlap=0.0,
            matched_source=None,
            sources_checked=0,
            n_gram_size=n,
            threshold=threshold,
        )

    worst_overlap = 0.0
    worst_source: str | None = None
    worst_matches: list[str] = []

    for name, text in sources:
        if not text or not text.strip():
            continue
        overlap, matches = compare_texts(script, text, n=n, language=language)
        if overlap > worst_overlap:
            worst_overlap = overlap
            worst_source = name
            worst_matches = matches

    score = 100.0 * (1.0 - worst_overlap)
    return OriginalityReport(
        score=score,
        max_overlap=worst_overlap,
        matched_source=worst_source,
        longest_matches=worst_matches,
        n_gram_size=n,
        sources_checked=len(sources),
        threshold=threshold,
    )


def check_originality_from_documents(
    script: str,
    document_paths: Iterable[tuple[str, str]],
    fact_claims: Iterable[str] | None = None,
    n: int = 5,
) -> OriginalityReport:
    """Check script originality against source documents + fact claims.

    Args:
        script: The final narration script.
        document_paths: Iterable of (filename, file_path, file_type) — wait,
            actually (filename, file_path) and we detect type. Simpler:
            iterable of (name, path) where path is parsed via document_parser.
        fact_claims: Optional list of extracted fact claim strings (also checked).
        n: n-gram size.
    """
    sources: list[tuple[str, str]] = []

    # Load documents
    for name, path in document_paths:
        try:
            text = parse_document(path)
            sources.append((name, text))
        except DocumentParseError:
            continue

    # Also check against fact claims (the LLM-extracted intermediate text)
    if fact_claims:
        combined_claims = " ".join(fact_claims)
        sources.append(("extracted_facts", combined_claims))

    return check_originality(script, sources, n=n)
