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


from telegram_assistant.markers import MarkerRegistry, DuplicateTriggerError


def test_registry_returns_winning_marker():
    reg = MarkerRegistry()
    reg.register("drafting", [
        Marker("draft", "/draft", MatchKind.CONTAINS, priority=50),
        Marker("auto_on", "/auto on", MatchKind.EXACT, priority=100),
    ])
    match = reg.resolve("/auto on")
    assert match is not None
    assert match.module == "drafting"
    assert match.marker.name == "auto_on"
    assert match.remainder == ""


def test_registry_priority_beats_lower():
    reg = MarkerRegistry()
    reg.register("drafting", [Marker("draft", "/draft", MatchKind.CONTAINS, priority=50)])
    reg.register("correcting", [Marker("fix", "/fix", MatchKind.CONTAINS, priority=70)])
    match = reg.resolve("hello /draft /fix world")
    assert match is not None
    assert match.module == "correcting"
    assert match.marker.name == "fix"


def test_registry_no_match_returns_none():
    reg = MarkerRegistry()
    reg.register("drafting", [Marker("draft", "/draft", MatchKind.CONTAINS, priority=50)])
    assert reg.resolve("hello world") is None


def test_registry_duplicate_trigger_rejected_across_modules():
    reg = MarkerRegistry()
    reg.register("drafting", [Marker("draft", "/x", MatchKind.CONTAINS, priority=50)])
    import pytest
    with pytest.raises(DuplicateTriggerError):
        reg.register("correcting", [Marker("fix", "/x", MatchKind.CONTAINS, priority=70)])


def test_registry_duplicate_trigger_rejected_same_module():
    reg = MarkerRegistry()
    import pytest
    with pytest.raises(DuplicateTriggerError):
        reg.register("drafting", [
            Marker("a", "/x", MatchKind.CONTAINS, priority=50),
            Marker("b", "/x", MatchKind.EXACT, priority=90),
        ])
