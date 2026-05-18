"""run_daily.py — 每日 pipeline 用例。

链路：
  行情快照 (PriceSnapshotBatch)
    → signal_scan (StockSignal 列表)
    → Attributor (AttributionResult + ai_score)
    → 组装 CandidateSignal
    → rank_candidates (main / secondary / archive)
    → render_daily_report (Markdown 字符串)

设计原则：
  - 只依赖接口（Attributor Protocol），不依赖具体 LLM 实现。
  - 不直接调用 I/O，所有快照加载和报告写入由调用方（CLI）负责。
  - sector_score 来自 theme_mapping 驱动的板块联动扫描，缺少主题映射时使用保守默认值。
  - ai_recommendation 从 AttributionResult.upgrade_recommendation 映射。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from lurker.ai.attributor import Attributor, StubAttributor
from lurker.application.rank_candidates import rank_candidates
from lurker.application.signal_scan import StockSignal, scan_signals
from lurker.reports.daily_report import render_daily_report
from lurker.reports.trend_card import render_trend_card


# 缺少主题映射时使用保守默认分，避免无映射标的被静默丢弃。
_DEFAULT_SECTOR_SCORE = 50.0

# upgrade_recommendation -> ai_recommendation 映射（保持与 domain.models 一致）
_RECOMMENDATION_MAP: dict[str, str] = {
    "升级": "升级",
    "降级": "降级",
    "观察": "观察",
    "证据不足": "证据不足",
}


def _normalize_symbols(symbols: set[str] | list[str] | None) -> set[str]:
    if not symbols:
        return set()
    return {symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()}


def _filter_suppressed_candidates(
    ranked: dict[str, list[dict]],
    suppressed_symbols: set[str],
) -> tuple[dict[str, list[dict]], list[str]]:
    if not suppressed_symbols:
        return ranked, []

    filtered: dict[str, list[dict]] = {}
    hidden_symbols: set[str] = set()
    for tier, candidates in ranked.items():
        filtered[tier] = []
        for candidate in candidates:
            symbol = str(candidate.get("symbol", "")).upper()
            if symbol in suppressed_symbols:
                hidden_symbols.add(symbol)
                continue
            filtered[tier].append(candidate)

    if not hidden_symbols:
        return filtered, []

    symbols_text = "、".join(sorted(hidden_symbols))
    return filtered, [f"本地屏蔽列表已隐藏 {len(hidden_symbols)} 条：{symbols_text}"]


def _build_candidate(
    signal: StockSignal,
    ai_score: int,
    attribution_summary: str,
    theme_id: str | None = None,
    sector_score: int | None = None,
) -> dict:
    """将信号 + AI 归因结果组装为 rank_candidates 可接受的 dict。"""
    return {
        "theme": theme_id or signal.symbol,
        "stock_score": float(signal.stock_score),
        "sector_score": sector_score if sector_score is not None else _DEFAULT_SECTOR_SCORE,
        "ai_score": float(ai_score),
        "trigger_type": "stock_first",
        "ai_recommendation": "观察",  # StubAttributor 下默认"观察"，真实 LLM 后从归因映射
        # 额外字段，供报告渲染使用
        "symbol": signal.symbol,
        "market": signal.market,
        "double_bagger_class": signal.double_bagger_class,
        "attribution_summary": attribution_summary,
        "returns": signal.returns,
    }


def run_daily(
    *,
    snapshot_batch: dict[str, Any],
    theme_mapping: dict[str, list[str]] | None = None,
    attributor: Attributor | None = None,
    report_date: str | None = None,
    signal_threshold: int = 60,
    main_limit: int = 10,
    low_score_watch_limit: int = 5,
    suppressed_symbols: set[str] | list[str] | None = None,
) -> str:
    """执行每日完整 pipeline，返回 Markdown 日报字符串。

    Args:
        snapshot_batch: collect_price_snapshot_batch 或 FilePriceSnapshotStore.load_latest() 的返回值。
        attributor: AI 归因实现，默认使用 StubAttributor。
        report_date: 报告日期字符串，默认 today。
        signal_threshold: scan_signals 过滤阈值，默认 60。
        main_limit: 主候选数量上限，默认 10。
        low_score_watch_limit: 从 archive 中展示的低分观察样本数量，默认 5。
        suppressed_symbols: 本地屏蔽标的集合，不进入日报展示。

    Returns:
        Markdown 格式的每日日报字符串。
    """
    if attributor is None:
        attributor = StubAttributor()

    today = report_date or date.today().isoformat()
    windows: list[int] = snapshot_batch.get("windows", [20, 60, 120, 180])
    snapshots: list[dict[str, Any]] = snapshot_batch.get("snapshots", [])

    # Step 1: 信号扫描
    signals: list[StockSignal] = scan_signals(snapshots, windows, threshold=signal_threshold)

    # failures 统计放在 early return 前，确保无信号时也能提示
    failures: list[dict] = snapshot_batch.get("failures", [])
    risk_alerts: list[str] = []
    if failures:
        risk_alerts.append(f"本次快照有 {len(failures)} 只标的行情获取失败，信号可能不完整。")

    if not signals:
        return render_daily_report(
            report_date=today,
            main_cards=["今日无个股触发强度信号。"],
            secondary_leads=[],
            low_score_watch_samples=[],
            watchlist_changes=[],
            risk_alerts=risk_alerts,
        )

    from lurker.ingest.news import fetch_recent_news
    from lurker.application.sector_scan import compute_theme_scores

    # 计算板块联动分
    theme_scores = compute_theme_scores(signals, theme_mapping or {})

    # Step 2: 逐信号归因 + 组装候选
    candidates: list[dict] = []
    for signal in signals:
        # 在送入归因前抓取新闻喂料
        news_items = fetch_recent_news(signal.symbol, signal.market, limit=3)
        if news_items:
            signal.extra_sources = news_items

        attribution_result, ai_score = attributor.attribute(signal)

        # 找到个股所属得分最高的主题
        best_theme = None
        best_sector_score = 0
        themes = (theme_mapping or {}).get(signal.symbol, [])
        if themes:
            # 选取得分最高的主题
            best_theme = max(themes, key=lambda t: theme_scores.get(t, 0))
            best_sector_score = theme_scores.get(best_theme, 0)
        else:
            best_sector_score = _DEFAULT_SECTOR_SCORE

        candidate = _build_candidate(
            signal,
            ai_score,
            attribution_result.reason_summary,
            theme_id=best_theme,
            sector_score=best_sector_score,
        )
        # 覆盖 ai_recommendation（真实 LLM 时从归因结果映射）
        candidate["ai_recommendation"] = _RECOMMENDATION_MAP.get(
            attribution_result.upgrade_recommendation, "观察"
        )
        candidates.append(candidate)

    # Step 3: 候选排序
    ranked = rank_candidates(candidates, main_limit=main_limit)
    ranked, watchlist_changes = _filter_suppressed_candidates(
        ranked,
        _normalize_symbols(suppressed_symbols),
    )

    # Step 4: 渲染主候选趋势卡片
    main_cards: list[str] = []
    for c in ranked["main"]:
        return_lines = [
            f"{k.replace('return_', '').replace('d', 'D 窗口')}: {v * 100:.1f}%"
            for k, v in c.get("returns", {}).items()
        ]
        card = render_trend_card(
            theme=f"{c['symbol']} ({c['market'].upper()})",
            status="主候选",
            stage="发现",
            total_score=c["total_score"],
            triggers=return_lines or ["个股强度信号触发"],
            attribution=c.get("attribution_summary", ""),
            evidence=[],
            risks=[],
            next_checks=["等待新闻/公告归因", "观察板块联动"],
        )
        main_cards.append(card)

    # Step 5: 次级线索
    secondary_leads: list[str] = [
        f"{c['symbol']} ({c['market'].upper()})：总分 {c['total_score']}，{c['ai_recommendation']}，保留观察"
        for c in ranked["secondary"]
    ]

    low_score_watch_samples: list[str] = [
        (
            f"{c['symbol']} ({c['market'].upper()})：总分 {c['total_score']}，"
            f"个股分 {c['stock_score']}，{c['ai_recommendation']}，低分观察"
        )
        for c in ranked["archive"][:low_score_watch_limit]
    ]

    return render_daily_report(
        report_date=today,
        main_cards=main_cards,
        secondary_leads=secondary_leads,
        low_score_watch_samples=low_score_watch_samples,
        watchlist_changes=watchlist_changes,
        risk_alerts=risk_alerts,
    )
