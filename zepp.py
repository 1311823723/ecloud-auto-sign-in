import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime

from loguru import logger
from pusher import WeChat, requests, sio


APP_NAME = "com.xiaomi.hm.health"
APP_VERSION = "6.12.0"
CLIENT_ID = "HuaMi"
REDIRECT_URI = "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html"
LOGIN_URL_TEMPLATE = "https://api-user.huami.com/registrations/{account}/tokens"
TOKEN_URL = "https://account.huami.com/v2/client/login"
UPLOAD_URL = "https://api-mifit-cn2.huami.com/v1/data/band_data.json"


@dataclass
class ZeppAccount:
    username: str
    password: str
    steps: int


def mask_account(username):
    if len(username) <= 4:
        return "*" * len(username)
    return f"{username[:3]}****{username[-4:]}"


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


def login(session, username, password):
    hashed_password = hashlib.md5(password.encode()).hexdigest()
    url = LOGIN_URL_TEMPLATE.format(account=username)
    data = {
        "client_id": CLIENT_ID,
        "password": hashed_password,
        "redirect_uri": REDIRECT_URI,
        "token": "access",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "User-Agent": f"MiFit/{APP_VERSION} ({APP_NAME})",
    }
    response = session.post(url, data=data, headers=headers, timeout=15)
    response.raise_for_status()
    result = response.json()
    login_token = result.get("token_info", {}).get("login_token")
    if not login_token:
        raise RuntimeError(f"Zepp 登录失败：{result}")

    params = {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "code": login_token,
        "country_code": "CN",
        "device_id": "2C8B4939-0CCD-4E94-8CBA-CB8EA6E613A1",
        "device_model": "phone",
        "grant_type": "access_token",
        "third_name": "huami_phone",
    }
    response = session.get(TOKEN_URL, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    result = response.json()
    token_info = result.get("token_info", {})
    app_token = token_info.get("app_token")
    user_id = result.get("user_id")
    if not app_token or not user_id:
        raise RuntimeError(f"Zepp token 获取失败：{result}")
    return app_token, user_id


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


def upload_steps(session, app_token, user_id, steps):
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
    response = session.post(UPLOAD_URL, headers=headers, data=data, timeout=15)
    response.raise_for_status()
    result = response.json()
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
                app_token, user_id = login(session, account.username, account.password)
                upload_steps(session, app_token, user_id, account.steps)
            sio.write(f"Zepp 步数提示：{masked} 同步成功，步数 {account.steps}\n")
            success = True
        except Exception as exc:
            sio.write(f"Zepp 步数提示：{masked} 同步失败：{exc}\n")
            logger.exception("Zepp 步数同步失败")

    content = sio.getvalue().strip()
    if success and pusher:
        pusher.push(content)
    logger.info(content)


if __name__ == "__main__":
    main()
