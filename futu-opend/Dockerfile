# Futu OpenD 网关(命令行/无头版) —— 供 stockpilot-app 通过 futu-opend:11111 连接。
# 登录凭据由 k8s Secret 注入(见 k8s/futu-opend.yaml)；首次登录的手机验证码经 stdin FIFO 注入。
FROM ubuntu:22.04 AS fetch
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /dl
# 下载官方最新 Ubuntu 版，抽取“命令行版”目录(含 FutuOpenD + 自带 .so + Languages)
RUN curl -fL -o od.tgz "https://www.futunn.com/download/fetch-lasted-link?name=opend-ubuntu" \
 && mkdir -p /tmp/od /opt/opend \
 && tar -xzf od.tgz -C /tmp/od \
 && cli="$(dirname "$(find /tmp/od -maxdepth 3 -name FutuOpenD -type f | head -1)")" \
 && cp -a "$cli"/. /opt/opend/ \
 && rm -rf /tmp/od od.tgz

FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates libidn2-0 tzdata netcat-openbsd && rm -rf /var/lib/apt/lists/*
COPY --from=fetch /opt/opend /opt/opend
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /opt/opend/FutuOpenD
ENV LD_LIBRARY_PATH=/opt/opend TZ=Europe/Berlin HOME=/data
EXPOSE 11111 22222
ENTRYPOINT ["/entrypoint.sh"]
