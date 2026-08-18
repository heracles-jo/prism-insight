"""compute_us_fractional_sell_quantity under fractional (Toss) holdings.

The int() truncation this guards against turned Decimal("0.44") into 0, which
reported a held position as nothing to sell (migration audit P0 #3).
"""

from decimal import Decimal

import pytest

from tracking.db_schema import compute_us_fractional_sell_quantity


@pytest.mark.parametrize(
    "total, rows, expected",
    [
        # KIS/integer path: byte-for-byte the original arithmetic.
        (10, 3, 3),
        (10, 1, 10),
        (7, 2, 3),
        (0, 3, 0),
        (Decimal("3"), 2, 1),                      # integral Decimal stays on the int path
        # Fractional (Toss) path.
        (Decimal("0.44"), 1, Decimal("0.44")),     # the P0 case: sellable, not 0
        (Decimal("1.68"), 2, Decimal("0.84")),
        (Decimal("0.0000019"), 2, Decimal("0.000000")),  # six-decimal ROUND_DOWN
        (Decimal("-0.5"), 2, 0),
        # Junk stays harmless.
        ("junk", 2, 0),
        (None, 2, 0),
    ],
)
def test_split_quantities(total, rows, expected):
    result = compute_us_fractional_sell_quantity(total, rows)
    assert result == expected
    # Integer inputs must keep returning ints (the KIS order path formats them
    # with str(int(q))); fractional inputs must come back as exact Decimals.
    if isinstance(expected, Decimal) and expected == Decimal("0.44"):
        assert isinstance(result, Decimal)
    if expected == 3 and total == 10:
        assert isinstance(result, int)


def test_junk_remaining_rows_falls_back_to_selling_everything():
    assert compute_us_fractional_sell_quantity(5, "junk") == 5
    assert compute_us_fractional_sell_quantity(0, "junk") == 0
