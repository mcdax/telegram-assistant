"""Pure helpers for the meme module: agent-answer parsing, memegen.link URL
building and the image download. No module state, so it's unit-testable.

The OpenClaw agent answers with JSON only, one of::

    {"kind": "template", "template_id": "drake", "lines": ["…", "…"]}
    {"kind": "custom", "image_url": "https://…", "lines": ["top", "bottom"]}

Rendering is delegated to https://memegen.link (``/images/{id}/{l1}/{l2}.png``
or ``/images/custom/{top}/{bottom}.png?background=<url>``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import aiohttp
from yarl import URL

DEFAULT_RENDER_BASE = "https://api.memegen.link"
MAX_LINES = 8
MAX_IMAGE_BYTES = 10 * 1024 * 1024

_TEMPLATE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.IGNORECASE)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")

# memegen.link special-character escapes, applied per character so the
# replacements never feed into each other (e.g. "-" → "--" vs. "_" → "__").
_ESCAPES = {
    "_": "__",
    "-": "--",
    " ": "_",
    "?": "~q",
    "&": "~a",
    "%": "~p",
    "#": "~h",
    "/": "~s",
    "\\": "~b",
    "<": "~l",
    ">": "~g",
    '"': "''",
    "\n": "~n",
}


class MemeError(Exception):
    """A generation step failed. ``retryable`` errors are fed back to the
    agent for one more attempt (bad JSON, unknown template, non-image)."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class MemeSpec:
    kind: str                      # "template" | "custom"
    lines: tuple[str, ...]
    template_id: str | None = None
    image_url: str | None = None

    @property
    def label(self) -> str:
        """Short human name for captions / the 'other template' hint."""
        return self.template_id if self.kind == "template" else "custom"

    def to_json(self) -> str:
        data: dict[str, object] = {"kind": self.kind}
        if self.kind == "template":
            data["template_id"] = self.template_id
        else:
            data["image_url"] = self.image_url
        data["lines"] = list(self.lines)
        return json.dumps(data, ensure_ascii=False)


def escape_line(text: str) -> str:
    """One memegen path segment: special-char escapes, then URL-quoting of
    whatever is left (umlauts, emoji). Empty line → ``_`` (blank)."""
    if not text:
        return "_"
    escaped = "".join(_ESCAPES.get(ch, ch) for ch in text)
    return quote(escaped, safe="_-~'")


def _cap(line: str, max_chars: int) -> str:
    line = line.replace("\r", "").strip()
    if max_chars > 0 and len(line) > max_chars:
        return line[: max_chars - 1].rstrip() + "…"
    return line


def parse_spec(raw: str, *, max_line_chars: int = 80) -> MemeSpec:
    """Parse the agent answer. Tolerates ``` fences and prose around the
    object by decoding the first ``{…}``. Raises a retryable MemeError."""
    text = _FENCE.sub("", raw.strip()).strip()
    start = text.find("{")
    if start < 0:
        raise MemeError("Antwort enthält kein JSON-Objekt")
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise MemeError(f"ungültiges JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise MemeError("JSON ist kein Objekt")

    lines_raw = data.get("lines")
    if not isinstance(lines_raw, list) or not lines_raw:
        raise MemeError('"lines" fehlt oder ist leer')
    if not all(isinstance(x, (str, int, float)) for x in lines_raw):
        raise MemeError('"lines" muss eine Liste von Strings sein')
    if len(lines_raw) > MAX_LINES:
        raise MemeError(f'zu viele "lines" ({len(lines_raw)} > {MAX_LINES})')
    lines = tuple(_cap(str(x), max_line_chars) for x in lines_raw)

    kind = data.get("kind")
    if kind == "template":
        tid = data.get("template_id")
        if not isinstance(tid, str) or not _TEMPLATE_ID.match(tid):
            raise MemeError(f'ungültige "template_id": {tid!r}')
        return MemeSpec(kind="template", template_id=tid, lines=lines)
    if kind == "custom":
        url = data.get("image_url")
        if not isinstance(url, str) or not re.match(r"^https?://\S+$", url):
            raise MemeError(f'ungültige "image_url": {url!r}')
        return MemeSpec(kind="custom", image_url=url, lines=lines)
    raise MemeError(f'unbekanntes "kind": {kind!r} (erwartet "template" oder "custom")')


def build_url(spec: MemeSpec, *, base: str = DEFAULT_RENDER_BASE) -> str:
    base = base.rstrip("/")
    segments = "/".join(escape_line(line) for line in spec.lines)
    if spec.kind == "template":
        return f"{base}/images/{spec.template_id}/{segments}.png"
    assert spec.image_url is not None
    return f"{base}/images/custom/{segments}.png?background={quote(spec.image_url, safe='')}"


async def fetch_image(
    http: aiohttp.ClientSession, url: str, dest: Path, *, timeout_s: float = 30,
) -> Path:
    """Download the rendered meme to ``dest``.

    4xx (unknown template, bad background) and non-image bodies are
    retryable — the agent can pick something else. 5xx/network errors are
    not: asking the agent again wouldn't help.
    """
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with http.get(URL(url, encoded=True), timeout=timeout) as resp:
            if 400 <= resp.status < 500:
                detail = (await resp.text(errors="replace"))[:200].strip()
                raise MemeError(f"memegen HTTP {resp.status}: {detail or resp.reason}")
            if resp.status >= 300:
                raise MemeError(f"memegen HTTP {resp.status}", retryable=False)
            ctype = resp.headers.get("Content-Type", "")
            if not ctype.startswith("image/"):
                raise MemeError(f"memegen lieferte kein Bild (Content-Type {ctype or '?'})")
            if int(resp.headers.get("Content-Length") or 0) > MAX_IMAGE_BYTES:
                raise MemeError("Bild zu groß", retryable=False)
            body = await resp.read()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise MemeError(f"memegen nicht erreichbar: {exc!r}", retryable=False) from exc
    if not body:
        raise MemeError("memegen lieferte ein leeres Bild")
    if len(body) > MAX_IMAGE_BYTES:
        raise MemeError("Bild zu groß", retryable=False)
    dest.write_bytes(body)
    return dest
