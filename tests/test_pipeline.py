from lurker.pipeline import rank_candidates


def test_rank_candidates_splits_main_and_secondary():
    candidates = [
        {
            "theme": "AI 算力基础设施",
            "stock_score": 86,
            "sector_score": 76,
            "ai_score": 80,
            "trigger_type": "stock_first",
            "ai_recommendation": "升级",
        },
        {
            "theme": "创新药出海",
            "stock_score": 62,
            "sector_score": 55,
            "ai_score": 50,
            "trigger_type": "stock_first",
            "ai_recommendation": "证据不足",
        },
    ]

    result = rank_candidates(candidates, main_limit=10)

    assert result["main"][0]["theme"] == "AI 算力基础设施"
    assert result["secondary"][0]["theme"] == "创新药出海"
