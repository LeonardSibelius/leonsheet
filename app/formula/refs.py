"""Cell-reference conversions: the canonical (row, col) <-> "A1" boundary.

Predicted bug #15 (column-index off-by-one): Excel columns are bijective base-26,
*not* zero-padded base-26. "A" is 1, not 0; there is no digit for zero. So:
    A=1, Z=26, AA=27, AZ=52, BA=53, ZZ=702, AAA=703.

We expose 0-indexed integers internally (so the v1.0 26-col grid spans col=0..25,
the v1.0 100-row grid spans row=0..99). Conversion to/from letter form happens
at this boundary and nowhere else. Predicted bug #17 (canonical cell-ID format):
everything downstream of the parser uses (row, col) ints; only the UI / CSV
boundary speaks "A1".
"""

from __future__ import annotations

A = ord("A")


def col_letters_to_index(letters: str) -> int:
    """Convert a column letter run ("A", "AA", "ZZ", ...) to a 0-indexed column.

    Raises ValueError on empty input or non-letters. Case-insensitive.
    """
    if not letters:
        raise ValueError("empty column letters")
    n = 0
    for c in letters.upper():
        if not ("A" <= c <= "Z"):
            raise ValueError(f"non-letter character {c!r} in column letters {letters!r}")
        n = n * 26 + (ord(c) - A + 1)
    return n - 1  # collapse 1-indexed bijective base-26 to 0-indexed


def col_index_to_letters(index: int) -> str:
    """Inverse of col_letters_to_index. index must be >= 0."""
    if index < 0:
        raise ValueError(f"negative column index {index}")
    n = index + 1  # work in 1-indexed bijective base-26
    letters: list[str] = []
    while n > 0:
        n -= 1
        letters.append(chr(A + n % 26))
        n //= 26
    return "".join(reversed(letters))


def parse_cell_ref(ref: str) -> tuple[int, int]:
    """Parse "A1", "AA10", etc. into (row, col), both 0-indexed.

    Returns (0, 0) for "A1" — A is column 0, row 1 is row index 0.
    Raises ValueError if the ref isn't well-formed.
    """
    ref = ref.upper()
    # Find the boundary between letters and digits.
    split = 0
    while split < len(ref) and ref[split].isalpha():
        split += 1
    if split == 0 or split == len(ref):
        raise ValueError(f"malformed cell reference {ref!r}")
    letters, digits = ref[:split], ref[split:]
    if not digits.isdigit():
        raise ValueError(f"malformed cell reference {ref!r}")
    row_1indexed = int(digits)
    if row_1indexed < 1:
        raise ValueError(f"row number must be >= 1 in {ref!r}")
    return row_1indexed - 1, col_letters_to_index(letters)


def format_cell_ref(row: int, col: int) -> str:
    """Inverse of parse_cell_ref: (row, col) 0-indexed -> "A1"-style string."""
    if row < 0:
        raise ValueError(f"negative row {row}")
    return f"{col_index_to_letters(col)}{row + 1}"
