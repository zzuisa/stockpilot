"""watchlist.yaml 加载 → 内存模型 → 数据库(说明书 §4)

配置层级(低→高): defaults → group → symbol;extra_* 追加不替换。
"""
import logging
import os
import re

import yaml

import settings
from models import Group, NotifyRoute, WatchlistItem

log = logging.getLogger(__name__)

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")

# defaults 中可被 group/symbol 覆盖的键
_INHERITABLE = [
    "notify_channels", "telegram_chat_ids", "email_recipients",
    "news_sources", "news_auto", "news_types", "rss_macro",
    "t212_community", "community_priority", "language",
]
_DEFAULT_NOTIFY_ON = ["daily_report", "signal"]

# 新闻类别规范键(对应 prompt.md 的 8 类高信号新闻),供 news_types 聚焦与 LLM 提示词
NEWS_TYPES = [
    "earnings",       # 财报发布、业绩指引及财报电话会议
    "announcement",   # 公司重大公告(产品/合作/并购/资本支出/订单)
    "industry",       # 行业趋势、供需变化、同业竞争
    "macro",          # 宏观经济(利率/政策/通胀/衰退)
    "regulatory",     # 监管、法律、地缘政治、政策(贸易/出口管制/反垄断)
    "analyst",        # 分析师评级变更、目标价、机构观点
    "supply_chain",   # 供应链、运营、生产
    "governance",     # 公司治理、公司行动(股息/回购/高管变动)
]
NEWS_TYPE_LABELS = {
    "earnings": "财报/业绩指引", "announcement": "公司重大公告",
    "industry": "行业趋势/竞争", "macro": "宏观经济",
    "regulatory": "监管/法律/地缘", "analyst": "分析师评级",
    "supply_chain": "供应链/运营", "governance": "公司治理/公司行动",
}
# 标的级新闻采集来源(RSS 为宏观无标的源,由 defaults.rss_macro 全局控制)
NEWS_SOURCES = ["finnhub", "alphavantage"]
_DEFAULT_NEWS_SOURCES = ["finnhub", "alphavantage"]


def _norm_news_sources(v) -> list[str]:
    if not v:
        return list(_DEFAULT_NEWS_SOURCES)
    return [s for s in v if s in NEWS_SOURCES]


def _norm_news_types(v) -> list[str]:
    if not v:
        return list(NEWS_TYPES)
    return [t for t in v if t in NEWS_TYPES]


def _sub_env(obj):
    """递归替换 ${VAR} 为环境变量值"""
    if isinstance(obj, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), obj)
    if isinstance(obj, list):
        return [_sub_env(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _sub_env(v) for k, v in obj.items()}
    return obj


def load_yaml(path: str | None = None) -> dict:
    path = path or settings.WATCHLIST_PATH
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return _sub_env(raw)


def resolve_group_cfg(defaults: dict, group: dict) -> dict:
    """group 级有效配置 = defaults 被 group 覆盖"""
    cfg = {k: defaults.get(k) for k in _INHERITABLE}
    for k in _INHERITABLE:
        if k in group:
            cfg[k] = group[k]
    cfg["notify_on"] = group.get("notify_on", _DEFAULT_NOTIFY_ON)
    if group.get("recipients"):
        cfg["recipients"] = group["recipients"]
    return cfg


def normalized_recipients(gcfg: dict) -> list[dict]:
    """统一成 [{channel, recipient, events}]。
    优先用显式 recipients(按接收人配置事件类型);否则由旧字段
    telegram_chat_ids/email_recipients × notify_on 合成,保证老配置不破。"""
    recips = gcfg.get("recipients")
    if recips:
        out = []
        for r in recips:
            ch = (r.get("channel") or "").strip()
            rec = (r.get("recipient") or "").strip()
            evs = list(r.get("events") or []) or list(gcfg.get("notify_on") or _DEFAULT_NOTIFY_ON)
            if ch in ("telegram", "email") and rec:
                out.append({"channel": ch, "recipient": rec, "events": evs})
        return out
    notify_on = list(gcfg.get("notify_on") or _DEFAULT_NOTIFY_ON)
    channels = gcfg.get("notify_channels") or []
    if "both" in channels:
        channels = ["telegram", "email"]
    out = []
    for ch in channels:
        for rec in _recipients_for(gcfg, ch):
            out.append({"channel": ch, "recipient": rec, "events": notify_on})
    return out


def resolve_symbol_cfg(group_cfg: dict, symbol: dict) -> dict:
    """symbol 级有效配置 = group 配置被 symbol 覆盖,extra_* 追加"""
    cfg = dict(group_cfg)
    for k in _INHERITABLE:
        if k in symbol:
            cfg[k] = symbol[k]
    cfg["telegram_chat_ids"] = list(dict.fromkeys(
        (cfg.get("telegram_chat_ids") or []) + (symbol.get("extra_telegram") or [])))
    cfg["email_recipients"] = list(dict.fromkeys(
        (cfg.get("email_recipients") or []) + (symbol.get("extra_email") or [])))
    return cfg


def _recipients_for(cfg: dict, channel: str) -> list[str]:
    if channel == "telegram":
        return [r for r in (cfg.get("telegram_chat_ids") or []) if r]
    if channel == "email":
        return [r for r in (cfg.get("email_recipients") or []) if r]
    return []


def expand_routes(defaults: dict, group: dict) -> list[dict]:
    """把一个 group 的配置展开成 notify_routes 行(组级 + symbol 级)。
    组级按接收人各自的 events 展开(每接收人一套事件类型)。"""
    gcfg = resolve_group_cfg(defaults, group)
    rows, seen = [], set()

    def add(symbol, channel, recipient, events):
        key = (symbol, channel, recipient)
        if recipient and key not in seen:
            seen.add(key)
            rows.append({
                "group_id": group["id"], "symbol": symbol, "channel": channel,
                "recipient": recipient, "event_types": list(events), "active": True,
            })

    # 组级路由(symbol = NULL)——按接收人各自的事件类型
    group_recips = normalized_recipients(gcfg)
    group_keys = {(r["channel"], r["recipient"]) for r in group_recips}
    for r in group_recips:
        add(None, r["channel"], r["recipient"], r["events"])

    # symbol 级路由:只为额外(extra_*)且组级没有的接收人建行,沿用组级 notify_on
    notify_on = list(gcfg.get("notify_on") or _DEFAULT_NOTIFY_ON)
    for sym in group.get("symbols") or []:
        for ch in ("telegram", "email"):
            extra = sym.get("extra_telegram" if ch == "telegram" else "extra_email") or []
            for rec in extra:
                if rec and (ch, rec) not in group_keys:
                    add(sym["ticker"], ch, rec, notify_on)
    return rows


def sync_to_db(db, cfg: dict | None = None):
    """YAML → groups / watchlist / notify_routes,整体覆盖(幂等,§12 sync-yaml)"""
    cfg = cfg or load_yaml()
    defaults = cfg.get("defaults") or {}
    groups = cfg.get("groups") or []

    db.query(NotifyRoute).delete()
    db.query(WatchlistItem).delete()
    db.query(Group).delete()

    for g in groups:
        gcfg = resolve_group_cfg(defaults, g)
        db.add(Group(id=g["id"], name=g.get("name", g["id"]),
                     description=g.get("description", ""),
                     config={**gcfg, "symbols": g.get("symbols") or []}))
        for sym in g.get("symbols") or []:
            db.add(WatchlistItem(
                symbol=sym["ticker"], group_id=g["id"],
                t212_ticker=sym.get("t212_ticker"),
                tags=sym.get("tags") or [],
                symbol_config={k: v for k, v in sym.items()
                               if k not in ("ticker", "t212_ticker", "tags")},
                active=True,
            ))
        for row in expand_routes(defaults, g):
            db.add(NotifyRoute(**row))
    db.flush()
    n_groups = len(groups)
    n_routes = db.query(NotifyRoute).count()
    log.info("watchlist synced: %d groups, %d routes", n_groups, n_routes)
    return {"groups": n_groups, "routes": n_routes}


def export_yaml(db) -> str:
    """数据库当前状态 → YAML 文本(§12 export-yaml)"""
    out = {"groups": []}
    for g in db.query(Group).order_by(Group.id).all():
        cfg = dict(g.config or {})
        symbols = []
        for w in (db.query(WatchlistItem)
                  .filter(WatchlistItem.group_id == g.id, WatchlistItem.active)
                  .order_by(WatchlistItem.symbol).all()):
            row = {"ticker": w.symbol, "t212_ticker": w.t212_ticker,
                   "tags": list(w.tags or [])}
            row.update(w.symbol_config or {})
            symbols.append(row)
        entry = {"id": g.id, "name": g.name, "description": g.description}
        entry.update({k: v for k, v in cfg.items() if k != "symbols"})
        entry["symbols"] = symbols
        out["groups"].append(entry)
    return yaml.safe_dump(out, allow_unicode=True, sort_keys=False)


# ─── 信号/采集流程用的便捷查询 ───

def active_symbols(db) -> list[dict]:
    """去重后的 watchlist(一个 symbol 可属多组,采集只跑一次)
    返回 [{symbol, t212_ticker, groups, t212_community, community_priority,
          news_auto, news_sources, news_types}]
    news_auto 为「任一组开启即开启」;news_sources/news_types 跨组取并集。
    """
    items = db.query(WatchlistItem).filter(WatchlistItem.active).all()
    groups = {g.id: g for g in db.query(Group).all()}
    by_sym: dict[str, dict] = {}
    for w in items:
        gcfg = (groups[w.group_id].config or {}) if w.group_id in groups else {}
        scfg = resolve_symbol_cfg(gcfg, {**(w.symbol_config or {}),
                                         "ticker": w.symbol})
        d = by_sym.setdefault(w.symbol, {
            "symbol": w.symbol, "t212_ticker": w.t212_ticker,
            "yf_symbol": (w.symbol_config or {}).get("yf_symbol") or w.symbol,
            "groups": [], "t212_community": False,
            "community_priority": scfg.get("community_priority", "all"),
            "news_auto": False, "news_sources": set(), "news_types": set(),
        })
        d["groups"].append(w.group_id)
        d["t212_ticker"] = d["t212_ticker"] or w.t212_ticker
        # 任一组开了社区采集即采集
        d["t212_community"] = d["t212_community"] or bool(scfg.get("t212_community"))
        # 任一组开了新闻自动拉取即开启;来源/类型跨组取并集
        if scfg.get("news_auto"):
            d["news_auto"] = True
        d["news_sources"].update(_norm_news_sources(scfg.get("news_sources")))
        d["news_types"].update(_norm_news_types(scfg.get("news_types")))
    out = []
    for d in by_sym.values():
        # set → 按规范顺序的有序列表,便于稳定展示/序列化
        d["news_sources"] = [s for s in NEWS_SOURCES if s in d["news_sources"]]
        d["news_types"] = [t for t in NEWS_TYPES if t in d["news_types"]]
        out.append(d)
    return out


def news_symbols(db) -> list[dict]:
    """仅返回开启了自动新闻拉取(news_auto=True)的标的及其 sources/types。
    供采集层(只拉这些标的)、精华生成、逐条情绪打分共用。"""
    return [d for d in active_symbols(db) if d.get("news_auto")]


def rss_macro_enabled(db) -> bool:
    """宏观 RSS 源全局开关(defaults.rss_macro,缺省 True)。"""
    g = db.query(Group).first()
    # defaults 不直接落库,统一以 group.config 解析后的值近似;无分组时默认开
    if not g:
        return True
    return bool((g.config or {}).get("rss_macro", True))
