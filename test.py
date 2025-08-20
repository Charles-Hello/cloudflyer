import logging
from threading import Thread
import threading
import time
import argparse
import json

from curl_cffi import requests

from cloudflyer.log import apply_logging_adapter
from cloudflyer.server import main, stop_instances

CLIENT_KEY = "example_token"
EXAMPLE_TOKEN = "example_token"
BASE_URL = "http://127.0.0.1:3000"

def verify_cloudflare_challenge(result):
    try:
        # Extract necessary information from response
        cookies = result["response"]["cookies"]
        headers = result["response"]["headers"]
        url = result["data"]["url"]

        # Use curl_cffi to send request
        response = requests.get(
            url,
            cookies=cookies,
            headers={"User-Agent": headers["User-Agent"]},
            impersonate="chrome",
            allow_redirects=True,
        )

        # Check if response contains success marker
        return "Captcha is passed successfully!" in response.text
    except (KeyError, TypeError):
        return False


def create_task(data):
    headers = {"Content-Type": "application/json"}
    response = requests.post(f"{BASE_URL}/createTask", json=data, headers=headers)
    return response.json()


def get_task_result(task_id, client_key=EXAMPLE_TOKEN):
    headers = {"Content-Type": "application/json"}

    data = {"clientKey": client_key, "taskId": task_id}

    response = requests.post(
        f"{BASE_URL}/getTaskResult",
        json=data,
        headers=headers,
    )
    return response.json()


def start_server():
    ready = threading.Event()
    t = Thread(
        target=main,
        kwargs={
            "argl": ["-K", CLIENT_KEY],
            "ready": ready,
            "log": False,
        },
        daemon=True,
    )
    t.start()
    ready.wait()
    return t


def poll_task_result(task_id, client_key) -> dict:
    while True:
        cf_response = get_task_result(task_id, client_key)
        if cf_response["status"] == "completed":
            return cf_response["result"]
        time.sleep(3)


def cloudflare_challenge(proxy=None):
    start_server()

    data = {
        "clientKey": EXAMPLE_TOKEN,
        "type": "CloudflareChallenge",
        "url": "https://2captcha.com/demo/cloudflare-turnstile-challenge",
        "userAgent": "CFNetwork/897.15 Darwin/17.5.0 (iPhone/6s iOS/11.3)",
    }
    
    # Add proxy configuration if provided
    if proxy:
        data["proxy"] = proxy

    task_info = create_task(data)
    result = poll_task_result(task_info["taskId"], data["clientKey"])
    print(f"Challenge result:\n{json.dumps(result, indent=2)}")

    success = verify_cloudflare_challenge(result)
    print(f"\nChallenge verification result:\n{success}")


def turnstile(proxy=None):
    start_server()

    data = {
        "clientKey": EXAMPLE_TOKEN,
        "type": "Turnstile",
        "url": "https://www.coronausa.com",
        "siteKey": "0x4AAAAAAAH4-VmiV_O_wBN-",
    }

    # Add proxy configuration if provided
    if proxy:
        data["proxy"] = proxy

    print("Task:")
    print(json.dumps(data, indent=2))
    task_info = create_task(data)
    result = poll_task_result(task_info["taskId"], data["clientKey"])
    print(f"Turnstile result:\n{json.dumps(result, indent=2)}")


    response = result.get("response")
    if response:
        token = response.get("token")
    else:
        token = None
    if token:
        print(f"\nTurnstile token:\n{token}")

def recapcha_invisible(proxy=None):
    start_server()

    data = {
        "clientKey": EXAMPLE_TOKEN,
        "type": "RecaptchaInvisible",
        "url": "https://antcpt.com/score_detector",
        "siteKey": "6LcR_okUAAAAAPYrPe-HK_0RULO1aZM15ENyM-Mf",
        "action": "homepage",
    }

    # Add proxy configuration if provided
    if proxy:
        data["proxy"] = proxy

    task_info = create_task(data)
    result = poll_task_result(task_info["taskId"], data["clientKey"])
    print(f"Challenge result:\n{json.dumps(result, indent=2)}")

def parse_proxy_string(proxy_str):
    """Parse proxy string in format scheme://host:port"""
    if not proxy_str:
        return None
    
    try:
        scheme, rest = proxy_str.split('://')
        host, port = rest.split(':')
        return {
            "scheme": scheme,
            "host": host,
            "port": int(port)
        }
    except (ValueError, AttributeError):
        raise ValueError("Invalid proxy format. Use scheme://host:port (e.g., socks5://127.0.0.1:1080)")

def main_cli():
    parser = argparse.ArgumentParser(description="Challenge solver CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Add proxy argument to each command
    for cmd in ["turnstile", "cloudflare", "recaptcha"]:
        parser_cmd = subparsers.add_parser(cmd, help=f"Solve {cmd.replace('_', ' ')} challenge")
        parser_cmd.add_argument("-x", "--proxy", help="Proxy in format scheme://host:port (e.g., socks5://127.0.0.1:1080)")
        parser_cmd.add_argument("-v", "--verbose", help="Show verbose output", action="store_true")

    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return

    # Parse proxy string if provided
    proxy = parse_proxy_string(args.proxy) if args.proxy else None
    
    if args.verbose:
        apply_logging_adapter([
            ('http.*->.*', logging.DEBUG),
            ('server disconnect', logging.DEBUG),
            ('client disconnect', logging.DEBUG),
            ('server connect', logging.DEBUG),
            ('client connect', logging.DEBUG),
        ], level=10)
        logging.getLogger('hpack').setLevel(logging.WARNING)

    # Execute corresponding function based on command
    try:
        if args.command == "turnstile":
            turnstile(proxy)
        elif args.command == "cloudflare":
            cloudflare_challenge(proxy)
        elif args.command == "recaptcha":
            recapcha_invisible(proxy)
        else:
            parser.print_help()
    finally:
        stop_instances()

if __name__ == "__main__":
    main_cli()
