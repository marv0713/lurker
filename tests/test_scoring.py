from lurker.scoring.candidate_score import combine_candidate_scores, visibility_tier
from lurker.signals.sector_breadth import score_sector_breadth


def test_score_sector_breadth_for_cross_market_diffusion():
    metrics = {
        "sector_outperformance": True,
        "strong_stock_count": 5,
        "new_high_ratio": 0.22,
        "chain_segments": 2,
        "cross_market_count": 2,
        "turnover_persistent": True,
    }

    score = score_sector_breadth(metrics)

    assert score >= 75


def test_combine_candidate_scores_stock_first():
    total = combine_candidate_scores(
        stock_score=86,
        sector_score=76,
        ai_score=80,
        trigger_type="stock_first",
    )

    assert total == 80.7


def test_visibility_tier_keeps_secondary_leads_visible():
    assert visibility_tier(total_score=82, ai_recommendation="升级") == "main"
    assert visibility_tier(total_score=62, ai_recommendation="证据不足") == "secondary"
    assert visibility_tier(total_score=40, ai_recommendation="降级") == "archive"
