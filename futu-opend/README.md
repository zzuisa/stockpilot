# Futu OpenD 网关（集群内无头部署）

stockpilot-app 通过集群内 `futu-opend:11111` 使用富途行情/资讯能力（非美股优质信息源）。
OpenD 需登录你的富途账号；首次从新 IP 登录会触发一次**手机验证码**。

## 一次性上线步骤

### 1) 构建并导入镜像（本机 microk8s）
```bash
cd apps/stockpilot
docker build --network=host -t futu-opend:v1 futu-opend
docker save futu-opend:v1 -o /tmp/futu-opend.tar
microk8s ctr image import /tmp/futu-opend.tar && rm /tmp/futu-opend.tar
microk8s kubectl apply -f k8s/futu-opend.yaml   # 建 PVC/Service/Deployment(replicas=0)
```
> 镜像 tag 用固定 `v1`；重建换新代码时改 tag 或先 `kubectl rollout restart`。

### 2) 写登录凭据到 Secret（不进 git、不在聊天明文）
密码用 **MD5(32位小写十六进制)**：
```bash
PWD_MD5=$(printf '%s' '你的登录密码' | md5sum | awk '{print $1}')
microk8s kubectl -n stockpilot create secret generic futu-opend-secret \
  --from-literal=FUTU_LOGIN_ACCOUNT='你的账号(用户ID/邮箱/手机号)' \
  --from-literal=FUTU_LOGIN_PWD_MD5="$PWD_MD5" \
  --dry-run=client -o yaml | microk8s kubectl apply -f -
```
> 手机号格式：`+86 13800138000`（含区号与空格）。

### 3) 启动并过手机验证
```bash
microk8s kubectl -n stockpilot scale deploy/futu-opend --replicas=1
microk8s kubectl -n stockpilot logs -f deploy/futu-opend      # 看是否要验证码
```
日志提示需要验证码时（富途已把验证码发到你手机）：
```bash
microk8s kubectl -n stockpilot exec deploy/futu-opend -- sh -c 'echo 你收到的验证码 > /tmp/openin'
```
登录成功后日志会显示已连接/行情就绪；`/data`(PVC) 持久化登录态，普通重启不必再验证
（**换节点/换 IP 可能需要重验**）。

### 4) 开启 app 使用富途
在 `secrets.env` 设 `FUTU_ENABLED=true`（`FUTU_OPEND_HOST/PORT` 已在 app.yaml 默认指向
`futu-opend:11111`，无需再填），然后：
```bash
./deploy.sh secrets      # 更新 stockpilot-secrets 并滚动重启 app
```
非美股（港股/A股/日股/德股关键词）即开始走富途资讯 + Qwen 研究员。

## 排障
- `available()` 只探测 11111 端口通不通；端口通但未登录时查询会失败并优雅降级。
- 连不上/未登录：app 自动回落原有源（美股不受影响）。
- 看 OpenD 状态：`microk8s kubectl -n stockpilot logs deploy/futu-opend --tail=100`
- 关闭：`microk8s kubectl -n stockpilot scale deploy/futu-opend --replicas=0`
