"""★ SMTP 邮件推送(说明书 §11.3),aiosmtplib 异步发送"""
import logging
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import unescape

import aiosmtplib

import settings

log = logging.getLogger(__name__)


class EmailSender:
    def __init__(self):
        self.host = settings.SMTP_HOST
        self.port = settings.SMTP_PORT
        self.user = settings.SMTP_USER
        self.password = settings.SMTP_PASSWORD
        self.from_addr = settings.SMTP_FROM

    async def send(self, to: str, subject: str, body_html: str):
        if not settings.email_enabled:
            raise RuntimeError("SMTP_USER/SMTP_PASSWORD 未配置")
        msg = MIMEMultipart("alternative")
        msg["From"] = f"StockPilot <{self.from_addr}>"
        msg["To"] = to
        msg["Subject"] = subject
        # 纯文本 fallback
        plain = re.sub(r"<[^>]+>", "", unescape(body_html or ""))
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(body_html or plain, "html", "utf-8"))
        # 端口 465 = 隐式 TLS(SMTPS,连接即 TLS)；587/25 = STARTTLS(明文连接后升级)。
        # 二者互斥，用错会握手失败（之前 465 误用 start_tls 导致发送失败）。
        tls_kwargs = {"use_tls": True} if self.port == 465 else {"start_tls": True}
        await aiosmtplib.send(
            msg,
            hostname=self.host,
            port=self.port,
            username=self.user,
            password=self.password,
            timeout=30,
            **tls_kwargs,
        )
