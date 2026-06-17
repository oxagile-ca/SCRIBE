"""QA bug-pattern classifier.

Reads `~/.claude/skills/qa-evidence/patterns.yml` and returns the rule IDs that
apply to a given PR / ticket. Used by:
  - qa-evidence skill Phase 1 to inject pattern TCs into the manifest
  - council code-reviewer to flag uncovered patterns in the reviewer prompt

Single source of truth so the skill and council never drift.
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

DEFAULT_PATTERNS_PATH = Path(
    os.environ.get(
        "QA_PATTERNS_FILE",
        str(Path.home() / ".claude" / "skills" / "qa-evidence" / "patterns.yml"),
    )
)


@dataclass(frozen=True)
class PatternMatch:
    id: str
    name: str
    why: str
    matched_on: tuple[str, ...]


def _load(path: Path = DEFAULT_PATTERNS_PATH) -> dict:
    if not path.exists():
        return {"rules": [], "baseline_always_on": []}
    return yaml.safe_load(path.read_text()) or {"rules": [], "baseline_always_on": []}


def _file_matches(paths: Iterable[str], globs: Iterable[str]) -> list[str]:
    hits: list[str] = []
    for p in paths:
        for g in globs:
            if fnmatch.fnmatch(p, g):
                hits.append(f"file:{p} (rule glob {g})")
                break
    return hits


def _keyword_matches(text: str, patterns: Iterable[str]) -> list[str]:
    hits: list[str] = []
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            hits.append(f"kw:/{pat}/")
    return hits


def classify(
    *,
    changed_files: Iterable[str] = (),
    diff_text: str = "",
    ticket_text: str = "",
    patterns_path: Path = DEFAULT_PATTERNS_PATH,
) -> list[PatternMatch]:
    """Return PatternMatch objects for every rule whose triggers apply.

    A rule applies if ANY of:
      - one of its file globs matches one of `changed_files`
      - one of its keyword regexes matches the combined diff+ticket text
      - its `always: true` is set (baseline-style always-on rule)
    """
    cfg = _load(patterns_path)
    text = f"{diff_text}\n{ticket_text}"
    out: list[PatternMatch] = []
    for rule in cfg.get("rules", []):
        triggers = rule.get("triggers") or {}
        matched: list[str] = []
        if triggers.get("always"):
            matched.append("always:true")
        matched += _file_matches(changed_files, triggers.get("files") or [])
        matched += _keyword_matches(text, triggers.get("keywords") or [])
        if matched:
            out.append(
                PatternMatch(
                    id=rule["id"],
                    name=rule.get("name", rule["id"]),
                    why=rule.get("description", ""),
                    matched_on=tuple(matched),
                )
            )
    return out


def inject_tcs_for_manifest(
    matches: Iterable[PatternMatch],
    ticket_key: str,
    patterns_path: Path = DEFAULT_PATTERNS_PATH,
) -> list[dict]:
    """Render matched rules into manifest TC dicts.

    TC id shape: TC-PAT-<rule_id>-<id_suffix> (e.g. TC-PAT-P3_block_reorder-001).
    Always tagged @<ticket_key> and @qa-pattern so they're filterable in reports.
    """
    cfg = _load(patterns_path)
    by_id = {r["id"]: r for r in cfg.get("rules", [])}
    tcs: list[dict] = []
    for m in matches:
        rule = by_id.get(m.id)
        if not rule:
            continue
        for tc in rule.get("inject_tcs") or []:
            tcs.append(
                {
                    "id": f"TC-PAT-{m.id}-{tc.get('id_suffix','001')}",
                    "title": tc["title"],
                    "type": tc.get("type", "manual"),
                    "priority": tc.get("priority", "P1"),
                    "evidence_required": tc.get("evidence_required", ["screenshot"]),
                    "tags": [f"@{ticket_key}", "@qa-pattern", f"@pattern-{m.id}"],
                    "notes": tc.get("notes", "") + f"  [matched on: {', '.join(m.matched_on)}]",
                    "assertion_hints": tc.get("assertion_hints", []),
                }
            )
    return tcs


def baseline_checklist(patterns_path: Path = DEFAULT_PATTERNS_PATH) -> list[str]:
    """Return the always-on baseline lines (independent of rule matches)."""
    cfg = _load(patterns_path)
    return list(cfg.get("baseline_always_on", []))
