# Cloudflyer

Cloudflyer is a Python service that helps solve various web security challenges including Cloudflare challenges, Turnstile captchas, and reCAPTCHA Invisible.

## Features

- Cloudflare challenge solver
- Turnstile captcha solver
- reCAPTCHA Invisible solver
- Proxy support (HTTP/SOCKS)
- Concurrent task processing
- RESTful API server

## Installation

```bash
pip install cloudflyer
```

## Quick Start

### Example Script

```bash
# Run example cloudflare solving with proxy
python test.py cloudflare -x socks5://127.0.0.1:1080

# Run example turnstile solving
python test.py turnstile

# Run example recaptcha invisible solving
python test.py recaptcha
```

### Solver Server

```bash
cloudflyer -K YOUR_CLIENT_KEY
```

Options:
- `-K, --clientKey`: Client API key (required)
- `-M, --maxTasks`: Maximum concurrent tasks (default: 1)
- `-P, --port`: Server listen port (default: 3000)
- `-H, --host`: Server listen host (default: localhost)
- `-T, --timeout`: Maximum task timeout in seconds (default: 120)

### Docker

Docker:

```bash
docker run -it --rm -p 3000:3000 jackzzs/cloudflyer -K YOUR_CLIENT_KEY
```

Docker Compose:

```yaml
services:
  cloudflyer:
    build: .
    image: cloudflyer:latest
    container_name: cloudflyer
    ports:
      - 3000:3000
    command: >
      -K "YOUR_API_KEY" 
      -H "0.0.0.0"
```

## API Endpoints for Server

### Create Task

Request:

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

1. Field `userAgent` and `proxy` is optional.
2. Supported task types: `CloudflareChallenge`, `Turnstile`, `RecaptchaInvisible`
3. For `Turnstile` task, `siteKey` is required.
4. For `RecaptchaInvisible` task, `siteKey` and `action` is required.
5. For `CloudflareChallenge` task, set `content` to true to get page html in `response`.

Response:

```
{
    "taskId": "21dfdca6-fbf5-4313-8ffa-cfc4b8483cc7"
}
```

### Get Task Result

Request:

```
POST /getTaskResult
Content-Type: application/json
{
  "clientKey": "your_client_key",
  "taskId": "21dfdca6-fbf5-4313-8ffa-cfc4b8483cc7"
}
```

Response:

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

For `Turnstile` task:

```
"response": {
    "token": "..."
}
```

For `CloudflareChallenge` tasks:

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

For `RecaptchaInvisible` tasks:

```
"response": {
    "token": "..."
}
```

## WSSocks

[WSSocks](https://github.com/zetxtech/wssocks) is a socks proxy agent for intranet penetration via websocket. It can be used for connecting to the user's network.

For agent proxy:

```bash
wssocks server -r -t example_token -a -dd
```

For user side (using proxy):

```bash
wssocks client -u https://ws.zetx.tech -r -t example_token -T 1 -c example_connector_token -dd -E -x socks5://127.0.0.1:1080
```

For solver side:

```
POST /createTask
Content-Type: application/json

{
  "clientKey": "your_client_key",
  "type": "CloudflareChallenge",
  "url": "https://example.com",
  "userAgent": "...",
  "wssocks": {
    "url": "https://ws.zetx.tech",
    "token": "example_connector_token"
  }
}
```
