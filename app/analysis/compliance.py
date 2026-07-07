"""合规护栏（[enforce in code]）——纯函数，无外部依赖，便于单测。
研究 Agent 的输出必须：不含买卖/仓位建议措辞（命中则显式标注为情景分析）、附免责声明。
"""
import re

DISCLAIMER = ("以上为基于公开数据的情景分析，非投资建议，不构成买卖或仓位建议；"
              "作者非持牌投顾。历史与共识数据可能滞后，请自行核实。")

# 买卖/仓位建议类措辞（命中则加标注 + 免责，不静默改写数字）
_ADVICE_PAT = re.compile(r"(建议(买入|卖出|加仓|减仓|清仓|抄底))|(强烈(买入|卖出|推荐))|(应该(买|卖))|全仓|梭哈")


def has_advice_language(text: str) -> bool:
    """是否含买卖/仓位建议措辞。"""
    return bool(_ADVICE_PAT.search(text or ""))


def enforce_compliance(text: str) -> str:
    """命中买卖/仓位建议措辞则加显式情景标注；始终附免责声明。"""
    out = text or ""
    if _ADVICE_PAT.search(out):
        out = "⚠ 以下为情景分析，非投资建议（原文含疑似建议措辞，已按情景口径理解）：\n" + out
    if DISCLAIMER not in out and "免责" not in out:   # 已有免责则不重复
        out = out.rstrip() + "\n\n— " + DISCLAIMER
    return out
