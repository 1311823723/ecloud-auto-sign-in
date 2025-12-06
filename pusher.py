import requests
import json, requests, io, pytz
from datetime import datetime
from urllib3 import Retry
from requests.adapters import HTTPAdapter

sio = io.StringIO()
sio.seek(0, 2)
now = datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
sio.write("-----------" + now + "----------\n")
from loguru import logger

class WeChat:
    def __init__(self, title, params):
        """
        初始化
        :param title: 推送标题 (例如 "摩尔庄园")
        :param params: 这里的 params 是从 mole.py 传过来的列表
                       原本是 [corp_id, secret...]，现在我们只需要列表里的第1个元素作为 SendKey
        """
        self.title = title
        # 兼容处理：防止 params 为空或者解析错误
        if isinstance(params, list) and len(params) > 0:
            self.send_key = params[0]
        else:
            self.send_key = None
            logger.warning("未配置 Server酱 SendKey，将无法推送消息")

    def push(self, content):
        """
        发送推送
        :param content: 推送的具体内容
        """
        if not self.send_key:
            logger.error("SendKey 为空，跳过推送")
            return

        url = f"https://sctapi.ftqq.com/{self.send_key}.send"
        
        # Server酱支持 Markdown，我们把 content 包装一下
        data = {
            "title": self.title,
            "desp": content
        }

        try:
            response = requests.post(url, data=data)
            json_res = response.json()
            
            if json_res.get("code") == 0:
                logger.info("Server酱推送成功")
            else:
                logger.error(f"Server酱推送失败: {json_res}")
        except Exception as e:
            logger.error(f"推送请求异常: {e}")

# 这一行是为了兼容 mole.py 的 'from pusher import *' 引用机制
# 把 requests 和 sio 也暴露出去，变成 public
__all__ = ['WeChat', 'requests', 'sio', 'json', 'io']
