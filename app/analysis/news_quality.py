"""新闻质量评估：来源分级 + 综合质量分。

- source_tier：按媒体权威性分 1/2/3（1=一线财经权威，3=PR通稿/博客/未知）
- quality_score：0..1 综合分 = 来源权重 × 0.55 + 相关度 × 0.30 + 时效 × 0.15
  供推送过滤排序、详情展示、情绪加权使用。
"""
from datetime import datetime, timezone

# 一线权威财经媒体（情绪/事实可信度最高）
_TIER1 = (
    "reuters", "bloomberg", "wall street journal", "wsj", "financial times",
    "ft.com", "cnbc", "barron", "the economist", "associated press", "ap news",
    "dow jones", "marketwatch", "nikkei",
)
# 二线主流财经/券商研究
_TIER2 = (
    "benzinga", "seeking alpha", "seekingalpha", "yahoo", "forbes",
    "business insider", "thestreet", "investing.com", "morningstar", "zacks",
    "motley fool", "fool.com", "investor", "kiplinger", "investopedia",
    "techcrunch", "the verge", "ars technica",
)
# 三线：PR 通稷/未知/博客（默认）
_PR = (
    "globenewswire", "prnewswire", "pr newswire", "business wire", "businesswire",
    "accesswire", "newsfile", "globe newswire",
)

_SRC_WEIGHT = {1: 1.0, 2: 0.7, 3: 0.4}


def classify_source(name: str | None) -> int:
    """媒体名 → 质量等级 1/2/3（1 最佳）。未知/PR 通稿归 3。"""
    n = (name or "").lower()
    if not n:
        return 3
    if any(k in n for k in _PR):
        return 3
    if any(k in n for k in _TIER1):
        return 1
    if any(k in n for k in _TIER2):
        return 2
    return 3


def _recency_factor(published) -> float:
    """越新越高：当天≈1.0，7天≈0.5，14天起 0.2 触底。"""
    if not published:
        return 0.5
    try:
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - published).total_seconds() / 86400.0
    except Exception:
        return 0.5
    return max(0.2, min(1.0, 1.0 - days / 14.0))


def quality_score(tier: int | None, relevance: float | None, published) -> float:
    """0..1 综合质量分。"""
    sw = _SRC_WEIGHT.get(tier or 3, 0.4)
    rel = 0.6 if relevance is None else max(0.0, min(1.0, relevance))
    rec = _recency_factor(published)
    return round(0.55 * sw + 0.30 * rel + 0.15 * rec, 3)


def quality_weight(tier: int | None, relevance: float | None) -> float:
    """情绪聚合用的静态权重（不含时效）：来源权重 × 相关度。一线高相关权重最大。"""
    sw = _SRC_WEIGHT.get(tier or 3, 0.4)
    rel = 0.6 if relevance is None else max(0.0, min(1.0, relevance))
    return round(sw * (0.5 + 0.5 * rel), 3)
