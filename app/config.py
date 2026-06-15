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
    "news_sources", "t212_community", "community_priority", "language",
]
_DEFAULT_NOTIFY_ON = ["daily_report", "signal"]


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
    return cfg


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
    """把一个 group 的 YAML 配置展开成 notify_routes 行(组级 + symbol 级)"""
    gcfg = resolve_group_cfg(defaults, group)
    notify_on = gcfg["notify_on"]
    rows, seen = [], set()

    def add(symbol, channel, recipient):
        key = (symbol, channel, recipient)
        if recipient and key not in seen:
            seen.add(key)
            rows.append({
                "group_id": group["id"], "symbol": symbol, "channel": channel,
                "recipient": recipient, "event_types": notify_on, "active": True,
            })

    channels = gcfg.get("notify_channels") or []
    if "both" in channels:
        channels = ["telegram", "email"]
    # 组级路由(symbol = NULL)
    for ch in channels:
        for r in _recipients_for(gcfg, ch):
            add(None, ch, r)
    # symbol 级路由:只为与组级结果有差异的部分建行
    for sym in group.get("symbols") or []:
        scfg = resolve_symbol_cfg(gcfg, sym)
        sch = scfg.get("notify_channels") or []
        if "both" in sch:
            sch = ["telegram", "email"]
        for ch in sch:
            group_level = set(_recipients_for(gcfg, ch)) if ch in channels else set()
            for r in _recipients_for(scfg, ch):
                if r not in group_level:
                    add(sym["ticker"], ch, r)
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
    返回 [{symbol, t212_ticker, groups:[gid...], t212_community, community_priority}]
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
        })
        d["groups"].append(w.group_id)
        d["t212_ticker"] = d["t212_ticker"] or w.t212_ticker
        # 任一组开了社区采集即采集
        d["t212_community"] = d["t212_community"] or bool(scfg.get("t212_community"))
    return list(by_sym.values())
