"""news.py — 股票新闻与公告摘要抓取模块。

提供 fetch_recent_news 接口，用于为 AI 归因提供真实的文本上下文。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _fetch_cn_news(symbol: str, limit: int) -> list[str]:
    import akshare as ak

    # 剥离后缀，如 300308.SZ -> 300308
    code = symbol.split(".")[0]

    try:
        df = ak.stock_news_em(symbol=code)
        if df.empty:
            return []

        results = []
        for _, row in df.head(limit).iterrows():
            title = str(row.get("新闻标题", ""))
            content = str(row.get("新闻内容", ""))
            pub_date = str(row.get("发布时间", ""))
            # 简化日期显示
            if len(pub_date) > 10:
                pub_date = pub_date[:10]  # YYYY-MM-DD

            results.append(f"[{pub_date}] {title}：{content}")
        return results
    except Exception as e:
        logger.warning("获取 A 股新闻失败 symbol=%s error=%s", symbol, e)
        return []


def _fetch_yf_news(symbol: str, limit: int) -> list[str]:
    import yfinance as yf

    try:
        ticker = yf.Ticker(symbol)
        news_items: list[dict[str, Any]] = getattr(ticker, "news", [])
        if not news_items:
            return []

        results = []
        for item in news_items[:limit]:
            # yfinance news 结构体处理
            content = item.get("content", item) # 兼容不同 yfinance 版本的字典结构
            title = content.get("title", "")
            summary = content.get("summary", "")
            pub_date_raw = content.get("pubDate", "")

            # 解析日期
            pub_date = ""
            if pub_date_raw:
                try:
                    # 例如 '2026-05-17T10:43:18Z' -> '2026-05-17'
                    if "T" in pub_date_raw:
                        pub_date = pub_date_raw.split("T")[0]
                    else:
                        pub_date = pub_date_raw[:10]
                except Exception:
                    pub_date = pub_date_raw

            date_prefix = f"[{pub_date}] " if pub_date else ""
            results.append(f"{date_prefix}{title} - {summary}")
        return results
    except Exception as e:
        logger.warning("获取美港股新闻失败 symbol=%s error=%s", symbol, e)
        return []


def fetch_recent_news(symbol: str, market: str, limit: int = 3) -> list[str]:
    """获取指定股票的最近新闻摘要。

    Args:
        symbol: 股票代码（带后缀，如 '300308.SZ', 'NVDA', '0700.HK'）
        market: 市场代码 ('cn', 'us', 'hk')
        limit: 获取条数上限

    Returns:
        新闻字符串列表，格式类似 "[YYYY-MM-DD] 标题 - 摘要"
    """
    if market == "cn":
        return _fetch_cn_news(symbol, limit)
    elif market in {"us", "hk"}:
        return _fetch_yf_news(symbol, limit)
    return []
