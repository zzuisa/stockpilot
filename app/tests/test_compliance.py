"""合规护栏单测（纯函数，无外部依赖）。运行：python3 -m pytest app/tests/test_compliance.py
或直接 python3 app/tests/test_compliance.py。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis.compliance import DISCLAIMER, enforce_compliance, has_advice_language


def test_detects_advice_language():
    assert has_advice_language("我建议买入 NVDA")
    assert has_advice_language("应该卖出，全仓梭哈")
    assert not has_advice_language("bear/base/bull 情景区间：40 / 55 / 70")


def test_advice_text_is_annotated_and_disclaimed():
    out = enforce_compliance("综合看，建议买入并加仓。")
    assert "情景分析" in out and "非投资建议" in out   # 被标注为情景
    assert DISCLAIMER in out                          # 附免责


def test_clean_text_only_gets_disclaimer():
    out = enforce_compliance("现价 55，均价目标 62，情景区间 45/55/70。")
    assert not out.startswith("⚠")                    # 无建议措辞→不加警示头
    assert DISCLAIMER in out


def test_existing_disclaimer_not_duplicated():
    out = enforce_compliance("这是一段已含免责声明的文本。")
    assert out.count("免责") == 1                      # 不重复追加


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); n += 1; print(f"  ✓ {name}")
    print(f"\n全部 {n} 个合规单测通过。")
