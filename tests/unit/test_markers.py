from telegram_assistant.markers import Marker, MatchKind


def test_exact_match_case_insensitive_trimmed():
    m = Marker(name="auto_on", trigger="/auto on", kind=MatchKind.EXACT, priority=100)
    ok, remainder = m.match("  /AUTO on  ")
    assert ok is True
    assert remainder == ""


def test_exact_match_rejects_extra_content():
    m = Marker(name="auto_on", trigger="/auto on", kind=MatchKind.EXACT, priority=100)
    ok, remainder = m.match("/auto on now")
    assert ok is False
    assert remainder is None


def test_contains_match_strips_marker_and_returns_remainder():
    m = Marker(name="draft", trigger="/draft", kind=MatchKind.CONTAINS, priority=50)
    ok, remainder = m.match("please /draft and ask about tomorrow")
    assert ok is True
    assert remainder == "please and ask about tomorrow"


def test_contains_match_empty_remainder():
    m = Marker(name="draft", trigger="/draft", kind=MatchKind.CONTAINS, priority=50)
    ok, remainder = m.match("/draft")
    assert ok is True
    assert remainder == ""


def test_no_match():
    m = Marker(name="draft", trigger="/draft", kind=MatchKind.CONTAINS, priority=50)
    ok, remainder = m.match("hello world")
    assert ok is False
    assert remainder is None


def test_contains_takes_first_occurrence():
    m = Marker(name="draft", trigger="/draft", kind=MatchKind.CONTAINS, priority=50)
    ok, remainder = m.match("a /draft b /draft c")
    assert ok is True
    # Only first occurrence is stripped; subsequent stays in the remainder.
    assert remainder == "a b /draft c"
