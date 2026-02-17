from __future__ import annotations

import re


HARD_MISSED_PHRASES = [
    "expired",
    "offer has expired",
    "this offer has expired",
    "no longer",
    "not available",
    "not availible",
    "unavailable",
]

ALREADY_CLAIMED_SUCCESS_PHRASES = [
    "already been claimed",
    "already claimed",
    "offer has already been claimed",
    "this offer has already been claimed",
]


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def split_lines(text: str) -> list[str]:
    raw_lines = [re.sub(r"\s+", " ", l).strip() for l in str(text or "").splitlines()]
    return [l for l in raw_lines if l]


def match_phrase_detail(tnorm: str, lines: list[str], phrases: list[str]) -> tuple[bool, str]:
    for phrase in phrases:
        p = str(phrase or "").strip().lower()
        if not p:
            continue
        if p in tnorm:
            detail = next((l for l in lines if p in l.lower()), p)
            return True, detail
    return False, ""


def classify_result_text(
    text: str, success_patterns: list[str] | None, fail_patterns: list[str] | None
) -> tuple[str, str]:
    tnorm = norm_text(text)
    lines = split_lines(text)

    ok, detail = match_phrase_detail(tnorm, lines, ALREADY_CLAIMED_SUCCESS_PHRASES)
    if ok:
        return "success", detail

    ok, detail = match_phrase_detail(tnorm, lines, HARD_MISSED_PHRASES)
    if ok:
        return "missed", detail

    fail = [norm_text(x) for x in (fail_patterns or []) if str(x).strip()]
    for pat in fail:
        if pat and pat in tnorm:
            detail = next((l for l in lines if pat in l.lower()), pat)
            return "fail", detail

    succ = [norm_text(x) for x in (success_patterns or []) if str(x).strip()]
    if succ:
        for l in lines:
            low = l.lower()
            if all(p in low for p in succ):
                return "success", l
        if len(succ) >= 2 and all(p in tnorm for p in succ[:2]):
            detail = next((l for l in lines if succ[0] in l.lower()), succ[0])
            return "success", detail

    return "none", ""
