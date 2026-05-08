# 自动签到

## GitHub Secrets

- `MOLE_ACCOUNTS`: 摩尔庄园账号，每行一个，格式为 `账号,密码`
- `WECHAT_PARAMS`: Server酱 SendKey，用于推送通知
- `ECLOUD_ACCOUNT`: 天翼云盘账号，格式为 `账号,密码`
- `ZEPP_ACCOUNTS`: Zepp Life 账号，每行一个，格式为 `账号,密码,步数`
- `ZEPP_STEP_MIN`: Zepp 随机步数下限，默认 `18000`
- `ZEPP_STEP_MAX`: Zepp 随机步数上限，默认 `28000`

`ZEPP_ACCOUNTS` 第三列可以写固定步数，也可以写 `random`：

```text
15551661320,你的密码,random
15551661320,你的密码,23000
```
