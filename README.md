# astrbot_plugin_seventmp_push

监控 Seven欧卡教程网主页最新帖子并推送到指定群聊。

## 功能

- 定时检查最新帖子，发现新帖自动推送到已开启的群
- 群内指令控制推送开关，无需重启
- 通过 AstrBot WebUI 可视化配置
- 持久化存储推送状态和已启用群列表

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `owner_qqs` | `string` | `""` | 主人QQ，多个用英文逗号分隔 |
| `profile_url` | `string` | `https://ets2.seventmp.cn/profile/13` | 用户主页地址 |
| `post_api_url` | `string` | `https://ets2.seventmp.cn/api/users/13/posts?page=1&limit=1` | 帖子接口地址 |
| `check_interval_seconds` | `int` | `180` | 检查间隔（秒），最小 10 秒 |

## 群内指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `ets推送状态` | 所有人 | 查看当前群的推送状态 |
| `ets推送开启` | 管理员/主人 | 开启当前群的推送 |
| `ets推送关闭` | 管理员/主人 | 关闭当前群的推送 |
| `ets推送检查` | 管理员/主人 | 立即检查一次最新帖子 |

> 指令为纯文本匹配，不需要加 `/` 前缀。

## 安装

将本插件目录放入 AstrBot 的 `data/plugins/` 目录下，然后在 AstrBot WebUI 中启用插件即可。

或通过 AstrBot 插件市场安装（需先发布到 GitHub）。

## 数据存储

推送状态（最后帖子、已启用群列表等）持久化存储在 `data/astrbot_plugin_seventmp_push/state.json`，插件更新或重装不会丢失数据。

## 许可

MIT
