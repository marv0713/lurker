import ast
from pathlib import Path

from lurker.application.rank_candidates import rank_candidates
from lurker.domain.models import CandidateSignal
from lurker.domain.policies import visibility_tier
from lurker.domain.signals import classify_double_bagger, score_sector_breadth, score_stock_strength


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIR = ROOT / "src" / "lurker" / "domain"


def test_domain_exports_core_language():
    candidate = CandidateSignal(
        theme="AI 算力基础设施",
        stock_score=86,
        sector_score=76,
        ai_score=80,
        trigger_type="stock_first",
        ai_recommendation="升级",
    )

    assert candidate.theme == "AI 算力基础设施"
    assert classify_double_bagger(1.2) == "double"
    assert score_stock_strength({"return_180d": 1.05, "near_52w_high": True}) >= 25
    assert score_sector_breadth({"cross_market_count": 2}) == 15
    assert visibility_tier(total_score=82, ai_recommendation="升级") == "main"


def test_application_ranks_domain_candidates():
    result = rank_candidates(
        [
            CandidateSignal(
                theme="AI 算力基础设施",
                stock_score=86,
                sector_score=76,
                ai_score=80,
                trigger_type="stock_first",
                ai_recommendation="升级",
            ),
            CandidateSignal(
                theme="创新药出海",
                stock_score=62,
                sector_score=55,
                ai_score=50,
                trigger_type="stock_first",
                ai_recommendation="证据不足",
            ),
        ]
    )

    assert result["main"][0]["theme"] == "AI 算力基础设施"
    assert result["secondary"][0]["theme"] == "创新药出海"


def test_domain_layer_does_not_import_infrastructure_libraries():
    forbidden_modules = {"pandas", "sqlalchemy", "requests", "yfinance", "akshare"}

    for path in DOMAIN_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        assert imported.isdisjoint(forbidden_modules), f"{path} imports {imported & forbidden_modules}"
