from brokerops_core.models.feedback import Sentiment
from brokerops_core.services.feedback_extraction import extract_feedback, parse_budget_range

HOT_TRANSCRIPT = (
    "We loved the kitchen and the backyard was perfect for the kids. "
    "Honestly we are ready to write an offer if the sellers are flexible on the closing date. "
    "Our budget is between four fifty and five twenty five, so it works."
)

COOL_TRANSCRIPT = (
    "It was a nice house but it felt overpriced for the neighborhood. "
    "The bathroom is dated and needs updating. We are going to keep looking."
)


def test_hot_transcript_extraction() -> None:
    extracted = extract_feedback(HOT_TRANSCRIPT)
    assert extracted.sentiment is Sentiment.POSITIVE
    assert extracted.hot_signal is True
    assert "kitchen" in extracted.highlights
    assert extracted.budget_min == 450_000
    assert extracted.budget_max == 525_000
    assert "HOT" in extracted.summary


def test_cool_transcript_extraction() -> None:
    extracted = extract_feedback(COOL_TRANSCRIPT)
    assert extracted.sentiment is Sentiment.NEGATIVE
    assert extracted.hot_signal is False
    assert extracted.price_opinion == "overpriced"
    assert "bathroom" in extracted.concerns
    assert extracted.budget_min is None


def test_neutral_when_no_cues() -> None:
    extracted = extract_feedback("The showing happened at three. They walked through quickly.")
    assert extracted.sentiment is Sentiment.NEUTRAL
    assert extracted.highlights == []


def test_budget_parsing_variants() -> None:
    assert parse_budget_range("between four fifty and five twenty five") == (450_000, 525_000)
    assert parse_budget_range("between $480,000 and $510,000") == (480_000, 510_000)
    assert parse_budget_range("between 450k and 500k") == (450_000, 500_000)
    assert parse_budget_range("somewhere between five hundred and… actually no idea") is None
    assert parse_budget_range("no budget mentioned") is None
