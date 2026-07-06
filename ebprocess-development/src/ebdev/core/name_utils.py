# -*- coding: utf-8 -*-
"""
name_utils.py
=============
Shared, configurable name extraction and sanitization utilities.

Provides a single source of truth for deriving kebab-case feature names from
ticket titles / task names and for producing git-safe branch names.  All
stop-word lists are configurable via module-level constants so that downstream
callers (or configuration) can tune extraction without modifying core logic.

Usage
-----
    from ebdev.core.name_utils import extract_feature_name, sanitize_branch_name

    feature = extract_feature_name("Create enquiry form page")
    # → "enquiry"

    branch = sanitize_branch_name("User Authentication")
    # → "User-Authentication"
"""

from __future__ import annotations

import re
from typing import Collection, Optional

# ---------------------------------------------------------------------------
# Configurable stop-word sets
# ---------------------------------------------------------------------------
# These sets are configurable module-level constants.  You may add or remove
# entries at import-time before any extraction happens, or subclass
# FeatureNameExtractor and override them.

#: Verbs commonly used in ticket / task titles that carry no domain meaning.
GENERIC_VERBS: Collection[str] = frozenset({
    "create", "add", "build", "implement", "update", "fix", "remove",
    "delete", "edit", "manage", "show", "view", "list", "get", "set",
    "enable", "disable", "integrate", "migrate", "refactor", "optimise",
    "optimize", "replace", "convert", "handle", "support", "submit",
})

#: Generic nouns that describe the *type* of work rather than the domain.
GENERIC_NOUNS: Collection[str] = frozenset({
    "feature", "screen", "page", "ui", "flow", "form", "button",
    "input", "field", "text", "content", "body", "component",
    "module", "section", "dialog", "modal", "toast", "banner",
    "header", "footer", "sidebar", "navbar", "bug", "error",
    "issue", "task", "item", "entry",
})

#: UI / presentation terms — stripped only when the task also targets a
#: backend platform; kept when the task is UI-only so they can form part of
#: the feature name.
UI_TERMS: Collection[str] = frozenset({
    "widget", "card", "tile", "grid", "listview", "scaffold",
    "appbar", "drawer", "bottom", "sheet", "snackbar",
    "tab", "tabs", "carousel", "slider", "stepper", "chip",
})

#: Highly generic English words that never contribute to a feature name.
FUNCTION_WORDS: Collection[str] = frozenset({
    "and", "or", "the", "a", "an", "for", "with", "of", "in", "on",
    "at", "to", "from", "by", "is", "be", "are", "was", "were",
    "that", "this", "it", "its", "has", "have", "will", "would",
    "should", "can", "could", "may", "might", "not", "no", "yes",
    "we", "our", "us", "i", "me", "my", "you", "your", "he", "she",
    "they", "them", "their",
})

#: Domain nouns that represent common form fields but not business entities.
#: Stripped during extraction so "enquiry title description" → "enquiry".
FORM_FIELD_TERMS: Collection[str] = frozenset({
    "title", "description", "name", "email", "phone", "address",
    "message", "comment", "note", "status", "type", "category",
    "tag", "label", "date", "time", "url", "link", "image",
    "file", "attachment", "value", "key",
})

#: Combined set of all words that should ALWAYS be stripped.
ALWAYS_STRIP: Collection[str] = (
    GENERIC_VERBS
    | GENERIC_NOUNS
    | FUNCTION_WORDS
    | FORM_FIELD_TERMS
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_FEATURE_FALLBACK: str = "feature"
_MAX_FEATURE_WORDS: int = 3   # Maximum words to keep in a feature name

# Regex fragments (compiled lazily)
_CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")        # camelCase → kebab
_NON_SLUG_RE = re.compile(r"[^a-z0-9-]")                # strip non-slug chars
_COLLAPSE_DASHES_RE = re.compile(r"-+")                 # collapse -- to -


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract_feature_name(
    name: str,
    *,
    max_words: int = _MAX_FEATURE_WORDS,
    extra_strip: Optional[Collection[str]] = None,
    extra_keep: Optional[Collection[str]] = None,
) -> str:
    """
    Derive a concise, kebab-case feature name from a ticket / task title.

    Strategy
    --------
    1. Lowercase the input.
    2. Tokenise on word boundaries (spaces, dashes, camelCase splits).
    3. Remove words from ``ALWAYS_STRIP`` (configurable module-level set).
    4. Remove words from ``extra_strip`` if supplied.
    5. Retain words from ``extra_keep`` even if they appear in strip sets.
    6. Keep up to ``max_words`` remaining words.
    7. Join with ``-`` and collapse runs of dashes.

    Parameters
    ----------
    name : str
        The raw ticket title or task name.
    max_words : int
        Maximum number of meaningful words to retain (default 3).
    extra_strip : Collection[str] | None
        Additional words to strip beyond the global ``ALWAYS_STRIP`` set.
    extra_keep : Collection[str] | None
        Words that should NEVER be stripped, even if they appear in a
        strip set.  Useful for domain terms that happen to collide with
        generic words (e.g. a project named "feature").

    Returns
    -------
    str
        A kebab-case feature name, or ``"feature"`` if no meaningful words
        remain after stripping.
    """
    if not name or not name.strip():
        return _DEFAULT_FEATURE_FALLBACK

    # ---------- 1. Normalise ----------
    lower = name.strip().lower()

    # ---------- 2. Tokenise ----------
    # Split on camelCase boundaries
    dash_separated = _CAMEL_CASE_RE.sub("-", lower)

    # Split into word tokens (preserve sequence)
    tokens = re.split(r"[\s_-]+", dash_separated)
    tokens = [t for t in tokens if t]   # drop empty segments

    if not tokens:
        return _DEFAULT_FEATURE_FALLBACK

    # ---------- 3. Build the effective strip / keep sets ----------
    strip_set: set[str] = set(ALWAYS_STRIP)
    if extra_strip:
        strip_set.update(word.lower() for word in extra_strip)

    keep_set: set[str] = set()
    if extra_keep:
        keep_set = {word.lower() for word in extra_keep}

    # ---------- 4. Filter ----------
    meaningful: list[str] = []
    for token in tokens:
        if token in keep_set:
            meaningful.append(token)
        elif token not in strip_set:
            meaningful.append(token)

        if len(meaningful) >= max_words:
            break

    if not meaningful:
        meaningful = [_DEFAULT_FEATURE_FALLBACK]

    # ---------- 5. Format as kebab-case ----------
    slug = "-".join(meaningful)
    slug = _NON_SLUG_RE.sub("", slug)
    slug = _COLLAPSE_DASHES_RE.sub("-", slug).strip("-")

    return slug or _DEFAULT_FEATURE_FALLBACK


def sanitize_branch_name(name: str) -> str:
    """
    Sanitise a string for use as a git branch name.

    Parameters
    ----------
    name : str
        The raw name to sanitise.

    Returns
    -------
    str
        A branch-safe string with only alphanumeric, dash, and underscore
        characters.
    """
    sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "-", name)
    sanitized = _COLLAPSE_DASHES_RE.sub("-", sanitized)
    return sanitized.strip("-")


# ---------------------------------------------------------------------------
# Backward-compatible wrappers
# ---------------------------------------------------------------------------
def derive_feature_name_from_task(task_name: str) -> str:
    """
    Convenience wrapper tailored for EpicTask name strings.

    Equivalent to ``extract_feature_name(task_name)`` — preserved so that
    existing callers in the orchestration layer can migrate without
    signature changes.
    """
    return extract_feature_name(task_name)
