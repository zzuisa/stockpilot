#!/usr/bin/env bash
# 启动 Futu OpenD(无头)。凭据来自环境变量(k8s Secret)：
#   FUTU_LOGIN_ACCOUNT   用户ID / 邮箱 / 手机号(手机号格式 "+86 13800138000")
#   FUTU_LOGIN_PWD_MD5   登录密码的 32 位 MD5(十六进制)  —— 二选一
#   FUTU_LOGIN_PWD       登录密码明文                      ——(有 MD5 时用 MD5)
# 首次从新 IP 登录会触发手机验证码：富途把验证码发到你手机，
# 用下面命令把验证码写进 OpenD 的 stdin：
#   kubectl -n stockpilot exec deploy/futu-opend -- sh -c 'echo <验证码> > /tmp/openin'
set -u
mkdir -p /data
FIFO=/tmp/openin
[ -p "$FIFO" ] || mkfifo "$FIFO"
# 常开一个写端，避免 OpenD 读到 EOF 导致控制台退出
sleep infinity > "$FIFO" &

ARGS=(ip=0.0.0.0 api_port="${FUTU_API_PORT:-11111}"
      telnet_ip=0.0.0.0 telnet_port="${FUTU_TELNET_PORT:-22222}"
      lang="${FUTU_LANG:-en}" log_level="${FUTU_LOG_LEVEL:-info}")
[ -n "${FUTU_LOGIN_ACCOUNT:-}" ] && ARGS+=(login_account="${FUTU_LOGIN_ACCOUNT}")
if [ -n "${FUTU_LOGIN_PWD_MD5:-}" ]; then
  ARGS+=(login_pwd_md5="${FUTU_LOGIN_PWD_MD5}")
elif [ -n "${FUTU_LOGIN_PWD:-}" ]; then
  ARGS+=(login_pwd="${FUTU_LOGIN_PWD}")
fi

cd /opt/opend
echo "[entrypoint] starting FutuOpenD (account=${FUTU_LOGIN_ACCOUNT:-<unset>}, api_port=${FUTU_API_PORT:-11111})"
echo "[entrypoint] 首次登录需手机验证码时：kubectl -n stockpilot exec deploy/futu-opend -- sh -c 'echo <code> > /tmp/openin'"
exec ./FutuOpenD "${ARGS[@]}" < "$FIFO"
