"""富途 OpenAPI skills 运行封装(subprocess 调 app/skills/futuapi 脚本)。

需要:
- settings.FUTU_ENABLED=true
- OpenD 网关在 FUTU_OPEND_HOST:FUTU_OPEND_PORT 可达(在别处运行并登录富途账号)
- futu-api SDK(见 requirements)

不可用(未开启/连不上/超时/报错)时所有封装返回 {"unavailable": true, ...}，调用方降级。
"""
import json
import logging
import os
import socket
import subprocess
import sys
import time

import settings

log = logging.getLogger(__name__)

_reach_cache = {"ts": 0.0, "ok": False}
_REACH_TTL = 60.0


def available() -> bool:
    """FUTU_ENABLED 且 OpenD 端口可 TCP 连通(2s，60s 缓存)。"""
    if not settings.FUTU_ENABLED:
        return False
    now = time.time()
    if now - _reach_cache["ts"] < _REACH_TTL:
        return _reach_cache["ok"]
    ok = False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect((settings.FUTU_OPEND_HOST, settings.FUTU_OPEND_PORT))
        ok = True
    except OSError as e:
        log.debug("futu OpenD 不可达 %s:%s: %s",
                  settings.FUTU_OPEND_HOST, settings.FUTU_OPEND_PORT, e)
    finally:
        try:
            s.close()
        except Exception:
            pass
    _reach_cache.update(ts=now, ok=ok)
    return ok


def run_skill(script_rel: str, *args, timeout: int = 40) -> dict:
    """跑 skills/futuapi/scripts/<script_rel>(自动加 --json)，返回解析后的 dict。
    script_rel 例: 'quote/get_search_news.py'。不可用/失败 → {'unavailable': True}。"""
    if not available():
        return {"unavailable": True, "reason": "OpenD 不可达或 FUTU_ENABLED 未开启"}
    script = os.path.join(settings.FUTU_SKILLS_DIR, "scripts", script_rel)
    if not os.path.isfile(script):
        return {"unavailable": True, "reason": f"脚本不存在: {script}"}
    env = dict(os.environ)
    env.setdefault("FUTU_OPEND_HOST", settings.FUTU_OPEND_HOST)
    env.setdefault("FUTU_OPEND_PORT", str(settings.FUTU_OPEND_PORT))
    if settings.FUTU_LOGIN_ACCOUNT:
        env.setdefault("FUTU_LOGIN_ACCOUNT", settings.FUTU_LOGIN_ACCOUNT)
    if settings.FUTU_LOGIN_PWD:
        env.setdefault("FUTU_LOGIN_PWD", settings.FUTU_LOGIN_PWD)
    cmd = [sys.executable, script, *[str(a) for a in args], "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=env)
        out = (r.stdout or "").strip()
        # 脚本正常时最后一行为 JSON；取最后一个非空行解析
        line = out.splitlines()[-1] if out else ""
        data = json.loads(line) if line else {}
        if r.returncode != 0 and not data:
            return {"unavailable": True, "reason": (r.stderr or out)[:300]}
        return data
    except subprocess.TimeoutExpired:
        return {"unavailable": True, "reason": f"skill 超时({timeout}s)"}
    except Exception as e:
        log.warning("run_skill %s 失败: %s", script_rel, e)
        return {"unavailable": True, "reason": str(e)[:300]}


# ─── 便捷封装 ───

def search_news(keyword: str, sub_type: str = "NEWS", n: int = 15) -> dict:
    return run_skill("quote/get_search_news.py", keyword,
                     "--max-count", n, "--news-sub-type", sub_type)


def snapshot(code: str) -> dict:
    return run_skill("quote/get_snapshot.py", code)


def rating_summary(code: str) -> dict:
    return run_skill("quote/get_research_rating_summary.py", code)


def capital_flow(code: str) -> dict:
    return run_skill("quote/get_capital_flow.py", code)


def company_profile(code: str) -> dict:
    return run_skill("quote/get_company_profile.py", code)
