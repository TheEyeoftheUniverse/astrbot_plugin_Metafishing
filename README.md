![:astrbot_plugin_fish](https://count.getloli.com/@:astrbot_plugin_fish?theme=capoo-1)

# 🎣 AstrBot 钓鱼插件

原项目地址：<https://github.com/0xNMLSS/astrbot_plugin_fishing>

本仓库是基于原项目持续定制的衍生版本，目标是把玩法扩展、玩家 WebUI 和管理体验逐步做成可长期维护的版本。

## 当前版本重点

### 1. 玩家 WebUI 可用性提升

- 玩家端独立端口，默认 `8888`
- 插件重载时会优先接管并关闭残留的玩家 WebUI 任务，减少端口被占用的问题
- 管理端 WebUI 增加优雅关闭逻辑，降低重复启动导致的异常
- 登录页支持根据配置展示不同登录方式

### 2. 新增 Linux.do OAuth 登录

- 支持 `Linux.do` OAuth 登录玩家 WebUI
- 可配置自动注册、昵称字段映射、用户 ID 字段映射
- 可按需保留原来的账号密码登录回退
- 支持为 OAuth 请求单独配置代理

相关配置位于 `webui.oauth.linuxdo`，常用字段如下：

```json
{
  "webui": {
    "player_port": 8888,
    "oauth": {
      "linuxdo": {
        "enabled": false,
        "client_id": "",
        "client_secret": "",
        "redirect_uri": "http://localhost:8888/player/oauth/linuxdo/callback",
        "scope": "read",
        "user_id_field": "id",
        "nickname_field": "username",
        "auto_register": true,
        "allow_password_fallback": true,
        "proxy_url": ""
      }
    }
  }
}
```

### 3. 交易所改造成“期货”玩法

- `交易所` 指令保留别名，但文案和页面统一改为 `期货`
- 开户免费，不再扣除开户费
- 新增 `exchange_capacity` 用户字段和 `041_add_exchange_capacity.py` 迁移
- 支持查看期货容量与升级容量
- 持仓到期后不再直接腐败清空，而是按当前价格自动卖出并结算金币
- 玩家 WebUI 与指令侧都同步展示容量、升级入口和自动结算提示

常用命令：

- `/期货`
- `/持仓`
- `/清仓`
- `/期货容量`
- `/升级期货`

### 4. 玩家端接口扩展

- 首页增加更多状态聚合信息
- 新增图鉴接口
- 酒馆页补齐公告、排行榜、每日擦弹、公示信息和进行中的科考数据
- 管理端/玩家端共用一套玩家凭证数据，避免出现两套登录体系

## 与原仓库相比的主要方向

- 更强调玩家 WebUI 的可用性，而不只是实验性页面
- 增加第三方 OAuth 登录能力
- 将原交易所玩法扩展为带容量成长和自动结算的期货系统
- 持续补齐前端页面与后端 API 的对应关系，减少“有页面但没有完整数据接口”的情况

## 使用说明

1. 按 AstrBot 插件方式安装本项目。
2. 确认 `webui.port` 与 `webui.player_port` 未被占用。
3. 如果启用 Linux.do OAuth，需确保回调地址与平台侧配置完全一致。
4. 升级现有数据库时，请确认迁移已执行到 `041_add_exchange_capacity.py`。

## 说明

- 当前仓库仍在持续开发中，但本次提交涉及的玩家 WebUI、期货容量与 OAuth 相关功能已经过一轮实际测试整理。
- `data/*.json`、`data/*.bak` 等运行时数据默认不纳入版本控制。

## 免责声明

- 本项目仅供学习和娱乐使用
- 使用者需自行承担使用风险
- 开发者不对任何直接或间接损失负责
