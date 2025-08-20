# Cloudflyer

Cloudflyer 是一个 Python 服务，用于解决各种网络安全挑战，包括 Cloudflare 挑战、Turnstile 验证码和 reCAPTCHA Invisible。

[中文文档 / Chinese README](README_zh.md)

## 功能特性

- Cloudflare 挑战解决器
- Turnstile 验证码解决器
- reCAPTCHA Invisible 解决器
- 代理支持 (HTTP/SOCKS)
- 并发任务处理
- RESTful API 服务器

## 安装

```bash
pip install cloudflyer
```

## 快速开始

### 示例脚本

```bash
# 使用代理运行 Cloudflare 解决示例
python test.py cloudflare -x socks5://127.0.0.1:1080

# 运行 Turnstile 解决示例
python test.py turnstile

# 运行 reCAPTCHA Invisible 解决示例
python test.py recaptcha
```

### 解决器服务器

```bash
cloudflyer -K YOUR_CLIENT_KEY
```

选项：
- `-K, --clientKey`：客户端 API 密钥（必需）。这是你自己定义的秘密字符串——**不是 Cloudflare 或任何第三方服务签发的**——必须在每个 API 调用中（在 `clientKey` 字段中）提供，以便服务器可以进行身份验证和处理请求。
- `-M, --maxTasks`：最大并发任务数（默认：1）
- `-P, --port`：服务器监听端口（默认：3000）
- `-H, --host`：服务器监听主机（默认：localhost）
- `-T, --timeout`：最大任务超时时间（秒）（默认：120）

### Docker

Docker：

```bash
docker run -it --rm -p 3000:3000 jackzzs/cloudflyer -K YOUR_CLIENT_KEY
```

Docker Compose：

```yaml
services:
  cloudflyer:
    image: jackzzs/cloudflyer
    container_name: cloudflyer
    ports:
      - 3000:3000
    command: >
      -K "YOUR_API_KEY" 
      -H "0.0.0.0"
```

## 服务器 API 端点

### 创建任务

请求：

```
POST /createTask
Content-Type: application/json

{
  "clientKey": "your_client_key",
  "type": "CloudflareChallenge",
  "url": "https://example.com",
  "userAgent": "...",
  "proxy": {
    "scheme": "socks5",
    "host": "127.0.0.1",
    "port": 1080
  },
  "content": false
}
```

1. 字段 `userAgent` 和 `proxy` 是可选的。
2. 支持的任务类型：`CloudflareChallenge`、`Turnstile`、`RecaptchaInvisible`
3. 对于 `Turnstile` 任务，需要 `siteKey`。
4. 对于 `RecaptchaInvisible` 任务，需要 `siteKey` 和 `action`。
5. 对于 `CloudflareChallenge` 任务，将 `content` 设置为 true 以在 `response` 中获取页面 html。

响应：

```
{
    "taskId": "21dfdca6-fbf5-4313-8ffa-cfc4b8483cc7"
}
```

### 获取任务结果

请求：

```
POST /getTaskResult
Content-Type: application/json
{
  "clientKey": "your_client_key",
  "taskId": "21dfdca6-fbf5-4313-8ffa-cfc4b8483cc7"
}
```

响应：

```
{
    "status": "completed",
    "result": {
        "success": true,
        "code": 200,
        "response": {
            ...
        },
        "data": {
            "type": "CloudflareChallenge",
            ...(input)
        }
    }
}
```

对于 `Turnstile` 任务：

```
"response": {
    "token": "..."
}
```

对于 `CloudflareChallenge` 任务：

```
"response": {
    "cookies": {
        "cf_clearance": "..."
    },
    "headers": {
        "User-Agent": "..."
    },
    "content": "..."
}
```

对于 `RecaptchaInvisible` 任务：

```
"response": {
    "token": "..."
}
```

## LinkSocks

[LinkSocks](https://github.com/linksocks/linksocks) 是一个通过 websocket 进行内网穿透的 socks 代理代理。它可以用于连接到用户的网络。

对于代理端：

```bash
linksocks server -r -t example_token -a -dd
```

对于用户端（使用代理）：

```bash
linksocks client -u https://ws.zetx.tech -r -t example_token -T 1 -c example_connector_token -dd -E -x socks5://127.0.0.1:1080
```

对于解决器端：

```
POST /createTask
Content-Type: application/json

{
  "clientKey": "your_client_key",
  "type": "CloudflareChallenge",
  "url": "https://example.com",
  "userAgent": "...",
  "linksocks": {
    "url": "https://ws.zetx.tech",
    "token": "example_connector_token"
  }
}
```