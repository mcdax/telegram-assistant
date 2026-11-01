"""Tests for meme parsing, memegen URL building and the image download."""
from __future__ import annotations

import re

import aiohttp
import pytest
from aioresponses import aioresponses

from telegram_assistant.modules.meme.render import (
    MemeError,
    MemeSpec,
    build_url,
    escape_line,
    fetch_image,
    parse_spec,
)

PNG = b"\x89PNG\r\n\x1a\nfake"


# ---------- escaping ----------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "_"),
        ("hello world", "hello_world"),
        ("snake_case", "snake__case"),
        ("dash-dash", "dash--dash"),
        ("what?", "what~q"),
        ("a&b", "a~ab"),
        ("100%", "100~p"),
        ("#tag", "~htag"),
        ("a/b", "a~sb"),
        ("a\\b", "a~bb"),
        ("<x>", "~lx~g"),
        ('say "hi"', "say_''hi''"),
        ("two\nlines", "two~nlines"),
        # escapes never feed into each other
        ("_ -", "___--"),
        ("~q", "~q"),
        # non-ASCII is percent-encoded after escaping
        ("Grüße", "Gr%C3%BC%C3%9Fe"),
    ],
)
def test_escape_line(text, expected):
    assert escape_line(text) == expected


def test_build_url_template():
    spec = MemeSpec(kind="template", template_id="drake", lines=("Arbeit", "Memes machen?"))
    assert build_url(spec) == "https://api.memegen.link/images/drake/Arbeit/Memes_machen~q.png"


def test_build_url_template_empty_line_and_custom_base():
    spec = MemeSpec(kind="template", template_id="ds", lines=("", "b"))
    assert build_url(spec, base="http://memegen:5000/") == "http://memegen:5000/images/ds/_/b.png"


def test_build_url_custom_encodes_background():
    spec = MemeSpec(kind="custom", image_url="https://x.org/a b.jpg?s=1&t=2", lines=("top", "bottom"))
    assert build_url(spec) == (
        "https://api.memegen.link/images/custom/top/bottom.png"
        "?background=https%3A%2F%2Fx.org%2Fa%20b.jpg%3Fs%3D1%26t%3D2"
    )


# ---------- parsing ----------

def test_parse_template():
    spec = parse_spec('{"kind":"template","template_id":"drake","lines":["a","b"]}')
    assert spec == MemeSpec(kind="template", template_id="drake", lines=("a", "b"))
    assert spec.label == "drake"


def test_parse_custom():
    spec = parse_spec('{"kind":"custom","image_url":"https://i.imgur.com/x.png","lines":["t","b"]}')
    assert spec.kind == "custom" and spec.image_url == "https://i.imgur.com/x.png"
    assert spec.label == "custom"


def test_parse_strips_fences_and_prose():
    raw = 'Klar!\n```json\n{"kind":"template","template_id":"fry","lines":["x"]}\n```\nViel Spaß {nope}'
    assert parse_spec(raw).template_id == "fry"


def test_parse_caps_line_length():
    spec = parse_spec('{"kind":"template","template_id":"fry","lines":["' + "a" * 100 + '"]}', max_line_chars=10)
    assert spec.lines == ("a" * 9 + "…",)


@pytest.mark.parametrize(
    "raw",
    [
        "no json here",
        "{broken",
        "[1,2]",
        '{"kind":"template","template_id":"drake"}',
        '{"kind":"template","template_id":"drake","lines":[]}',
        '{"kind":"template","template_id":"../etc","lines":["a"]}',
        '{"kind":"custom","image_url":"ftp://x","lines":["a"]}',
        '{"kind":"gif","lines":["a"]}',
        '{"kind":"template","template_id":"drake","lines":[{"x":1}]}',
    ],
)
def test_parse_invalid_is_retryable(raw):
    with pytest.raises(MemeError) as err:
        parse_spec(raw)
    assert err.value.retryable


def test_spec_to_json_roundtrip():
    spec = MemeSpec(kind="custom", image_url="https://x/y.png", lines=("ä", "b"))
    assert parse_spec(spec.to_json()) == spec


# ---------- download ----------

URL = "https://api.memegen.link/images/drake/a/b.png"


async def test_fetch_image_writes_png(tmp_path):
    with aioresponses() as m:
        m.get(URL, body=PNG, content_type="image/png")
        async with aiohttp.ClientSession() as http:
            path = await fetch_image(http, URL, tmp_path / "m.png")
    assert path.read_bytes() == PNG


async def test_fetch_image_4xx_is_retryable(tmp_path):
    with aioresponses() as m:
        m.get(URL, status=404, body="Template not found")
        async with aiohttp.ClientSession() as http:
            with pytest.raises(MemeError, match="HTTP 404: Template not found") as err:
                await fetch_image(http, URL, tmp_path / "m.png")
    assert err.value.retryable
    assert not (tmp_path / "m.png").exists()


async def test_fetch_image_non_image_is_retryable(tmp_path):
    with aioresponses() as m:
        m.get(URL, body="<html>", content_type="text/html")
        async with aiohttp.ClientSession() as http:
            with pytest.raises(MemeError, match="kein Bild") as err:
                await fetch_image(http, URL, tmp_path / "m.png")
    assert err.value.retryable


async def test_fetch_image_5xx_and_network_not_retryable(tmp_path):
    with aioresponses() as m:
        m.get(URL, status=502)
        m.get(re.compile(r".*"), exception=aiohttp.ClientConnectionError("down"))
        async with aiohttp.ClientSession() as http:
            with pytest.raises(MemeError) as e1:
                await fetch_image(http, URL, tmp_path / "m.png")
            with pytest.raises(MemeError) as e2:
                await fetch_image(http, URL, tmp_path / "m.png")
    assert not e1.value.retryable and not e2.value.retryable
