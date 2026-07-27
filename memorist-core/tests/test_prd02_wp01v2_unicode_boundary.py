"""PRD-02 WP01 v2: Unicode transport integrity is not linguistic normalization.

The field trace behind this suite showed Persian text arriving as mojibake in a
Windows PowerShell diagnostic export while the database rows behind it were
perfectly intact. Two different things were being conflated:

    Unicode transport integrity  !=  linguistic normalization

A transport bug is fixed at the boundary that decoded the bytes wrongly. It is
never fixed by teaching the runtime to recognise mojibake and guess the original
text back, because a guess that is usually right is a guess that silently
rewrites evidence when it is wrong. These tests pin both halves: text survives
every supported boundary exactly, and the runtime does not try to repair text
that did not.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from memcore.textsemantics import (
    analyze_text,
    normalize_text,
    normalize_with_mapping,
    raw_text_hash,
)

PERSIAN = "کوبونتو یه مزیت دارد: حذف لایه WSL2."
MIXED = "الان احتمالاً Docker را روی WSL2 اجرا می‌کنیم در ویندوز."
DEFERRED = "یادت باشه بعدا درباره اش صحبت کنیم."
CORPUS = (PERSIAN, MIXED, DEFERRED, "من از ویندوز استفاده نمی‌کنم.", "سرعت و عملکرد")


@pytest.mark.parametrize("text", CORPUS)
def test_python_string_round_trip_is_exact(text: str) -> None:
    assert text.encode("utf-8").decode("utf-8") == text


@pytest.mark.parametrize("text", CORPUS)
def test_json_round_trip_is_exact(text: str) -> None:
    for ensure_ascii in (True, False):
        payload = json.dumps({"text": text}, ensure_ascii=ensure_ascii)
        assert json.loads(payload)["text"] == text


@pytest.mark.parametrize("text", CORPUS)
def test_sqlite_round_trip_is_exact(tmp_path: Path, text: str) -> None:
    database = tmp_path / "roundtrip.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence (raw TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence (raw) VALUES (?)", (text,))
    with sqlite3.connect(database) as connection:
        stored = connection.execute("SELECT raw FROM evidence").fetchone()[0]
    assert stored == text
    assert raw_text_hash(stored) == raw_text_hash(text)


@pytest.mark.skipif(
    not os.getenv("MEMORIST_POSTGRES_DSN"),
    reason="requires real PostgreSQL",
)
@pytest.mark.parametrize("text", CORPUS)
def test_postgres_round_trip_is_exact(text: str) -> None:
    import psycopg

    with (
        psycopg.connect(os.environ["MEMORIST_POSTGRES_DSN"]) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT %s::text", (text,))
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == text
    assert raw_text_hash(row[0]) == raw_text_hash(text)


@pytest.mark.skipif(
    not os.getenv("MEMORIST_POSTGRES_DSN"),
    reason="requires real PostgreSQL",
)
def test_postgres_stores_semantic_result_json_without_loss() -> None:
    """The whole contract survives a jsonb round trip, not just a bare string."""

    import psycopg

    result = analyze_text(f"{PERSIAN}\n{DEFERRED}")
    with (
        psycopg.connect(os.environ["MEMORIST_POSTGRES_DSN"]) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT %s::jsonb", (result.as_json(),))
        row = cursor.fetchone()
    assert row is not None
    assert json.loads(json.dumps(row[0])) == json.loads(result.as_json())


def test_mixed_rtl_ltr_round_trip_preserves_code_points() -> None:
    text = f"{MIXED} :: PostgreSQL 16"
    assert list(text) == list(text.encode("utf-8").decode("utf-8"))
    assert normalize_with_mapping(text).raw == text


def test_composed_and_decomposed_forms_both_round_trip() -> None:
    """NFC folding is a comparison view, never a rewrite of the evidence."""

    decomposed = unicodedata.normalize("NFD", "résumé و کافه")
    composed = unicodedata.normalize("NFC", decomposed)
    assert decomposed != composed

    for form in (decomposed, composed):
        mapped = normalize_with_mapping(form)
        assert mapped.raw == form, "raw text must survive normalization untouched"

    # The two spellings compare equal once normalized, which is the entire
    # point of having a normalized view.
    assert normalize_text(decomposed) == normalize_text(composed)


def test_raw_hash_is_stable_across_boundaries(tmp_path: Path) -> None:
    """One canonical UTF-8 hash, whatever the text passed through."""

    original = raw_text_hash(MIXED)
    database = tmp_path / "hash.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE t (raw TEXT)")
        connection.execute("INSERT INTO t VALUES (?)", (MIXED,))
        from_sqlite = connection.execute("SELECT raw FROM t").fetchone()[0]
    from_json = json.loads(json.dumps({"t": MIXED}))["t"]
    from_file = tmp_path / "text.txt"
    from_file.write_text(MIXED, encoding="utf-8")

    assert original == raw_text_hash(from_sqlite)
    assert original == raw_text_hash(from_json)
    assert original == raw_text_hash(from_file.read_text(encoding="utf-8"))


def test_diagnostic_subprocess_boundary_decodes_as_utf8() -> None:
    """A captured child process must be decoded as UTF-8, not via the locale.

    This is the exact shape of the field defect: `text=True` with no explicit
    encoding decodes captured output with `locale.getpreferredencoding()`, which
    on a Windows console is an OEM/ANSI code page. The child here writes real
    UTF-8 bytes; decoding them as cp1252 is what produced the mojibake in the
    export while the database rows stayed correct.
    """

    child = f"import sys; sys.stdout.buffer.write({PERSIAN!r}.encode('utf-8'))"
    completed = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=True,
    )
    assert completed.stdout == PERSIAN

    # The same bytes read through a single-byte code page are mangled -- which
    # is what the unpinned call did, and why pinning it is the fix.
    mangled = PERSIAN.encode("utf-8").decode("cp1252", errors="replace")
    assert mangled != PERSIAN


def test_diagnostic_exporters_pin_utf8_on_captured_output() -> None:
    """The repository's diagnostic exporters must not rely on the host locale."""

    root = Path(__file__).resolve().parents[2]
    for relative in ("scripts/baseline_check.py", "scripts/full_mode_check.py"):
        source = (root / relative).read_text(encoding="utf-8")
        captures = source.count("stdout=subprocess.PIPE") + source.count("capture_output=True")
        assert captures > 0, f"{relative} no longer captures output"
        assert source.count('encoding="utf-8"') >= captures, (
            f"{relative} captures child output without pinning encoding='utf-8'"
        )


def test_runtime_does_not_repair_mojibake() -> None:
    """Mojibake stays mojibake. Guessing it back would rewrite evidence.

    A "helpful" repair rule would have to decide that these bytes were meant to
    be Persian. When it guesses right nobody notices; when it guesses wrong it
    has silently altered the raw record an auditor relies on. The runtime is
    therefore required to leave it alone.
    """

    mojibake = PERSIAN.encode("utf-8").decode("cp1252", errors="replace")
    assert mojibake != PERSIAN

    result = analyze_text(mojibake)
    assert PERSIAN not in result.normalized_text
    assert result.raw_text_hash == raw_text_hash(mojibake)
    assert result.raw_text_hash != raw_text_hash(PERSIAN)

    # And the reverse direction: correct text is never "corrected" either.
    assert normalize_with_mapping(PERSIAN).raw == PERSIAN


def test_normalization_never_spell_corrects() -> None:
    """`Kubunto` is not silently turned into `Kubuntu`.

    Spelling correction is semantic rewriting wearing normalization's clothes.
    The misspelling is what the user wrote, so it is what the evidence says.
    """

    raw = "میخوام از ویندوز مهاجرت کنم به سیستم عامل Kubunto یا اوبونتو عادی."
    result = analyze_text(raw)
    assert "kubunto" in result.normalized_text
    assert "kubuntu" not in result.normalized_text
    for clause in result.clauses:
        assert raw[clause.raw_start : clause.raw_end] == clause.text
