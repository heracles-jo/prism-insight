"""A screening pass that qualifies no stock must return nothing, not raise.

Found by running `trigger_batch.py morning` for real rather than importing it:
on a thin snapshot the whole batch died with

    ValueError: Can only compare identically-labeled Series objects

The mechanism is worth stating, because it is not obvious and it is the reason
an `if .empty` line is load-bearing rather than tidy. Each trigger narrows
`snap` by market cap and trade value, then does arithmetic against `prev`,
which was never narrowed:

    snap["gap_up_rate"] = (snap["Open"] / prev["Close"] - 1) * 100

When `snap` is empty that division aligns on the index *union*, so the result
carries `prev`'s full index. Assigning it back expands the empty frame to the
whole universe while its original columns stay empty — the frame now reports
2,686 rows with a 0-length "Close". Two columns of the same frame then compare
at different lengths and pandas raises.

Three of the eight triggers already guarded; five did not.
"""

import pandas as pd
import pytest

import trigger_batch as tb

TRIGGERS = [
    tb.trigger_morning_volume_surge,
    tb.trigger_morning_gap_up_momentum,
    tb.trigger_morning_value_to_cap_ratio,
    tb.trigger_afternoon_daily_rise_top,
    tb.trigger_afternoon_closing_strength,
    tb.trigger_afternoon_volume_surge_flat,
]


def _universe(n=40):
    """A snapshot no stock can pass: real prices, trade value far below the bar."""
    tickers = [f"{i:06d}" for i in range(n)]
    frame = pd.DataFrame(
        {
            "Open": [10_000] * n,
            "High": [10_500] * n,
            "Low": [9_800] * n,
            "Close": [10_200] * n,
            "Volume": [10] * n,
            # SCREENING_MIN_TRADE_VALUE is orders of magnitude above this.
            "Amount": [1_000] * n,
        },
        index=tickers,
    )
    cap = pd.DataFrame({"시가총액": [900_000_000_000] * n}, index=tickers)
    return frame, frame.copy(), cap


@pytest.mark.parametrize("trigger", TRIGGERS, ids=lambda f: f.__name__)
def test_a_universe_that_qualifies_nobody_returns_empty(trigger):
    snapshot, prev, cap = _universe()

    result = trigger("20260819", snapshot, prev, cap)

    assert isinstance(result, pd.DataFrame)
    assert result.empty


@pytest.mark.parametrize("trigger", TRIGGERS, ids=lambda f: f.__name__)
def test_the_frame_is_never_expanded_by_alignment(trigger):
    """The specific failure: `prev` wider than `snap` used to grow `snap` back
    to `prev`'s index. Give prev extra rows so alignment would be visible."""
    snapshot, _, cap = _universe(n=10)
    wide_prev = _universe(n=200)[0]

    result = trigger("20260819", snapshot, wide_prev, cap)

    assert result.empty, "alignment against a wider prev must not resurrect rows"


def test_every_trigger_guards_after_the_absolute_filter():
    """The rule, not the six samples above: `trigger_contrarian_value` reaches
    the login-gated KRX client before its filter, so it cannot be called here —
    a source-level check keeps it covered anyway."""
    import pathlib

    src = pathlib.Path(tb.__file__).read_text().splitlines()
    unguarded = []
    for i, line in enumerate(src):
        if "apply_absolute_filters(" in line and not line.lstrip().startswith("def "):
            if ".empty" not in "\n".join(src[i + 1 : i + 4]):
                name = next(
                    (
                        src[j].split("(")[0].replace("def ", "").strip()
                        for j in range(i, -1, -1)
                        if src[j].startswith("def ")
                    ),
                    f"line {i + 1}",
                )
                unguarded.append(name)

    assert not unguarded, (
        "these narrow the universe and then do arithmetic against `prev` "
        f"without checking for empty first: {', '.join(unguarded)}"
    )
