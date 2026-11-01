"""Extract CJK characters and words missing from the 火星文 dict."""
from __future__ import annotations

import re
from pathlib import Path

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
DICT_DEFAULT = Path.home() / "AppData/Roaming/Rime/locker.dict.yaml"


def load_dict_words(path: Path | None = None) -> set[str]:
    path = path or DICT_DEFAULT
    words: set[str] = set()
    in_body = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "...":
            in_body = True
            continue
        if not in_body or not line.strip() or line.startswith("#"):
            continue
        word = line.split("\t", 1)[0].strip()
        if word:
            words.add(word)
    return words


def _unmatched_after_known(span: str, known: set[str]) -> list[str]:
    """Cover with longest known 詞 of length >= 2; return leftover runs."""
    n = len(span)
    i = 0
    runs: list[str] = []
    start: int | None = None
    while i < n:
        matched = 0
        for length in range(n - i, 1, -1):
            if span[i : i + length] in known:
                matched = length
                break
        if matched:
            if start is not None:
                runs.append(span[start:i])
                start = None
            i += matched
        else:
            if start is None:
                start = i
            i += 1
    if start is not None:
        runs.append(span[start:])
    return runs


def _pack_bigrams(span: str) -> list[str]:
    """Non-overlapping 2-char 詞 from the left. Odd last char is a 字, not a 詞."""
    return [span[i : i + 2] for i in range(0, len(span) - 1, 2)]


def _word_candidates(span: str, known: set[str]) -> list[str]:
    """Leftover 詞: subtract known words, then pack 2-chars. No particle list, no n-grams."""
    if not span or span in known:
        return []
    out: list[str] = []
    for piece in _unmatched_after_known(span, known):
        if not piece or piece in known:
            continue
        out.extend(_pack_bigrams(piece))
    return out


def extract_missing(
    raw_text: str,
    known: set[str] | None = None,
    skipped: set[str] | None = None,
) -> list[str]:
    """Missing 字 first, then missing 2-char 詞. `skipped` are previously finalized skips."""
    known = known if known is not None else set()
    ignore = known | (skipped or set())
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        if not s or s in ignore or s in seen:
            return
        seen.add(s)
        out.append(s)

    spans = CJK_RE.findall(raw_text or "")
    for span in spans:
        for ch in span:
            add(ch)
    for span in spans:
        for word in _word_candidates(span, known):
            add(word)
    return out
