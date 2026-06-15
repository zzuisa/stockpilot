"""watchlist.yaml 分层解析与路由展开的离线测试(不需要数据库):
    cd app && python ../tests/test_config.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
os.environ["TELEGRAM_CHAT_ID"] = "111000111"

import config  # noqa: E402

DEFAULTS = {
    "notify_channels": ["telegram"],
    "telegram_chat_ids": ["111000111"],
    "email_recipients": [],
    "t212_community": True,
    "community_priority": "positive",
    "language": "zh",
}

GROUP = {
    "id": "core_holdings",
    "name": "核心持仓",
    "notify_channels": ["telegram", "email"],
    "telegram_chat_ids": ["111000111", "-100222"],
    "email_recipients": ["a@x.de", "b@x.de"],
    "notify_on": ["daily_report", "signal", "news_shock"],
    "symbols": [
        {"ticker": "NVDA", "t212_ticker": "NVDA_US_EQ",
         "extra_email": ["nvda@x.de"]},
        {"ticker": "MSFT", "t212_ticker": "MSFT_US_EQ"},
    ],
}


def test_group_overrides_defaults():
    g = config.resolve_group_cfg(DEFAULTS, GROUP)
    assert g["notify_channels"] == ["telegram", "email"]
    assert g["telegram_chat_ids"] == ["111000111", "-100222"]
    assert g["notify_on"] == ["daily_report", "signal", "news_shock"]
    assert g["community_priority"] == "positive"   # 继承 defaults


def test_symbol_extra_appends_not_replaces():
    g = config.resolve_group_cfg(DEFAULTS, GROUP)
    s = config.resolve_symbol_cfg(g, GROUP["symbols"][0])
    assert s["email_recipients"] == ["a@x.de", "b@x.de", "nvda@x.de"]
    assert s["telegram_chat_ids"] == ["111000111", "-100222"]


def test_route_expansion():
    rows = config.expand_routes(DEFAULTS, GROUP)
    group_level = [r for r in rows if r["symbol"] is None]
    sym_level = [r for r in rows if r["symbol"] == "NVDA"]
    # 组级: 2 个 TG + 2 个 email
    assert len(group_level) == 4
    # NVDA 的 extra_email 只追加一条 symbol 级路由
    assert len(sym_level) == 1
    assert sym_level[0]["channel"] == "email"
    assert sym_level[0]["recipient"] == "nvda@x.de"
    assert sym_level[0]["event_types"] == ["daily_report", "signal",
                                           "news_shock"]


def test_env_substitution():
    cfg = config._sub_env({"x": "${TELEGRAM_CHAT_ID}", "y": ["${NOPE_VAR}"]})
    assert cfg["x"] == "111000111"
    assert cfg["y"] == [""]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ✓ {name}")
    print("all config tests passed")
