import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from loguru import logger
from pusher import WeChat, requests, sio


APP_NAME = "com.xiaomi.hm.health"
APP_VERSION = "6.5.5"
CLIENT_ID = "HuaMi"
REDIRECT_URI = "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html"
LOGIN_URL_TEMPLATE = "https://api-user.huami.com/registrations/{account}/tokens"
TOKEN_URL = "https://account.huami.com/v2/client/login"
APP_TOKEN_URL = "https://account-cn.huami.com/v1/client/app_tokens"
UPLOAD_URL = "https://api-mifit-cn2.huami.com/v1/data/band_data.json"
DN = "api-user.huami.com,api-mifit.huami.com,app-analytics.huami.com"

logger.remove()
logger.add(sys.stderr, diagnose=False)


@dataclass
class ZeppAccount:
    username: str
    password: str
    steps: int


def mask_account(username):
    if len(username) <= 4:
        return "*" * len(username)
    return f"{username[:3]}****{username[-4:]}"


def format_account(username):
    username = username.strip()
    if re.fullmatch(r"\d+", username):
        return f"+86{username}"
    return username


def getenv_list(name):
    value = os.getenv(name, "").strip()
    return value.split(",") if value else []


def get_target_steps(value):
    value = (value or "random").strip().lower()
    if value == "random":
        min_steps = int(os.getenv("ZEPP_STEP_MIN", "18000"))
        max_steps = int(os.getenv("ZEPP_STEP_MAX", "28000"))
        return random.randint(min_steps, max_steps)
    return int(value)


def parse_accounts():
    raw_accounts = os.getenv("ZEPP_ACCOUNTS", "").strip()
    if not raw_accounts:
        return []

    accounts = []
    for line in raw_accounts.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            raise ValueError("ZEPP_ACCOUNTS 每行至少需要：账号,密码")
        username, password = parts[:2]
        steps = get_target_steps(parts[2] if len(parts) >= 3 else "random")
        accounts.append(ZeppAccount(username=username, password=password, steps=steps))
    return accounts


def get_access_code(location):
    params = parse_qs(urlparse(location or "").query)
    access_values = params.get("access")
    if not access_values:
        raise RuntimeError(f"Zepp 登录失败：未从重定向地址中获取 access code，{location_summary(location)}")
    return access_values[0]


def location_summary(location):
    if not location:
        return "location 为空"
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    safe_params = {}
    for key in ("error", "error_code", "error_description", "message", "msg", "state"):
        if key in params:
            safe_params[key] = params[key][0]
    if safe_params:
        return f"location 参数：{safe_params}"
    safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return f"location={safe_url}, query_keys={list(params.keys())}"


def response_summary(response):
    content_type = response.headers.get("Content-Type", "")
    preview = response.text[:120].replace("\n", " ").replace("\r", " ")
    return f"status={response.status_code}, content-type={content_type}, body={preview}"


def login(session, username, password):
    account = format_account(username)
    url = LOGIN_URL_TEMPLATE.format(account=account)
    data = {
        "client_id": CLIENT_ID,
        "password": password,
        "redirect_uri": REDIRECT_URI,
        "token": "access",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_7_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2",
    }
    response = session.post(url, data=data, headers=headers, timeout=15, allow_redirects=False)
    response.raise_for_status()
    location = response.headers.get("Location")
    if not location:
        raise RuntimeError(f"Zepp 登录失败：未返回重定向地址，{response_summary(response)}")
    code = get_access_code(location)

    params = {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "code": code,
        "country_code": "CN",
        "device_id": "2C8B4939-0CCD-4E94-8CBA-CB8EA6E613A1",
        "device_model": "phone",
        "dn": DN,
        "grant_type": "access_token",
        "lang": "zh_CN",
        "os_version": "1.5.0",
        "source": APP_NAME,
        "third_name": "huami_phone" if account.startswith("+") else "email",
    }
    response = session.get(TOKEN_URL, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    try:
        result = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(f"Zepp token 获取失败：返回内容不是 JSON，{response_summary(response)}") from exc
    token_info = result.get("token_info", {})
    login_token = token_info.get("login_token")
    user_id = result.get("user_id")
    if not login_token or not user_id:
        raise RuntimeError(f"Zepp login_token 获取失败：{result}")
    return login_token, user_id


def get_app_token(session, login_token):
    headers = {
        "User-Agent": f"MiFit/{APP_VERSION} (iPhone; iOS 14.0.1; Scale/2.00)",
    }
    params = {
        "app_name": APP_NAME,
        "dn": DN,
        "login_token": login_token,
    }
    response = session.get(APP_TOKEN_URL, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    try:
        result = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(f"Zepp app_token 获取失败：返回内容不是 JSON，{response_summary(response)}") from exc
    app_token = result.get("token_info", {}).get("app_token")
    if not app_token:
        raise RuntimeError(f"Zepp app_token 获取失败：{result}")
    return app_token


def build_step_payload(user_id, steps):
    today = datetime.now().strftime("%Y-%m-%d")
    timestamp = int(time.time() * 1000)
    summary = {
        "v": 5,
        "slp": {
            "st": 0,
            "ed": 0,
            "dp": 0,
            "lt": 0,
            "wk": 0,
            "usrSt": -1440,
            "usrEd": -1440,
            "wc": 0,
            "is": 0,
            "lb": 0,
            "to": 0,
            "dt": 0,
            "rhr": 0,
            "ss": 0,
        },
        "stp": {
            "ttl": steps,
            "dis": steps * 75,
            "cal": int(steps * 0.04),
            "wk": 0,
            "rn": 0,
            "runDist": 0,
            "runCal": 0,
            "stage": [],
        },
        "goal": 8000,
    }
    data_json = [
        {
            "date": today,
            "summary": summary,
            "source": 24,
            "type": 0,
            "tz": "Asia/Shanghai",
        }
    ]
    return {
        "userid": user_id,
        "last_sync_data_time": timestamp,
        "device_type": "0",
        "last_deviceid": "DA932FFFFE8816E7",
        "data_json": data_json,
    }


def upload_steps(session, login_token, app_token, user_id, steps):
    payload = build_step_payload(user_id, steps)
    headers = {
        "apptoken": app_token,
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": f"MiFit/{APP_VERSION} ({APP_NAME})",
    }
    data = {
        "userid": payload["userid"],
        "last_sync_data_time": payload["last_sync_data_time"],
        "device_type": payload["device_type"],
        "last_deviceid": payload["last_deviceid"],
        "data_json": json.dumps(payload["data_json"], ensure_ascii=False),
    }
    response = session.post(UPLOAD_URL, headers=headers, params={"login_token": login_token}, data=data, timeout=15)
    response.raise_for_status()
    try:
        result = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(f"Zepp 步数上传失败：返回内容不是 JSON，{response_summary(response)}") from exc
    if result.get("code") not in (1, "1", 200, "200"):
        raise RuntimeError(f"Zepp 步数上传失败：{result}")
    return result


def main():
    wechat_params = getenv_list("WECHAT_PARAMS")
    pusher = WeChat("Zepp 步数", wechat_params) if wechat_params else None
    dry_run = os.getenv("ZEPP_DRY_RUN", "").strip() == "1"
    accounts = parse_accounts()
    if not accounts:
        logger.info("未配置 ZEPP_ACCOUNTS，跳过 Zepp 步数同步")
        return

    success = False
    for account in accounts:
        masked = mask_account(account.username)
        try:
            if dry_run:
                sio.write(f"Zepp 步数提示：{masked} dry-run，目标步数 {account.steps}\n")
                success = True
                continue
            with requests.Session() as session:
                login_token, user_id = login(session, account.username, account.password)
                app_token = get_app_token(session, login_token)
                upload_steps(session, login_token, app_token, user_id, account.steps)
            sio.write(f"Zepp 步数提示：{masked} 同步成功，步数 {account.steps}\n")
            success = True
        except Exception as exc:
            sio.write(f"Zepp 步数提示：{masked} 同步失败：{exc}\n")
            logger.error(f"Zepp 步数同步失败：{exc}")

    content = sio.getvalue().strip()
    if success and pusher:
        pusher.push(content)
    logger.info(content)


if __name__ == "__main__":
    main()
