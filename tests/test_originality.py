"""Tests for the originality checker — n-gram overlap detection."""

from gpcg.domain.originality import (
    OriginalityReport,
    _normalize,
    _tokenize,
    _ngrams,
    check_originality,
    compare_texts,
)


class TestNormalization:
    def test_lowercase(self):
        assert _normalize("Hello WORLD") == "hello world"

    def test_strip_accents(self):
        assert _normalize("narracão vídeo") == "narracao video"
        assert _normalize("café à pás") == "cafe a pas"

    def test_strip_punctuation(self):
        assert _normalize("hello, world! how's it going?") == "hello world how s it going"

    def test_collapse_whitespace(self):
        assert _normalize("  multiple   spaces  ") == "multiple spaces"

    def test_empty(self):
        assert _normalize("") == ""
        assert _normalize("...,,,!!!") == ""


class TestNgrams:
    def test_basic_ngrams(self):
        tokens = ["a", "b", "c", "d", "e"]
        ng = _ngrams(tokens, 3)
        assert ng == {("a", "b", "c"), ("b", "c", "d"), ("c", "d", "e")}

    def test_too_few_tokens(self):
        assert _ngrams(["a", "b"], 3) == set()

    def test_empty(self):
        assert _ngrams([], 3) == set()


class TestCompareTexts:
    def test_identical_text_full_overlap(self):
        text = "O jogo Bully foi desenvolvido pela Rockstar Vancouver em dois mil e seis."
        overlap, matches = compare_texts(text, text, n=5)
        assert overlap == 1.0
        assert len(matches) > 0
        # Longest match should be most of the sentence
        assert len(matches[0].split()) >= 5

    def test_completely_different_text_zero_overlap(self):
        script = "Você sabia que existe um segredo incrível escondido no jogo?"
        source = "The weather today is sunny and warm with light breeze."
        overlap, matches = compare_texts(script, source, n=5)
        assert overlap == 0.0
        assert matches == []

    def test_paraphrase_low_overlap(self):
        """A good paraphrase should have low n-gram overlap."""
        source = "O jogo Bully foi desenvolvido pela Rockstar Vancouver e lançado em 2006."
        paraphrase = "Lançado no ano de dois mil e seis, esse título foi criado pelo estúdio Rockstar Vancouver."
        overlap, matches = compare_texts(paraphrase, source, n=5)
        # A good paraphrase shares the FACT but few 5-gram word sequences
        assert overlap < 0.3, f"paraphrase overlap too high: {overlap}"

    def test_verbatim_copy_high_overlap(self):
        """Verbatim copying should be detected."""
        source = "O jogo Bully foi desenvolvido pela Rockstar Vancouver e lançado em 2006 para PlayStation 2."
        # Copy a long chunk verbatim
        script = "Sabia que " + source + " Isso é incrível não é mesmo?"
        overlap, matches = compare_texts(script, source, n=5)
        assert overlap > 0.5, f"verbatim copy should have high overlap: {overlap}"
        assert len(matches) > 0

    def test_empty_inputs(self):
        overlap, matches = compare_texts("", "some source", n=5)
        assert overlap == 0.0
        assert matches == []
        overlap, matches = compare_texts("some script", "", n=5)
        assert overlap == 0.0


class TestCheckOriginality:
    def test_no_sources_perfect_score(self):
        report = check_originality("any script text here", [], n=5)
        assert report.score == 100.0
        assert report.max_overlap == 0.0
        assert report.is_original is True
        assert report.sources_checked == 0

    def test_single_source_low_overlap(self):
        script = "Você sabia que existe um segredo incrível escondido no jogo Bully?"
        source = ("doc1", "The game features an open world environment with multiple missions.")
        report = check_originality(script, [source], n=5)
        assert report.score > 70.0
        assert report.is_original is True
        # No overlap → matched_source is None (no source actually matched)
        assert report.matched_source is None
        assert report.sources_checked == 1

    def test_multiple_sources_worst_case(self):
        script = "O jogo foi desenvolvido pela Rockstar Vancouver e lançado em 2006."
        sources = [
            ("doc1", "Completely unrelated text about cooking recipes."),
            ("doc2", "O jogo foi desenvolvido pela Rockstar Vancouver e lançado em 2006 para PS2."),
            ("doc3", "Other unrelated content."),
        ]
        report = check_originality(script, sources, n=5)
        # doc2 has the highest overlap
        assert report.matched_source == "doc2"
        assert report.max_overlap > 0.5
        assert report.score < 50.0
        assert report.is_original is False

    def test_threshold_customization(self):
        # Build a script with partial overlap (score between 10 and 70)
        script = "Você sabia que o jogo foi desenvolvido pela Rockstar Vancouver em dois mil e seis que incrível não é mesmo"
        source = ("doc", "O jogo foi desenvolvido pela Rockstar Vancouver e lançado em 2006 para PlayStation 2 com gráficos")
        report_default = check_originality(script, [source], n=5, threshold=70.0)
        report_low = check_originality(script, [source], n=5, threshold=10.0)
        # Score is the same regardless of threshold
        assert report_default.score == report_low.score
        # With low threshold, even a moderate overlap passes
        assert report_low.is_original is True
        # With high threshold, it might not pass (depends on overlap)
        # Just verify the threshold logic works
        assert report_default.threshold == 70.0
        assert report_low.threshold == 10.0

    def test_report_to_dict(self):
        report = OriginalityReport(
            score=85.5,
            max_overlap=0.145,
            matched_source="doc1",
            longest_matches=["o jogo bully foi"],
            n_gram_size=5,
            sources_checked=3,
            threshold=70.0,
        )
        d = report.to_dict()
        assert d["score"] == 85.5
        assert d["matched_source"] == "doc1"
        assert d["is_original"] is True
        assert d["threshold"] == 70.0

    def test_empty_sources_in_list_skipped(self):
        script = "o jogo foi desenvolvido pela rockstar vancouver"
        sources = [("empty", ""), ("real", "o jogo foi desenvolvido pela rockstar vancouver em 2006")]
        report = check_originality(script, sources, n=5)
        # The empty source is skipped; "real" has overlap
        assert report.matched_source == "real"
        assert report.max_overlap > 0.0

    def test_portuguese_with_accents(self):
        """Portuguese text with accents should be normalized properly."""
        script = "Você sabia que o café é a bebida mais popular do Brasil?"
        source = ("doc", "Você sabia que o café é a bebida mais popular do Brasil atualmente?")
        report = check_originality(script, [source], n=5)
        # High overlap because most 5-grams match (after accent stripping)
        assert report.max_overlap > 0.4
        assert report.matched_source == "doc"
