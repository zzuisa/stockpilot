"""推送子系统:router 查 notify_routes 分发到 telegram/email(说明书 §11)"""

_router = None


def get_router():
    global _router
    if _router is None:
        from notify.email import EmailSender
        from notify.router import NotifyRouter
        from notify.telegram import TelegramSender
        _router = NotifyRouter(TelegramSender(), EmailSender())
    return _router
