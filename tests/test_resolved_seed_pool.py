import json

from lurker.universe.resolved_seed_pool import (
    build_resolved_seed_pool,
    extract_seed_symbols,
    load_resolved_seed_pool,
    save_resolved_seed_pool,
)


def test_build_resolved_seed_pool_keeps_source_attribution(tmp_path):
    themes_path = tmp_path / "themes.yaml"
    themes_path.write_text(
        """
themes:
  - id: ai_infra
    markets:
      cn:
        seed_indexes: [创业板指]
        seed_etfs: [人工智能 ETF]
        seed_symbols: [300308.SZ]
      us:
        seed_symbols: [NVDA]
""",
        encoding="utf-8",
    )

    pool = build_resolved_seed_pool(
        themes_path,
        generated_at="2026-05-17T12:00:00+00:00",
        cn_index_resolver=lambda index_name: ["300502.SZ"] if index_name == "创业板指" else [],
        cn_etf_resolver=lambda etf_name: ["002230.SZ"] if etf_name == "人工智能 ETF" else [],
    )

    assert pool["generated_at"] == "2026-05-17T12:00:00+00:00"
    assert pool["markets"]["cn"]["symbols"] == ["300308.SZ", "300502.SZ", "002230.SZ"]
    assert pool["markets"]["cn"]["sources"]["manual"] == ["300308.SZ"]
    assert pool["markets"]["cn"]["sources"]["indexes"] == {"创业板指": ["300502.SZ"]}
    assert pool["markets"]["cn"]["sources"]["etfs"] == {"人工智能 ETF": ["002230.SZ"]}
    assert pool["markets"]["cn"]["sources"]["unresolved"] == []
    assert pool["markets"]["us"]["symbols"] == ["NVDA"]


def test_build_resolved_seed_pool_includes_symbol_names(tmp_path):
    themes_path = tmp_path / "themes.yaml"
    themes_path.write_text(
        """
themes:
  - id: ai_infra
    markets:
      cn:
        seed_indexes: [创业板指]
        seed_symbols: [300308.SZ]
      us:
        seed_symbols: [NVDA]
""",
        encoding="utf-8",
    )

    pool = build_resolved_seed_pool(
        themes_path,
        generated_at="2026-05-17T12:00:00+00:00",
        cn_index_resolver=lambda index_name: ["300502.SZ"] if index_name == "创业板指" else [],
        cn_etf_resolver=lambda etf_name: [],
        cn_symbol_name_resolver=lambda symbols: {
            "300308.SZ": "中际旭创",
            "300502.SZ": "新易盛",
        },
    )

    assert pool["symbol_names"] == {
        "300308.SZ": "中际旭创",
        "300502.SZ": "新易盛",
    }


def test_save_and_load_resolved_seed_pool(tmp_path):
    path = tmp_path / "resolved_seed_pool.json"
    pool = {
        "generated_at": "2026-05-17T12:00:00+00:00",
        "markets": {"cn": {"symbols": ["300308.SZ"], "sources": {}}},
    }

    save_resolved_seed_pool(pool, path)

    assert json.loads(path.read_text(encoding="utf-8")) == pool
    assert load_resolved_seed_pool(path) == pool


def test_extract_seed_symbols_from_resolved_pool():
    pool = {
        "generated_at": "2026-05-17T12:00:00+00:00",
        "markets": {
            "cn": {"symbols": ["300308.SZ"], "sources": {}},
            "us": {"symbols": ["NVDA"], "sources": {}},
        },
    }

    assert extract_seed_symbols(pool) == {"cn": ["300308.SZ"], "us": ["NVDA"]}
