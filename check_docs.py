#!/usr/bin/env python3
"""check_docs.py — the documentation checker. Stdlib only, one file, copied verbatim.

Began as a port of a PowerShell script that was debugged against a real codebase;
the PowerShell version was retired 2026-09-03 (see TODONT.md) and this is now the
only implementation. tests/test_check_docs.py pins what it must find in the
fixture repo.

Four checks:
  1. Docstrings          — every def/class in an enforced file has one.
  2. Doc blocks + syntax — same for brace languages, plus a free syntax gate.
  3. covers: integrity   — every function named in a diagram's `%% covers:`
                           header still exists in the file it names.
  4. Diagram staleness   — which diagrams your working diff has invalidated.

Usage:
  python check_docs.py                # checks 1-3 on enforced files + 4 on the diff
  python check_docs.py --all          # checks 1-3 only, ignore git state
  python check_docs.py --deep         # check 4 narrowed to changed *functions*
  python check_docs.py --audit        # coverage for EVERY source file
  python check_docs.py --render       # parse every diagram, incl. markdown fences
  python check_docs.py --quiet        # exit code only

Exit code is 1 when any check reports a finding, 0 otherwise.

HONEST NOTE, and do not paper over it: check 4 tells you a diagram *might* be
stale. It cannot tell you it *is* — only a human reading the diagram can. If
someone rubber-stamps the output, the gate stops working. That is design intent,
not a bug to fix.

WHAT THIS CANNOT PROTECT: it counts whether a docstring EXISTS. It has no idea
whether the docstring still says anything true, and it cannot stop a rewrite
replacing hard-won evidence with fluent paraphrase. The countermeasure is
epistemic tags — see conventions/CLAUDE-snippet.md.

Configuration is read from docs-toolkit.json at the repo root. Nothing
project-specific belongs in this file; it is copied into repos as-is.
"""

from __future__ import annotations

import argparse
import ast
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "docs-toolkit.json"

# Force UTF-8 on stdout. Windows defaults a PIPED stdout to the ANSI codepage
# (cp1252), which turns the em-dash in every finding into a replacement
# character — so redirecting output to a file or through another tool silently
# corrupts it. Harmless where UTF-8 is already the default.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_CONFIG = {
    "enforced": {"python": [], "javascript": [], "go": [], "swift": [],
                 "kotlin": [], "typescript": [], "rust": [], "csharp": []},
    "skip_dirs": [".git", ".venv", "venv", "node_modules", "__pycache__",
                  "site-packages", "dist", "build", ".pytest_cache",
                  ".mypy_cache", ".claude", "target", "bin", "obj"],
    "diagram_dirs": ["docs"],
    "scan_markdown_diagrams": True,
    "js_syntax_gate": True,
    # Off: Node's type stripping rejects enum/namespace/parameter properties
    # and cannot read .tsx. See typescript_defs.
    "ts_syntax_gate": False,
}


# --- Records ------------------------------------------------------------------

@dataclass
class Definition:
    """One documentable definition found by a language adapter.

    The contract every adapter must return. `end_line` is only used by --deep
    hunk overlap, so an approximation is acceptable where a real parser is not
    available.
    """

    file: str
    line: int
    end_line: int
    name: str
    kind: str          # 'func' | 'class' | 'method' | 'ERROR'
    has_doc: bool


@dataclass
class Cover:
    """One `%% covers:` entry: a source file and the functions claimed in it."""

    source_file: str
    functions: list[str]


@dataclass
class Diagram:
    """A .mmd file and the coverage it declares."""

    path: str
    covers: list[Cover] = field(default_factory=list)


# --- Output -------------------------------------------------------------------

class Reporter:
    """Collects findings and prints them.

    Why a class rather than prints: every output string lives in one place, and
    tests/test_check_docs.py asserts on that output, so wording changes are made
    here and the tests say what they broke.
    """

    def __init__(self, quiet: bool = False) -> None:
        """In: quiet, which suppresses output but NOT the finding count — the
        exit code still has to be right for a hook to act on."""
        self.quiet = quiet
        self.findings = 0

    def line(self, text: str = "") -> None:
        """Print one plain line. No-op under --quiet."""
        if not self.quiet:
            print(text)

    def section(self, title: str) -> None:
        """Print a blank line then a `=== title ===` header.

        The leading blank is part of the output format the tests assert on; do
        not move it to the call sites.
        """
        self.line("")
        self.line(f"=== {title} ===")

    def finding(self, text: str) -> None:
        """Record a finding and print it indented two spaces.

        Counting is separate from printing on purpose, so --quiet still produces
        a correct exit code.
        """
        self.findings += 1
        if not self.quiet:
            print(f"  {text}")


# --- Config -------------------------------------------------------------------

def find_repo_root(start: Path) -> Path:
    """Locate the repo root: nearest ancestor with docs-toolkit.json, else .git.

    Why both: the config file is authoritative because it is what the two
    implementations share, but a repo that has not been configured yet should
    still work from anywhere inside it.

    In: any directory. Out: the root Path. Falls back to `start` when neither
    marker is found, so the tool degrades to "treat cwd as root" rather than
    guessing wildly.
    """
    for candidate in [start, *start.parents]:
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start


def load_config(root: Path) -> dict:
    """Read docs-toolkit.json, merged over the defaults.

    In: repo root. Out: the merged config dict.

    A missing file is NOT an error — an unconfigured repo still runs, with empty
    enforced lists, which is exactly the intended starting state. A malformed
    file IS an error, because silently falling back to defaults would hide a
    typo'd path list and report a false clean.
    """
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    path = root / CONFIG_NAME
    if not path.is_file():
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"{CONFIG_NAME} is unreadable: {exc}\n")
        raise SystemExit(2)
    for key, value in raw.items():
        if key.startswith("_") or key == "schema_version":
            continue
        if key == "enforced" and isinstance(value, dict):
            cfg["enforced"].update(value)
        else:
            cfg[key] = value
    return cfg


def is_skipped(rel_path: str, skip_dirs: list[str]) -> bool:
    """Is any path component a skipped directory?

    Component matching, not substring: a file called `build_tools.py` must not be
    skipped because `build` is in the list. Getting this wrong silently drops
    real files from the audit.
    """
    parts = Path(rel_path).parts
    return any(part in skip_dirs for part in parts)


# --- Adapters -----------------------------------------------------------------
#
# Adding a language is one function returning list[Definition], plus one line in
# defs_for(). The contract is documented on Definition.
#
# Use a real parser when the language ships one (see python_defs). Regex is
# acceptable when it does not — false positives are preferred to silence.


def python_defs(root: Path, rel_path: str) -> list[Definition]:
    """Module-level functions and classes (plus direct methods) via `ast`.

    Why ast rather than regex: decorators, multi-line signatures and nested
    classes all defeat a regex, and any real codebase has all three.

    Nested defs (closures inside functions) are deliberately NOT reported — they
    are usually three lines inside an already-documented parent.

    In: repo root and a repo-relative path. Out: one Definition per definition.
    A parse or read failure yields a single ERROR record rather than raising, so
    one broken file cannot abort a whole audit.
    """
    full = root / rel_path
    try:
        tree = ast.parse(full.read_text(encoding="utf-8"), filename=str(full))
    except (OSError, SyntaxError, ValueError) as exc:
        return [Definition(rel_path, 0, 0, "-", "ERROR", False)]

    out: list[Definition] = []

    def emit(node, kind: str) -> None:
        out.append(Definition(
            file=rel_path,
            line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            name=node.name,
            kind=kind,
            has_doc=ast.get_docstring(node) is not None,
        ))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            emit(node, "func")
        elif isinstance(node, ast.ClassDef):
            emit(node, "class")
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    emit(sub, "method")
    return out


JS_PATTERNS = (
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("),
    re.compile(
        r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
        r"(?:function\s*\(|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"
    ),
    re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?function\s*\("),
)

TRIVIAL_ARROW = re.compile(r"=>\s*[^\s{]")


def javascript_defs(root: Path, rel_path: str) -> list[Definition]:
    """Functions in a JS file, by regex. Deliberately crude.

    There is no parser available without adding an npm dependency, so this
    accepts false positives in exchange for never being silent. If it flags
    something that genuinely needs no block, document it anyway or add an
    exemption rule.

    In: repo root and a repo-relative path. Out: one Definition per match;
    empty list when the file is missing.

    Documented == the nearest preceding non-blank line ends with `*/`. Bare `//`
    lines do NOT count: the convention is a block comment so a contract is
    visually distinct from an inline aside.

    Exempt: a single-expression arrow whose whole body is on the same line
    (`const toRad = d => d * Math.PI / 180`). A six-line block over a one-line
    conversion is the noise that trains people to stop reading blocks.
    `end_line` is approximated as "until the next definition starts".
    """
    full = root / rel_path
    if not full.is_file():
        return []
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()

    out: list[Definition] = []
    for i, text in enumerate(lines):
        name = None
        for pattern in JS_PATTERNS:
            match = pattern.match(text)
            if match:
                name = match.group(1)
                break
        if name is None:
            continue
        if TRIVIAL_ARROW.search(text) and not text.rstrip().endswith("{"):
            continue

        has_doc = False
        for j in range(i - 1, -1, -1):
            prev = lines[j].strip()
            if not prev:
                continue
            has_doc = prev.endswith("*/")
            break

        out.append(Definition(rel_path, i + 1, 0, name, "func", has_doc))

    for k, definition in enumerate(out):
        definition.end_line = (out[k + 1].line - 1) if k + 1 < len(out) else len(lines)
    return out


GO_FUNC = re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*[\(\[]")


def go_defs(root: Path, rel_path: str) -> list[Definition]:
    """Top-level and method funcs in a Go file, by regex.

    The worked example of a second adapter. Go's convention differs from the
    brace-language default: the doc comment is `//` lines immediately above the
    declaration, conventionally starting with the identifier's own name. Any
    `//` line is accepted — enforcing GoDoc phrasing is a different job.

    In: repo root and a repo-relative path. Out: one Definition per func.
    """
    full = root / rel_path
    if not full.is_file():
        return []
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()

    out: list[Definition] = []
    for i, text in enumerate(lines):
        match = GO_FUNC.match(text)
        if not match:
            continue
        has_doc = False
        for j in range(i - 1, -1, -1):
            prev = lines[j].strip()
            if not prev:
                continue
            has_doc = prev.startswith("//")
            break
        out.append(Definition(rel_path, i + 1, 0, match.group(1), "func", has_doc))

    for k, definition in enumerate(out):
        definition.end_line = (out[k + 1].line - 1) if k + 1 < len(out) else len(lines)
    return out


SWIFT_MODIFIERS = (
    r"(?:(?:public|private|fileprivate|internal|open|static|class|final|override|"
    r"required|convenience|mutating|nonmutating|nonisolated|dynamic|lazy|weak|"
    r"unowned|indirect|@objc(?:\([^)]*\))?|@IBAction|@IBOutlet|@discardableResult|"
    r"@MainActor|@Sendable|@inlinable|@available\([^)]*\))\s+)*"
)
SWIFT_FUNC = re.compile(r"^\s*" + SWIFT_MODIFIERS + r"func\s+([A-Za-z_]\w*|`\w+`)\s*[<(]")
SWIFT_INIT = re.compile(r"^\s*" + SWIFT_MODIFIERS + r"(init)[?!]?\s*\(")
SWIFT_TYPE = re.compile(
    r"^\s*" + SWIFT_MODIFIERS
    + r"(?:class|struct|enum|extension|protocol|actor)\s+([A-Za-z_][\w.]*)"
)
SWIFT_ATTR_LINE = re.compile(r"^\s*@\w+(?:\([^)]*\))?\s*$")


def swift_defs(root: Path, rel_path: str) -> list[Definition]:
    """Functions, initialisers and type declarations in a Swift file, by regex.

    Why regex: no Swift parser is available without a toolchain, and this
    checker must run where there is none (Linux CI, a WSL box). False
    positives are preferred to silence, per the adapter contract.

    Documented == the nearest preceding non-blank, non-attribute line starts
    with `///` (Swift's native doc comment) or ends with `*/`. Attribute lines
    on their own (`@objc`, `@IBAction`) sit between the doc block and the
    declaration and are walked past, like C# attributes.

    Reports: `func` (kind 'func'), `init` ('method'), `class`/`struct`/`enum`/
    `extension`/`protocol`/`actor` ('class'). A `class func` is a func: the
    modifier regex eats the leading `class`. Not reported: computed
    properties, `subscript`, `deinit`, closures. Nested funcs inside bodies
    ARE reported — the regex cannot see scope — which is the accepted false
    positive. The repo's licence header is a `/* */` block followed by
    `import` lines, so it never counts as a definition's doc.

    In: repo root and a repo-relative path. Out: one Definition per match;
    empty list when the file is missing. `end_line` approximated as "until the
    next definition starts".
    """
    full = root / rel_path
    if not full.is_file():
        return []
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()

    out: list[Definition] = []
    for i, text in enumerate(lines):
        stripped = text.lstrip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        name = kind = None
        m = SWIFT_FUNC.match(text)
        if m:
            name, kind = m.group(1).strip("`"), "func"
        else:
            m = SWIFT_INIT.match(text)
            if m:
                name, kind = "init", "method"
            else:
                m = SWIFT_TYPE.match(text)
                if m:
                    name, kind = m.group(1), "class"
        if name is None:
            continue

        has_doc = False
        for j in range(i - 1, -1, -1):
            prev = lines[j].strip()
            if not prev or SWIFT_ATTR_LINE.match(prev):
                continue
            has_doc = prev.startswith("///") or prev.endswith("*/")
            break

        out.append(Definition(rel_path, i + 1, 0, name, kind, has_doc))

    for k, definition in enumerate(out):
        definition.end_line = (out[k + 1].line - 1) if k + 1 < len(out) else len(lines)
    return out


KOTLIN_MODIFIERS = (
    r"(?:(?:public|internal|private|protected|override|open|suspend|inline|infix|"
    r"operator|tailrec|external|abstract|final|actual|expect|inner|data|sealed|enum|"
    r"value|annotation|fun|companion|@\w+(?:\([^)]*\))?)\s+)*"
)
KOTLIN_NAME = r"([A-Za-z_]\w*|`[^`]+`)"
# `fun <T> Receiver<T>.name(` — type parameters first, then an optional receiver
# (dotted, possibly generic, possibly nullable), then the name. The receiver group
# is lazy-optional (`??`) so `fun name(` is taken as a name, not as a receiver.
KOTLIN_FUNC = re.compile(
    r"^\s*" + KOTLIN_MODIFIERS + r"fun\s+(?:<[^>]*>\s*)?(?:.*?\.)??" + KOTLIN_NAME + r"\s*\("
)
KOTLIN_CTOR = re.compile(r"^\s*" + KOTLIN_MODIFIERS + r"(constructor)\s*\(")
KOTLIN_COMPANION = re.compile(
    r"^\s*" + KOTLIN_MODIFIERS + r"companion\s+object\b(?:\s+([A-Za-z_]\w*))?"
)
KOTLIN_TYPE = re.compile(
    r"^\s*" + KOTLIN_MODIFIERS + r"(?:class|interface|object)\s+" + KOTLIN_NAME
)
KOTLIN_ANNOTATION_LINE = re.compile(r"^\s*@\w+(?:\([^)]*\))?\s*$")
KOTLIN_TEST_ANNOTATION = re.compile(r"(^|\s)@Test\b")
# Expression body on one line: `fun hex(c: Int): String = "%04X".format(c)`. The
# `=` must follow the parameter list's closing paren (and optional return type),
# so a default argument `(a: Int = 3) {` does not qualify.
KOTLIN_EXPR_BODY = re.compile(r"\)\s*(?::\s*[^={]+?)?\s*=\s*[^\s{]")
# A declaration that is complete on its line: no body opened, no primary
# constructor or supertype list left open for the next line.
KOTLIN_ONE_LINE_TYPE = re.compile(r"[^{(,:]\s*(?://.*)?$")


def kotlin_defs(root: Path, rel_path: str) -> list[Definition]:
    """Functions, secondary constructors and type declarations in a Kotlin
    file, by regex.

    Why regex: a Kotlin parser needs a JVM, and this checker must run where
    there is none. Same trade as Swift — false positives are preferred to
    silence.

    Documented == the nearest preceding non-blank, non-annotation line ends
    with `*/`. Kotlin has no `///`; only a KDoc `/** */` block counts, and a
    bare `//` line above a `fun` is NOT a doc. Annotation lines standing alone
    (`@Composable`, `@Volatile`, `@Suppress(...)`) sit between the KDoc and the
    declaration and are walked past. A multi-line annotation (`@Suppress(` ...
    `)`) is not recognised, so the declaration under it reads as undocumented —
    the accepted false positive.

    Reports: `fun` (kind 'func', named after the backticks are stripped and any
    extension receiver dropped: `fun RSEDevice.getFile(` is `getFile`),
    `constructor(` ('method'), `class`/`interface`/`object` with any of
    `data`/`sealed`/`enum`/`value`/`annotation`/`fun`/`inner` in front ('class'),
    and `companion object` ('class', named `companion` when anonymous).

    Not reported: properties (`val`/`var`, including ones with a `get()`),
    `init {}` blocks (they document at the class), enum constants, lambdas,
    `typealias`. Nested funs inside bodies ARE reported — the regex cannot see
    scope. Three exemptions, all dropped from the list entirely:
      - a fun carrying `@Test` (its name is its documentation; the path rule
        the Swift audit uses does not work here because JUnit tests live in
        `src/test/`, which the enforced list can still name);
      - a single-expression body on one line (`fun hex(c: Int): String = ...`),
        the same trade the JS adapter makes for one-line arrows;
      - a one-line, body-less type declaration that is INDENTED, i.e. nested in
        another type: `    data object ButtonShortPress : EventCode(1, "...")`.
        Those are the members of a sealed hierarchy — Kotlin's enum cases — and
        document at the parent, exactly as Swift `case` lines are invisible to
        swift_defs. Indentation is the proxy for "nested" because the regex
        cannot see scope; a top-level one-liner (`data class Point(val x: Int)`)
        is still reported, because a top-level type is API.

    In: repo root and a repo-relative path. Out: one Definition per match;
    empty list when the file is missing. `end_line` approximated as "until the
    next definition starts".
    """
    full = root / rel_path
    if not full.is_file():
        return []
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()

    out: list[Definition] = []
    for i, text in enumerate(lines):
        stripped = text.lstrip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        name = kind = None
        m = KOTLIN_FUNC.match(text)
        if m:
            name, kind = m.group(1).strip("`"), "func"
            if KOTLIN_EXPR_BODY.search(text) and not text.rstrip().endswith("{"):
                continue
        else:
            m = KOTLIN_CTOR.match(text)
            if m:
                name, kind = "constructor", "method"
            else:
                m = KOTLIN_COMPANION.match(text)
                if m:
                    name, kind = (m.group(1) or "companion"), "class"
                else:
                    m = KOTLIN_TYPE.match(text)
                    if m:
                        name, kind = m.group(1).strip("`"), "class"
                        if text[0] in " \t" and KOTLIN_ONE_LINE_TYPE.search(text):
                            continue
        if name is None:
            continue

        # Walk back over blank lines and standalone annotations to the doc
        # block, noting on the way whether any annotation was @Test.
        is_test = kind == "func" and bool(KOTLIN_TEST_ANNOTATION.search(text))
        has_doc = False
        for j in range(i - 1, -1, -1):
            prev = lines[j].strip()
            if not prev:
                continue
            if KOTLIN_ANNOTATION_LINE.match(prev):
                if kind == "func" and KOTLIN_TEST_ANNOTATION.search(prev):
                    is_test = True
                continue
            has_doc = prev.endswith("*/")
            break
        if is_test:
            continue

        out.append(Definition(rel_path, i + 1, 0, name, kind, has_doc))

    for k, definition in enumerate(out):
        definition.end_line = (out[k + 1].line - 1) if k + 1 < len(out) else len(lines)
    return out


TS_NAME = r"(?P<name>[A-Za-z_$][\w$]*)"
# One level of nested angle brackets: `<T extends Record<string, X>>`.
TS_GENERICS = r"(?:<(?:[^<>]|<[^<>]*>)*>)?"
# `: SomeType` between a name and its `=`, where the type itself may contain `=>`
# (`const f: (n: number) => number = ...`). `=(?!>)` is the assignment.
TS_ANNOTATION = r"(?::(?:[^=]|=>)*)?=(?!>)"
TS_DECL_PREFIX = r"^\s*(?:export\s+)?(?:default\s+)?(?P<declare>declare\s+)?"
TS_FUNCTION = re.compile(
    TS_DECL_PREFIX + r"(?:async\s+)?function\s*\*?\s*" + TS_NAME + r"\s*" + TS_GENERICS + r"\s*\("
)
# `const f = <T,>(x: T): T =>`, `let g: Fn = async (...) =>`, `const h = function name(`,
# `const k = x =>`. The paren form is confirmed as an arrow/body by its tail —
# `const n = (a + b) * 2` matches here and is dropped there.
TS_ARROW = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+" + TS_NAME + r"\s*" + TS_ANNOTATION
    + r"\s*(?:async\s+)?(?:(?P<funcexpr>function\b[^(]*)?" + TS_GENERICS + r"\s*\(|"
    + r"[A-Za-z_$][\w$]*\s*=>)"
)
TS_TYPE_DECL = re.compile(
    TS_DECL_PREFIX + r"(?:abstract\s+)?(?:const\s+)?(?P<kw>class|interface|enum|namespace)\s+"
    + TS_NAME
)
TS_TYPE_ALIAS = re.compile(TS_DECL_PREFIX + r"type\s+" + TS_NAME + r"\s*" + TS_GENERICS + r"\s*=")
# A type alias whose right-hand side continues on the next line: ends with `{`,
# `=`, `|`, `&`, `(`, `<` or `,`. Anything else is complete on its line.
TS_TYPE_OPEN = re.compile(r"[{=|&(<,]\s*$")
TS_MEMBER_MODS = (
    r"(?:(?:public|private|protected|static|readonly|abstract|override|async|get|set|"
    r"declare|accessor)\s+|@[\w.]+(?:\([^()]*\))?\s+)*"
)
TS_NOT_A_METHOD = (
    r"(?!(?:if|for|while|switch|catch|return|function|typeof|await|new|throw|yield|delete|"
    r"void|else|do|import|export|super|this|case|const|let|var|class|interface|type|enum)\b)"
)
# `  public async name<T>(` — no whitespace before the `(`: a JSX text line such as
# `Reads ({n}) {` has one, and a method never does in formatted code.
TS_METHOD = re.compile(r"^\s+" + TS_MEMBER_MODS + TS_NOT_A_METHOD + TS_NAME + TS_GENERICS + r"\(")
# `  protected handler = (e: Event): void =>` — a class field holding an arrow.
TS_FIELD_ARROW = re.compile(
    r"^\s+" + TS_MEMBER_MODS + TS_NOT_A_METHOD + TS_NAME + r"\s*" + TS_ANNOTATION
    + r"\s*(?:async\s+)?" + TS_GENERICS + r"\s*\("
)
# What follows a parameter list. Body: optional return type then `{` ending the
# line (or an empty `{}`). Signature: optional return type then `;`/`,`/nothing.
# Arrow: optional return type then `=>`. Anything else (`.then(`, `* 2;`) is a
# call or an expression, not a definition.
TS_BODY_TAIL = re.compile(r"^\s*(?::[^{;]*)?\{\s*\}?\s*$")
TS_SIGNATURE_TAIL = re.compile(r"^\s*(?::[^{;]*)?[;,]?\s*$")
TS_ARROW_TAIL = re.compile(r"^\s*(?::[^{;=]*)?=>")
TS_TRIVIAL_TAIL = re.compile(r"^\s*(?::[^{;=]*)?=>\s*[^\s{]")
TS_LINE_CONTINUES = re.compile(r"[({\[,>|&?:+\-*/=`]\s*$")
TS_DECORATOR_LINE = re.compile(r"^@[\w.]+(?:\([^()]*(?:\([^()]*\)[^()]*)*\))?$")
TS_PRAGMA_LINE = re.compile(r"^//\s*(?:eslint-|@ts-|prettier-|biome-)")
TS_EXEMPT_FILE = re.compile(r"\.d\.ts$|\.(?:test|spec)\.tsx?$|(?:^|/)__tests__/")


def ts_params_tail(lines: list[str], i: int, col: int) -> str | None:
    """The text after the parameter list that opens at lines[i][col].

    Why: TypeScript signatures wrap — `async foo(\\n  a: A,\\n): Promise<B> {` —
    and what classifies a match as body, signature or arrow is whatever
    follows the closing paren, wherever that lands. Counts parens only; a
    paren inside a string default is the accepted confusion.

    In: the file's lines, the line and column of the opening `(`. Out: the rest
    of the line holding the matching `)`, or None when it does not close within
    forty lines (an unbalanced file, or not a definition at all).
    """
    depth = 0
    for j in range(i, min(i + 40, len(lines))):
        text = lines[j][col:] if j == i else lines[j]
        for k, ch in enumerate(text):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[k + 1:]
    return None


def typescript_defs(root: Path, rel_path: str) -> list[Definition]:
    """Functions, arrows, classes, members and type declarations in a .ts/.tsx
    file, by regex. A separate adapter, not javascript_defs widened: TypeScript
    adds five declaration kinds and a signature/implementation split that the
    three JS regexes have no place for, and JS behaviour must not move under
    adopters when TS learns something.

    Why regex: parsing TypeScript needs the TypeScript compiler, an npm
    dependency this checker refuses. Same trade as Swift and Kotlin — false
    positives are preferred to silence.

    Documented == a `/** */` block whose closing line is DIRECTLY above the
    declaration, walking past standalone single-line decorators (`@Log('x')`),
    lint pragmas (`// eslint-disable-next-line ...`, `// @ts-expect-error`) and
    single-line overload signatures of the same name. A blank line breaks the
    link: a `/** */` block followed by a blank and then a function is a file or
    section comment, not that function's contract. `//` lines and `/* */`
    blocks are not docs. A multi-line decorator (`@Component({` ... `})`) is
    not recognised, so the declaration under it reads as undocumented — the
    accepted false positive, as for Kotlin's multi-line annotations.

    Reports ('func'): `function` declarations, generic or async, exported or
    not, including `export default function`; `const`/`let` arrows and
    function expressions, with or without a type annotation on the binding.
    ('class'): `class`, `abstract class`, `interface`, `enum`, `const enum`,
    `namespace`, and `type` aliases whose right-hand side continues past the
    line. ('method'): class methods with any of `public/private/protected/
    static/readonly/abstract/override/async/get/set` in front, `constructor`,
    accessors, class fields holding an arrow, and `abstract` signatures. An
    overload group is ONE definition, located at its implementation, and the
    block may sit above the first signature — that is where TSDoc reads it.

    Exempt, dropped from the list entirely:
      - the whole file when it is a `.d.ts` (ambient, nothing to document at
        this level), a `*.test.ts(x)`/`*.spec.ts(x)`, or lives under
        `__tests__/` — the test name is the documentation;
      - `describe(`/`it(`/`test(` callbacks anywhere, which never match a
        definition shape;
      - `declare` declarations (ambient) and bare overload/interface/type-
        literal signatures — a method-shaped line ending in `;` is a signature,
        never counted unless it is `abstract`;
      - a single-expression arrow complete on one line
        (`const toRad = (d: number): number => d * Math.PI / 180;`), the JS
        rule; an arrow whose line ends `=> (` is NOT exempt, its body is below;
      - a `type` alias complete on one line (`type Tab = 'a' | 'b';`), the same
        trade for the same reason;
      - INDENTED `function` declarations and `const`/`let` arrows — closures,
        which document at the parent exactly as python_defs treats nested defs.
        Indentation is the proxy for nesting because the regex cannot see
        scope; a `namespace` body's functions are the price.
      - properties without an arrow, interface and type-literal members, enum
        constants, one-line method bodies (`toString() { return this.x; }`) and
        definitions wrapped in a call (`React.memo(function X`, `useCallback(`).

    Syntax gate: none by default. `node --check` cannot read TypeScript.
    `node --experimental-strip-types --check` (Node 22.6+) does, but rejects
    `enum`, `namespace` and constructor parameter properties as syntax errors
    and cannot read `.tsx` at all; `tsc --noEmit` needs a project and a
    dependency. So `ts_syntax_gate` is opt-in in docs-toolkit.json and covers
    `.ts` only — see check_coverage.

    In: repo root and a repo-relative path. Out: one Definition per match;
    empty list when the file is missing or exempt. `end_line` approximated as
    "until the next definition starts".
    """
    if TS_EXEMPT_FILE.search(rel_path):
        return []
    full = root / rel_path
    if not full.is_file():
        return []
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()

    out: list[Definition] = []
    for i, text in enumerate(lines):
        stripped = text.lstrip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        indented = text[:1] in (" ", "\t")
        name = kind = None

        m = TS_ARROW.match(text)
        if m:
            if indented:
                continue
            name, kind = m.group("name"), "func"
            if m.group(0).endswith("=>"):
                tail = text[m.end() - 2:]
            else:
                tail = ts_params_tail(lines, i, m.end() - 1)
                if tail is None:
                    continue
                if m.group("funcexpr"):
                    if not TS_BODY_TAIL.match(tail):
                        continue
                elif not TS_ARROW_TAIL.match(tail):
                    continue
            if TS_TRIVIAL_TAIL.match(tail) and not TS_LINE_CONTINUES.search(text):
                continue
        else:
            m = TS_FUNCTION.match(text)
            if m:
                if indented or m.group("declare"):
                    continue
                name, kind = m.group("name"), "func"
                tail = ts_params_tail(lines, i, m.end() - 1)
                if tail is None or "{" not in tail:
                    continue
            else:
                m = TS_TYPE_DECL.match(text)
                if m:
                    if m.group("declare"):
                        continue
                    name, kind = m.group("name"), "class"
                else:
                    m = TS_TYPE_ALIAS.match(text)
                    if m:
                        if m.group("declare") or not TS_TYPE_OPEN.search(text):
                            continue
                        name, kind = m.group("name"), "class"
                    else:
                        m = TS_METHOD.match(text)
                        if m:
                            name, kind = m.group("name"), "method"
                            tail = ts_params_tail(lines, i, m.end() - 1)
                            if tail is None:
                                continue
                            if TS_SIGNATURE_TAIL.match(tail):
                                if not re.search(r"\babstract\s", text[:m.start("name")]):
                                    continue
                            elif not TS_BODY_TAIL.match(tail):
                                continue
                        else:
                            m = TS_FIELD_ARROW.match(text)
                            if m:
                                name, kind = m.group("name"), "method"
                                tail = ts_params_tail(lines, i, m.end() - 1)
                                if tail is None or not TS_ARROW_TAIL.match(tail):
                                    continue
                                if TS_TRIVIAL_TAIL.match(tail) and not TS_LINE_CONTINUES.search(text):
                                    continue
        if name is None:
            continue

        # Walk back over standalone decorators, lint pragmas and same-name
        # overload signatures to the line that must close the doc block.
        same_name_signature = re.compile(r"\b" + re.escape(name) + r"\s*[<(].*;\s*$")
        j = i - 1
        while j >= 0:
            prev = lines[j].strip()
            if TS_DECORATOR_LINE.match(prev) or TS_PRAGMA_LINE.match(prev):
                j -= 1
                continue
            if same_name_signature.search(prev) and not prev.endswith("*/"):
                j -= 1
                continue
            break
        has_doc = False
        if j >= 0 and lines[j].rstrip().endswith("*/"):
            k = j
            while k >= 0 and "/*" not in lines[k]:
                k -= 1
            has_doc = k >= 0 and lines[k].lstrip().startswith("/**")

        out.append(Definition(rel_path, i + 1, 0, name, kind, has_doc))

    for k, definition in enumerate(out):
        definition.end_line = (out[k + 1].line - 1) if k + 1 < len(out) else len(lines)
    return out


RUST_VIS = r"(?:pub(?:\s*\([^)]*\))?\s+)?"
RUST_FN = re.compile(
    r"^\s*" + RUST_VIS
    + r"(?:(?:default|const|async|unsafe|safe|extern(?:\s+\"[^\"]*\")?)\s+)*"
    + r"fn\s+(?:r#)?([A-Za-z_]\w*)\s*[<(]"
)
RUST_TYPE = re.compile(
    r"^\s*" + RUST_VIS + r"(?:unsafe\s+)?(?:auto\s+)?(?:struct|enum|trait|union)\s+"
    r"(?:r#)?([A-Za-z_]\w*)"
)
RUST_MACRO = re.compile(r"^\s*macro_rules!\s*([A-Za-z_]\w*)")
# `impl<T> Trait for Type<T>` — `for` followed by a type (or a `$name` metavariable
# inside `macro_rules!`), not the `for<'a>` of a higher-ranked bound.
RUST_IMPL_FOR = re.compile(r"^\s*(?:unsafe\s+)?impl\b.*\bfor\s+[A-Za-z_&(\[:$]")
RUST_MOD_OPEN = re.compile(r"^\s*" + RUST_VIS + r"mod\s+\w+\s*\{")
RUST_CFG_TEST = re.compile(r"^#\[cfg\(test\)\]")
# #[test], #[tokio::test], #[rstest], #[test_case(..)] — any attribute path
# whose last segment contains `test`. `cfg_attr(test, ..)` does not match.
RUST_TEST_ATTR = re.compile(r"^#\[(?:\w+::)*\w*test\w*\s*(?:[(\]])")
# An attribute is complete when its line ends in `]`, a trailing comment allowed:
# `#[allow(clippy::needless_range_loop)] // reason`.
RUST_ATTR_END = re.compile(r"\]\s*(?://.*)?$")
# A body that opens and closes on the declaration's own line: `fn x() -> u8 { 3 }`.
RUST_ONE_LINE_BODY = re.compile(r"\{.*\}\s*$")


def rust_defs(root: Path, rel_path: str) -> list[Definition]:
    """Functions, types and `macro_rules!` in a Rust file, by regex and indentation.

    Why regex: a Rust parser means a toolchain or a crate, and this checker must
    run where there is none. Why indentation: the two scopes that matter (test
    modules and trait impls) close on a `}` at the indentation they opened at,
    which holds for rustfmt'd code. `[INFERRED]` code that is not rustfmt'd can
    leave a scope open and silence the rest of the file; the fixture pins the
    rustfmt shape only.

    Documented == a `///` line or a `/** */` block above the item, with only blank
    lines and outer attributes (`#[..]`, also spread over several lines) in between.
    `//!` and `/*! */` are inner docs of the enclosing module and do not count; a plain
    `//` or `////` line between doc and item breaks the link, as it does in the
    other adapters.

    Reports: `fn` with any visibility and modifiers (`pub(crate) const unsafe extern
    "C" fn`, raw `r#` names, generics) as 'func'; `struct`, `enum`, `trait`, `union` as
    'class'; `macro_rules!` as 'func'. Trait method signatures without a body are
    reported: the trait is where they are documented.

    Exempt, dropped from the list entirely:
      - everything inside a `#[cfg(test)] mod … {`, and any item carrying
        `#[cfg(test)]` or a test attribute (`#[test]`, `#[tokio::test]`, …);
      - fns directly inside `impl Trait for Type`: rustdoc shows the trait's doc,
        and `missing_docs` does not ask for them either;
      - a fn whose body opens and closes on its own line (`fn x() -> u8 { 3 }`,
        `fn noop() {}`), the trade the Kotlin and JS adapters make for one-liners.
    Not reported: `impl` blocks, `mod`, `const`, `static`, `type` aliases, fields,
    variants, closures. A fn nested inside another fn's body IS reported, because
    the regex cannot tell it from a method.

    In: repo root and a repo-relative path. Out: one Definition per item; empty list
    when the file is missing. `end_line` approximated as "until the next
    definition starts".
    """
    full = root / rel_path
    if not full.is_file():
        return []
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()

    out: list[Definition] = []
    # Open scopes, innermost last: (indentation the scope opened at, "test" | "impl_for").
    scopes: list[tuple[str, str]] = []
    has_doc = False
    attrs: list[str] = []
    in_block_comment = False
    block_is_doc = False
    attr_open = False

    for i, text in enumerate(lines):
        stripped = text.strip()
        indent = text[:len(text) - len(text.lstrip())]

        if scopes and indent == scopes[-1][0] and stripped in ("}", "};"):
            scopes.pop()
            has_doc = False
            attrs = []
            continue
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
                has_doc = block_is_doc
            continue
        if attr_open:
            attrs[-1] += " " + stripped
            if RUST_ATTR_END.search(stripped):
                attr_open = False
            continue
        if not stripped:
            continue
        if stripped.startswith("///") and not stripped.startswith("////"):
            has_doc = True
            continue
        if stripped.startswith("/*"):
            block_is_doc = stripped.startswith("/**") and not stripped.startswith(("/***", "/**/"))
            if "*/" in stripped[2:]:
                has_doc = block_is_doc
            else:
                in_block_comment = True
            continue
        if stripped.startswith("//"):
            has_doc = False
            continue
        if stripped.startswith("#["):
            attrs.append(stripped)
            if not RUST_ATTR_END.search(stripped):
                attr_open = True
            continue

        # A code line. Everything accumulated above it applies to it alone.
        in_test = any(kind == "test" for _, kind in scopes)
        in_impl_for = bool(scopes) and scopes[-1][1] == "impl_for"
        is_cfg_test = any(RUST_CFG_TEST.match(a) for a in attrs)
        is_test_fn = any(RUST_TEST_ATTR.match(a) for a in attrs)
        documented = has_doc
        has_doc = False
        attrs = []

        # `impl Eq for Foo {}` and `mod x {}` close on their own line: no scope.
        opens_scope = not stripped.endswith(("}", ";"))
        if RUST_MOD_OPEN.match(text):
            if (in_test or is_cfg_test) and opens_scope:
                scopes.append((indent, "test"))
            continue
        if RUST_IMPL_FOR.match(text):
            if opens_scope:
                scopes.append((indent, "impl_for"))
            continue
        if in_test or is_cfg_test:
            continue

        name = kind = None
        m = RUST_FN.match(text)
        if m:
            if in_impl_for or is_test_fn or RUST_ONE_LINE_BODY.search(text[m.end():]):
                continue
            name, kind = m.group(1), "func"
        else:
            m = RUST_TYPE.match(text)
            if m:
                name, kind = m.group(1), "class"
            else:
                m = RUST_MACRO.match(text)
                if m:
                    name, kind = m.group(1), "func"
        if name is None:
            continue

        out.append(Definition(rel_path, i + 1, 0, name, kind, documented))

    for k, definition in enumerate(out):
        definition.end_line = (out[k + 1].line - 1) if k + 1 < len(out) else len(lines)
    return out


CS_MODIFIERS = (
    r"(?:(?:public|private|protected|internal|static|virtual|override|abstract|sealed|"
    r"async|extern|unsafe|new|partial|readonly|file|required)\s+)*"
)
CS_TYPE = re.compile(
    r"^\s*" + CS_MODIFIERS + r"(?:record\s+(?:class|struct)|class|struct|interface|enum|record)"
    r"\s+([A-Za-z_]\w*)"
)
# Return type (optional, so constructors match), then the name, optional generic
# parameters, then the opening parenthesis. The type allows generics, arrays,
# nullables, tuples and dotted names.
CS_METHOD = re.compile(
    r"^\s*" + CS_MODIFIERS
    + r"(?:[\w.]+(?:<[^()]*?>)?(?:\[[,\s]*\])*\??(?:\s*\([^()]*\))?\s+)?"
    + r"(~?[A-Za-z_]\w*)\s*(?:<[^()]*?>)?\s*\("
)
CS_STATEMENT = re.compile(
    r"^\s*(?:if|else|for|foreach|while|do|switch|case|catch|using|lock|return|throw|"
    r"await|yield|var|new|nameof|typeof|sizeof|default|checked|unchecked|fixed|"
    r"when|base|this|get|set|init|add|remove|goto|break|continue)\b"
)
CS_ATTR_LINE = re.compile(r"^\s*\[.*\]\s*$")
# Directory part case-insensitive (tests/, Tests/); file part case-sensitive so
# Contest.cs is not a test file but FooTests.cs is.
CS_TEST_FILE = re.compile(r"(?i:(^|/)tests?/)|Tests?\.cs$")


def csharp_defs(root: Path, rel_path: str) -> list[Definition]:
    """Methods, constructors and type declarations in a C# file, by regex.

    Why regex: Roslyn needs the .NET SDK, and this checker must run where there
    is none. Where the SDK is present, the build is the second gate: CS1591 as a
    warning-as-error per project catches a missing comment on public members,
    which this adapter backs up for internal and private ones.

    Documented == the nearest preceding line that is not blank, not an
    attribute line (`[RelayCommand]`), not a preprocessor line (`#pragma`) and
    not a `//` comment, starts with `///` or ends with `*/`. A multi-line
    attribute is not recognised, so the declaration under it reads as
    undocumented: the accepted false positive.

    Reports: types (`class`, `struct`, `interface`, `enum`, `record`, kind
    'class') and methods, constructors and interface members (kind 'method').
    Exempt, by design: one-line expression-bodied members (`=> ...;` on the
    declaration line), local functions (indented deeper than their enclosing
    member, documented at the parent), properties, fields, events, indexers and
    operators.

    In: repo root and a repo-relative path. Out: one Definition per declaration.
    """
    full = root / rel_path
    if not full.is_file():
        return []
    lines = full.read_text(encoding="utf-8-sig", errors="replace").splitlines()

    out: list[Definition] = []
    member_indent: int | None = None
    in_member_body = False
    for i, text in enumerate(lines):
        stripped = text.strip()
        if not stripped or stripped.startswith(("//", "#", "*", "/*")):
            continue
        indent = len(text) - len(text.lstrip())
        # Trailing `// note` would hide the `;` that marks a one-liner or a bodiless
        # member. Naive: a `//` inside a string literal cuts too early, which only
        # turns an exempt one-liner into a reported one.
        code = stripped.split("//", 1)[0].rstrip()

        type_match = CS_TYPE.match(text)
        if type_match:
            name, kind = type_match.group(1), "class"
            member_indent = None
        else:
            if CS_STATEMENT.match(text):
                continue
            method_match = CS_METHOD.match(text)
            if not method_match:
                continue
            head = text[: method_match.end()]
            if "=" in head.replace("=>", "") or "." in method_match.group(1):
                continue
            # A local function sits deeper than the member that holds it.
            if member_indent is not None and indent > member_indent and in_member_body:
                continue
            if "=>" in code and code.endswith(";"):
                continue  # one-line expression body: exempt
            name, kind = method_match.group(1).lstrip("~"), "method"
            member_indent = indent
            in_member_body = not code.endswith(";")

        documented = False
        for j in range(i - 1, -1, -1):
            prev = lines[j].strip()
            if not prev or CS_ATTR_LINE.match(prev) or prev.startswith("#"):
                continue
            if prev.startswith("//") and not prev.startswith("///"):
                continue
            documented = prev.startswith("///") or prev.endswith("*/")
            break

        out.append(Definition(rel_path, i + 1, 0, name, kind, documented))

    for k, definition in enumerate(out):
        definition.end_line = (out[k + 1].line - 1) if k + 1 < len(out) else len(lines)
    return out


LANG_BY_SUFFIX = {".py": "python", ".js": "javascript", ".go": "go", ".swift": "swift",
                  ".kt": "kotlin", ".ts": "typescript", ".tsx": "typescript", ".rs": "rust",
                  ".cs": "csharp"}
ADAPTERS = {"python": python_defs, "javascript": javascript_defs, "go": go_defs,
            "swift": swift_defs, "kotlin": kotlin_defs, "typescript": typescript_defs,
            "rust": rust_defs, "csharp": csharp_defs}


def defs_for(root: Path, rel_path: str) -> list[Definition]:
    """Dispatch to the right adapter by file extension.

    In: repo root and a repo-relative path. Out: definitions, or [] for a
    language with no adapter — an unknown extension is not an error, it is
    simply out of scope.
    """
    lang = LANG_BY_SUFFIX.get(Path(rel_path).suffix)
    if lang is None:
        return []
    return ADAPTERS[lang](root, rel_path)


# --- Checks 1 + 2: documentation coverage -------------------------------------

def check_coverage(root: Path, cfg: dict, rep: Reporter) -> None:
    """Docstrings and doc blocks on the enforced files, plus a syntax gate.

    In: repo root, config, reporter. Out: nothing; reports findings.

    Prints the "enforced list is empty" hint rather than a misleading clean
    result when nothing is configured — a green tick on zero files is the single
    most confusing thing this tool could say.
    """
    rep.section("Checks 1-2: function documentation (enforced files)")

    enforced = cfg["enforced"]
    py_files = list(enforced.get("python") or [])
    js_files = list(enforced.get("javascript") or [])
    go_files = list(enforced.get("go") or [])
    swift_files = list(enforced.get("swift") or [])
    kt_files = list(enforced.get("kotlin") or [])
    ts_files = list(enforced.get("typescript") or [])
    rs_files = list(enforced.get("rust") or [])
    cs_files = list(enforced.get("csharp") or [])

    if not (py_files or js_files or go_files or swift_files or kt_files or ts_files
            or rs_files or cs_files):
        rep.line("  (enforced list is empty — nothing checked strictly yet.)")
        rep.line("  Run with --audit for a coverage report across the whole repo.")
        return

    before = rep.findings

    for rel in py_files:
        if not (root / rel).is_file():
            continue
        for d in python_defs(root, rel):
            if d.kind == "ERROR":
                rep.finding(f"{d.file}: parse error")
                continue
            if d.name.startswith("test_"):
                continue
            if not d.has_doc:
                rep.finding(f"{d.file}:{d.line}  {d.name}  — no docstring")

    for rel in js_files:
        for d in javascript_defs(root, rel):
            if not d.has_doc:
                rep.finding(f"{d.file}:{d.line}  {d.name}  — no /** */ block")

    for rel in go_files:
        for d in go_defs(root, rel):
            if not d.has_doc:
                rep.finding(f"{d.file}:{d.line}  {d.name}  — no // doc comment")

    for rel in swift_files:
        for d in swift_defs(root, rel):
            if not d.has_doc:
                rep.finding(f"{d.file}:{d.line}  {d.name}  — no /// doc comment")

    for rel in kt_files:
        for d in kotlin_defs(root, rel):
            if not d.has_doc:
                rep.finding(f"{d.file}:{d.line}  {d.name}  — no /** */ KDoc")

    for rel in ts_files:
        for d in typescript_defs(root, rel):
            if not d.has_doc:
                rep.finding(f"{d.file}:{d.line}  {d.name}  — no /** */ TSDoc")

    for rel in rs_files:
        for d in rust_defs(root, rel):
            if not d.has_doc:
                rep.finding(f"{d.file}:{d.line}  {d.name}  — no /// rustdoc")

    for rel in cs_files:
        for d in csharp_defs(root, rel):
            if not d.has_doc:
                rep.finding(f"{d.file}:{d.line}  {d.name}  — no /// XML doc comment")

    # Syntax gate. `node --check` needs no packages and no package.json. A
    # documentation sweep's realistic failure is an unbalanced /** */, which IS
    # a syntax error. Skipped silently when node is absent.
    if js_files and cfg.get("js_syntax_gate", True) and shutil.which("node"):
        for rel in js_files:
            full = root / rel
            if not full.is_file():
                continue
            proc = subprocess.run(["node", "--check", str(full)],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                err = " ".join((proc.stderr or proc.stdout or "").split())
                rep.finding(f"{rel} — SYNTAX ERROR: {err}")

    # TypeScript gate, opt-in (`ts_syntax_gate`). Node 22.6+ strips types
    # before --check, which catches the unbalanced block — but it also refuses
    # `enum`, `namespace` and constructor parameter properties, and knows no
    # .tsx, so it is off by default and .tsx is never sent. Skipped silently
    # when node is absent or too old for the flag.
    ts_gate_files = [rel for rel in ts_files if rel.endswith(".ts")
                     and not TS_EXEMPT_FILE.search(rel) and (root / rel).is_file()]
    if ts_gate_files and cfg.get("ts_syntax_gate", False) and shutil.which("node"):
        probe = subprocess.run(["node", "--experimental-strip-types", "-e", "0"],
                               capture_output=True, text=True)
        for rel in ts_gate_files if probe.returncode == 0 else []:
            proc = subprocess.run(["node", "--experimental-strip-types", "--check",
                                   str(root / rel)], capture_output=True, text=True)
            if proc.returncode != 0:
                err = " ".join((proc.stderr or proc.stdout or "").split())
                rep.finding(f"{rel} — SYNTAX ERROR: {err}")

    if rep.findings == before:
        rep.line("  All enforced files fully documented.")


# --- Audit --------------------------------------------------------------------

def show_audit(root: Path, cfg: dict, rep: Reporter) -> None:
    """Coverage across every source file, enforced or not. Gates nothing.

    In: repo root, config, reporter. Out: nothing; prints a table.

    This is the measuring instrument, not a check — run it before starting a
    sweep and keep the number. Tests and package markers are excluded from the
    Python count because they are exempt by convention.
    """
    rep.section("Audit: documentation coverage, whole repo")
    skip = cfg["skip_dirs"]
    total_has = total_all = 0

    groups = (
        ("Python", ".py", python_defs,
         lambda r: not re.search(r"(^|/)tests?/", r) and not r.endswith("__init__.py")),
        ("JavaScript", ".js", javascript_defs, lambda r: True),
        ("Go", ".go", go_defs, lambda r: True),
        ("Swift", ".swift", swift_defs,
         lambda r: not re.search(r"(^|/)[Tt]ests?/", r) and not re.search(r"Tests\.swift$", r)),
        # JUnit convention: tests live under src/test/, whatever they are named.
        ("Kotlin", ".kt", kotlin_defs, lambda r: not re.search(r"(^|/)src/test/", r)),
        # .d.ts, *.test.ts(x), *.spec.ts(x) and __tests__/ are dropped by the
        # adapter itself, so they never make a row.
        ("TypeScript", (".ts", ".tsx"), typescript_defs, lambda r: True),
        # Cargo convention: integration tests, benches and examples are their own
        # documentation; unit tests are dropped by the adapter (#[cfg(test)]).
        ("Rust", ".rs", rust_defs,
         lambda r: not re.search(r"(^|/)(tests|benches|examples)/", r)
         and not re.search(r"(^|/)tests\.rs$", r)),
        # xUnit/NUnit convention: test projects live under tests/ or end in Tests.
        ("C#", ".cs", csharp_defs, lambda r: not CS_TEST_FILE.search(r)),
    )

    for label, suffix, adapter, keep in groups:
        suffixes = suffix if isinstance(suffix, tuple) else (suffix,)
        files = sorted(
            str(p.relative_to(root)).replace("\\", "/")
            for s in suffixes
            for p in root.rglob(f"*{s}")
            if p.is_file()
        )
        files = [f for f in files if not is_skipped(f, skip) and keep(f)]
        rows = []
        for rel in files:
            defs = [d for d in adapter(root, rel) if d.kind != "ERROR"]
            if not defs:
                continue
            has = sum(1 for d in defs if d.has_doc)
            total_has += has
            total_all += len(defs)
            pct = round(100 * has / len(defs))
            mark = "ok  " if pct == 100 else "    "
            rows.append(f"  {mark}{has:>4}/{len(defs):<4} {pct:>3}%  {rel}")
        if rows:
            rep.line("")
            rep.line(f"  {label}")
            for row in rows:
                rep.line(row)

    if total_all:
        rep.line("")
        rep.line(f"  Total: {total_has}/{total_all} ({round(100 * total_has / total_all)}%)")


# --- Diagram discovery --------------------------------------------------------

COVERS_RE = re.compile(r"^\s*%%\s*covers:\s*(.+)$")


def find_diagrams(root: Path, cfg: dict) -> list[Diagram]:
    """Every .mmd under a configured diagram dir, with its declared coverage.

    In: repo root, config. Out: Diagrams sorted by path, so output ordering is
    stable across platforms and between the two implementations.

    A `covers:` line is `path:fn1,fn2`. The path is split on the LAST colon so a
    Windows-style absolute path would not be mangled — repo-relative paths are
    expected, but being wrong here is silent.
    """
    out: list[Diagram] = []
    for dir_name in cfg.get("diagram_dirs", ["docs"]):
        for d in sorted(root.rglob(dir_name)):
            if not d.is_dir():
                continue
            rel_dir = str(d.relative_to(root)).replace("\\", "/")
            if is_skipped(rel_dir, cfg["skip_dirs"]):
                continue
            for f in sorted(d.glob("*.mmd")):
                rel = str(f.relative_to(root)).replace("\\", "/")
                diagram = Diagram(path=rel)
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    m = COVERS_RE.match(line)
                    if not m:
                        continue
                    spec = m.group(1).strip()
                    idx = spec.rfind(":")
                    if idx < 1:
                        continue
                    fns = [x.strip() for x in spec[idx + 1:].split(",") if x.strip()]
                    diagram.covers.append(Cover(spec[:idx].strip(), fns))
                out.append(diagram)
    return sorted(out, key=lambda x: x.path)


# --- Check 3: covers: integrity ----------------------------------------------

def check_covers(root: Path, diagrams: list[Diagram], rep: Reporter) -> None:
    """Every function named in a `covers:` header still exists.

    This is the check that actually bites. Rename a function and its diagram
    goes red on the next run, which is the ONLY automatic signal that a
    hand-drawn diagram has fallen behind the code.

    In: repo root, diagrams, reporter. Out: nothing; reports findings.
    """
    rep.section("Check 3: covers: headers point at code that exists")

    if not diagrams:
        rep.line("  (no .mmd files found under any docs/ directory.)")
        return

    before = rep.findings
    cache: dict[str, list[str]] = {}
    checked = 0

    for diagram in diagrams:
        if not diagram.covers:
            rep.finding(f"{diagram.path}  — no `%% covers:` header")
            continue
        for cover in diagram.covers:
            if not (root / cover.source_file).is_file():
                rep.finding(f"{diagram.path}  — covers missing file: {cover.source_file}")
                continue
            if cover.source_file not in cache:
                cache[cover.source_file] = [d.name for d in defs_for(root, cover.source_file)]
            known = cache[cover.source_file]
            for fn in cover.functions:
                checked += 1
                if fn not in known:
                    rep.finding(
                        f"{diagram.path}  — {cover.source_file} has no "
                        f"'{fn}' (renamed or removed?)"
                    )

    if rep.findings == before:
        rep.line(
            f"  {checked} function reference(s) across {len(diagrams)} "
            "diagram(s) all resolve."
        )


# --- Check 4: staleness -------------------------------------------------------

def git_lines(root: Path, args: list[str]) -> list[str]:
    """Run a git command in the repo, returning stdout lines. [] on failure.

    Swallows a git failure on purpose: a repo with no commits yet, or no git at
    all, should degrade to "nothing changed" rather than crashing a docs check.
    """
    try:
        proc = subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True)
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return [l for l in proc.stdout.splitlines() if l]


def changed_files(root: Path) -> list[str]:
    """Repo-relative paths changed vs HEAD, working tree and index together."""
    out = set(git_lines(root, ["diff", "--name-only", "HEAD"]))
    out |= set(git_lines(root, ["diff", "--name-only", "--cached"]))
    return sorted(out)


HUNK_RE = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def changed_ranges(root: Path, rel_path: str) -> list[tuple[int, int]]:
    """Changed line ranges for one file, from a zero-context diff.

    In: repo root, repo-relative path. Out: (from, to) inclusive pairs.

    A zero-length new-side hunk (a pure deletion) still points at a real line, so
    it is treated as one line — otherwise deletions are attributed nowhere and
    --deep silently misses them.
    """
    lines = git_lines(root, ["diff", "-U0", "HEAD", "--", rel_path])
    lines += git_lines(root, ["diff", "-U0", "--cached", "--", rel_path])
    ranges = []
    for line in lines:
        m = HUNK_RE.match(line)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) else 1
        if count == 0:
            count = 1
        ranges.append((start, start + count - 1))
    return ranges


def check_staleness(root: Path, diagrams: list[Diagram], rep: Reporter,
                    deep: bool) -> None:
    """Which diagrams the working diff has invalidated.

    In: repo root, diagrams, reporter, and deep. Out: nothing; reports findings.

    Without --deep a diagram is flagged when any file it covers changed. With
    --deep it is flagged only when a covered FUNCTION's line range overlaps a
    changed hunk — fewer false positives, more git calls.

    Remember what this can and cannot say: it names diagrams to REVIEW. Only a
    human can decide whether one is actually wrong.
    """
    mode = "changed functions" if deep else "changed files"
    rep.section(f"Check 4: diagrams to review ({mode})")

    changed = changed_files(root)
    if not changed:
        rep.line("  Working tree clean — nothing to review.")
        return

    hits: dict[str, set[str]] = {}
    for diagram in diagrams:
        for cover in diagram.covers:
            if cover.source_file not in changed:
                continue
            reason = cover.source_file
            if deep:
                ranges = changed_ranges(root, cover.source_file)
                if not ranges:
                    continue
                by_name = {d.name: d for d in defs_for(root, cover.source_file)}
                touched = [
                    fn for fn in cover.functions
                    if fn in by_name
                    and any(lo <= by_name[fn].end_line and hi >= by_name[fn].line
                            for lo, hi in ranges)
                ]
                if not touched:
                    continue
                reason = f"{cover.source_file} → {', '.join(touched)}"
            hits.setdefault(diagram.path, set()).add(reason)

    if not hits:
        rep.line("  No diagram covers anything in this diff.")
        if not deep:
            rep.line("  (Nothing changed under a covers: header.)")
        return

    for path in sorted(hits):
        rep.finding(path)
        for reason in sorted(hits[path]):
            rep.line(f"      {reason}")
    rep.line("")
    rep.line("  Update these in the same commit, or say in the commit message why not.")


# --- Render check -------------------------------------------------------------

MERMAID_FENCE = re.compile(r"^[ \t]*```mermaid[ \t]*\r?\n(.*?)^[ \t]*```",
                           re.MULTILINE | re.DOTALL)

RENDER_TAIL = """
<script type="module">
  const errs = [];
  mermaid.initialize({ startOnLoad: false });
  for (const p of document.querySelectorAll('pre.mermaid')) {
    try { await mermaid.parse(p.textContent); }
    catch (e) { errs.push(p.dataset.src + ' :: ' + (e && e.message || e)); }
  }
  const n = document.querySelectorAll('pre.mermaid').length;
  document.getElementById('verdict').textContent =
    errs.length ? ('PARSE ERRORS: ' + errs.length + ' of ' + n) : ('ALL OK (' + n + ' diagrams)');
  document.getElementById('errors').textContent = errs.join('\\n\\n');
  await mermaid.run({ querySelector: 'pre.mermaid' });
</script></body></html>
"""


def render_check(root: Path, cfg: dict, rep: Reporter, open_browser: bool = True) -> None:
    """Build a page that mermaid.parse()es every diagram, and open it.

    There is no offline Mermaid linter without npm, so this is the workaround:
    a self-contained page plus one CDN fetch. If npm is already in your repo,
    mermaid-cli's `mmdc -i f.mmd -o out.svg` renders each file and exits non-zero
    on a parse error — the browser step gone, and the CI form of this check.

    Not theoretical — the first run of this against eight hand-written diagrams
    found NINE real parse errors in files that had been read carefully:
    a bare `%%` line survives Mermaid's comment stripper and collides with the
    graph header, and `A -. .-> B` is not an unlabelled dotted edge.

    In: repo root, config, reporter. Out: nothing; writes the harness and opens
    it. Markdown fences are included because architecture diagrams embedded in
    a README break exactly as easily and nothing else would ever parse them.
    """
    items: list[tuple[str, str]] = []

    for diagram in find_diagrams(root, cfg):
        items.append((diagram.path, (root / diagram.path).read_text(encoding="utf-8")))

    if cfg.get("scan_markdown_diagrams", True):
        for md in sorted(root.rglob("*.md")):
            rel = str(md.relative_to(root)).replace("\\", "/")
            if is_skipped(rel, cfg["skip_dirs"]):
                continue
            text = md.read_text(encoding="utf-8", errors="replace")
            if "```mermaid" not in text:
                continue
            for n, m in enumerate(MERMAID_FENCE.finditer(text), start=1):
                items.append((f"{rel} (inline #{n})", m.group(1)))

    if not items:
        rep.line("No Mermaid diagrams found.")
        return

    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        "<title>diagram render check</title>",
        '<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>',
        "<style>body{font:14px system-ui;margin:2rem}"
        "h1{position:sticky;top:0;background:#fff;padding:.5rem 0}"
        "#errors{color:#a00;white-space:pre-wrap}</style>",
        '</head><body><h1 id="verdict">rendering…</h1><pre id="errors"></pre>',
    ]
    for label, source in items:
        parts.append(
            f'<h2>{html.escape(label)}</h2>'
            f'<pre class="mermaid" data-src="{html.escape(label, quote=True)}">'
            f'{html.escape(source)}</pre>'
        )
    parts.append(RENDER_TAIL)

    out = Path(tempfile.gettempdir()) / "diagram-render-check.html"
    out.write_text("\n".join(parts), encoding="utf-8")

    rep.line(f"Wrote render harness for {len(items)} diagram(s):")
    rep.line(f"  {out}")
    rep.line("")
    rep.line("Opening in the default browser. The heading reads ALL OK or lists")
    rep.line("the parse errors; the diagrams themselves render below it.")
    if open_browser:
        webbrowser.open(out.as_uri())


# --- Entry point --------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the selected checks, return an exit code.

    In: argv (None means sys.argv). Out: 0 when clean, 1 on any finding, 2 on a
    malformed config.

    --audit and --render both exit 0 unconditionally: neither is a gate. Only
    the checks gate.
    """
    parser = argparse.ArgumentParser(
        prog="check_docs.py",
        description="Verify functions are documented and diagrams match the code.",
    )
    parser.add_argument("--all", action="store_true",
                        help="checks 1-3 only, ignore git state")
    parser.add_argument("--deep", action="store_true",
                        help="narrow check 4 to changed functions, not files")
    parser.add_argument("--audit", action="store_true",
                        help="coverage for every source file; gates nothing")
    parser.add_argument("--render", action="store_true",
                        help="build a page that parses every diagram")
    parser.add_argument("--quiet", action="store_true",
                        help="exit code only, no output")
    parser.add_argument("--root", default=None,
                        help="repo root (default: discovered from cwd)")
    parser.add_argument("--no-open", action="store_true",
                        help="with --render, write the harness but do not open it")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else find_repo_root(Path.cwd())
    cfg = load_config(root)
    rep = Reporter(quiet=args.quiet)

    if args.render:
        render_check(root, cfg, rep, open_browser=not args.no_open)
        return 0

    if args.audit:
        show_audit(root, cfg, rep)
        return 0

    diagrams = find_diagrams(root, cfg)
    check_coverage(root, cfg, rep)
    check_covers(root, diagrams, rep)
    if not args.all:
        check_staleness(root, diagrams, rep, deep=args.deep)

    rep.line("")
    if rep.findings == 0:
        rep.line("check-docs: clean.")
        return 0
    rep.line(f"check-docs: {rep.findings} finding(s).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
