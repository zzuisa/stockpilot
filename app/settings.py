"""环境变量集中读取。占位/空值的集成在各模块里自行跳过。"""
import os


def _get(name: str, default: str = "") -> str:
    v = os.environ.get(name, default).strip()
    # secrets.env 模板里的占位值视为未配置
    return "" if v.startswith("changeme") else v


# ─── 数据库 ───
DB_HOST = _get("DB_HOST", "localhost")
DB_PORT = _get("DB_PORT", "5432")
DB_NAME = _get("DB_NAME", "stockpilot")
DB_USER = _get("DB_USER", "sp")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "sp")

# ─── Trading212 ───
T212_API_KEY = _get("T212_API_KEY")
T212_API_SECRET = _get("T212_API_SECRET")
T212_ENV = _get("T212_ENV", "demo")

# ─── 行情/新闻 ───
FINNHUB_TOKEN = _get("FINNHUB_TOKEN")
# 优质财经 RSS（一/二线媒体为主，宏观面）。可用 RSS_FEEDS 环境变量整体覆盖。
RSS_FEEDS = [u for u in _get(
    "RSS_FEEDS",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html,"          # CNBC 财经
    "https://feeds.content.dowjones.io/public/rss/mw_topstories,"     # MarketWatch 头条
    "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain,"    # MarketWatch 市场
    "https://www.ft.com/rss/home,"                                    # Financial Times
    "https://seekingalpha.com/market_currents.xml",                   # Seeking Alpha
).split(",") if u.strip()]

# ─── Alpha Vantage 新闻情绪（专业源 + 内置相关度/情绪，免费档 25 req/day）───
ALPHAVANTAGE_API_KEY = _get("ALPHAVANTAGE_API_KEY")
alphavantage_enabled = bool(ALPHAVANTAGE_API_KEY)

# ─── SiliconFlow LLM (OpenAI-compatible) ───
SILICONFLOW_API_KEY = _get("SILICONFLOW_API_KEY")
SILICONFLOW_BASE_URL = _get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
SILICONFLOW_MODEL = _get("SILICONFLOW_MODEL", "Pro/deepseek-ai/DeepSeek-V3.2")
# 新闻精华 LLM 提示词外部覆盖文件(可选);为空则用 analysis/news_brief.py 内置提示词
NEWS_PROMPT_PATH = _get("NEWS_PROMPT_PATH")

# ─── Telegram ───
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")          # 主 chat,也是唯一带确认按钮的 chat
TELEGRAM_ADMIN_USER_ID = _get("TELEGRAM_ADMIN_USER_ID")
TELEGRAM_MODE = _get("TELEGRAM_MODE", "polling")     # polling | webhook
TELEGRAM_WEBHOOK_SECRET = _get("TELEGRAM_WEBHOOK_SECRET")
WEBHOOK_PUBLIC_URL = _get("WEBHOOK_PUBLIC_URL", "https://roguelife.de/stockpilot/webhook/telegram")

# ─── SMTP ───
SMTP_HOST = _get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(_get("SMTP_PORT", "587") or 587)
SMTP_USER = _get("SMTP_USER")
SMTP_PASSWORD = _get("SMTP_PASSWORD")
SMTP_FROM = _get("SMTP_FROM") or SMTP_USER

# ─── 风控(§19) ───
RISK_MAX_ORDER_EUR = float(_get("RISK_MAX_ORDER_EUR", "200") or 200)
RISK_MAX_POSITION_PCT = float(_get("RISK_MAX_POSITION_PCT", "15") or 15)
RISK_DAILY_LOSS_LIMIT_EUR = float(_get("RISK_DAILY_LOSS_LIMIT_EUR", "100") or 100)
DEFAULT_ORDER_VALUE_EUR = float(_get("DEFAULT_ORDER_VALUE_EUR", "150") or 150)
INTENT_TTL_MINUTES = int(_get("INTENT_TTL_MINUTES", "30") or 30)

# ─── 其他 ───
ROOT_PATH = os.environ.get("ROOT_PATH", "")
WATCHLIST_PATH = _get("WATCHLIST_PATH", "/app/config/watchlist.yaml")
AUTO_BACKFILL = _get("AUTO_BACKFILL", "true").lower() == "true"
TIMEZONE = "Europe/Berlin"

t212_enabled = bool(T212_API_KEY)
finnhub_enabled = bool(FINNHUB_TOKEN)
llm_enabled = bool(SILICONFLOW_API_KEY)
telegram_enabled = bool(TELEGRAM_BOT_TOKEN)
email_enabled = bool(SMTP_USER and SMTP_PASSWORD)
