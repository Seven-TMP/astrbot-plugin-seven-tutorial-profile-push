import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

import aiohttp

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

# 持久化数据目录名（存储在 AstrBot 的 data 目录下）
DATA_DIR_NAME = "astrbot_plugin_seventmp_push"

# 当前插件版本（发版时同步修改 metadata.yaml 中的 version）
CURRENT_VERSION = "1.0.0"
# 远端最新版本元数据 URL（GitHub raw 的 metadata.yaml）
LATEST_VERSION_URL = (
    "https://raw.githubusercontent.com/Seven-TMP/"
    "astrbot-plugin-seven-tutorial-profile-push/master/metadata.yaml"
)


@register(
    "astrbot_plugin_seventmp_push",
    "Seven",
    "监控 Seven欧卡教程网主页最新帖子并推送到指定群聊",
    "1.0.0",
)
class SevenTMPProfilePush(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.checking = False
        self.timer_task: asyncio.Task | None = None

        # 运行时状态（从文件加载 / 持久化到文件）
        self.state: dict = {
            "last_post_url": "",
            "last_post_title": "",
            "last_check_time": 0,
            # unified_msg_origin -> 群显示信息（如 group_id）
            "enabled_groups": {},
        }

        # 远端最新版本号（运行时缓存，不持久化）
        self.latest_version: str = ""

        # 确保数据目录存在
        self.data_dir = os.path.join("data", DATA_DIR_NAME)
        os.makedirs(self.data_dir, exist_ok=True)
        self.state_file = os.path.join(self.data_dir, "state.json")

        # 加载持久化状态
        self._load_state()

        # 启动定时检查任务
        self.timer_task = asyncio.create_task(self._timer_loop())

    async def initialize(self):
        """插件初始化完成后执行首次检查"""
        await self._check_latest_version()
        await self._check_and_notify()
        logger.info(
            f"Seven欧卡教程网 主页推送插件已启动 (v{CURRENT_VERSION})"
        )

    # ==================== 持久化 ====================

    def _load_state(self):
        """从文件加载持久化状态"""
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # 合并，保留默认结构中的键
            for key in self.state:
                if key in saved:
                    self.state[key] = saved[key]
        except Exception as e:
            logger.warn(f"[Seven欧卡教程网推送] 加载状态文件失败: {e}")

    def _save_state(self):
        """将状态持久化到文件"""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warn(f"[Seven欧卡教程网推送] 保存状态文件失败: {e}")

    # ==================== 工具函数 ====================

    def _get_owner_set(self) -> set:
        """解析主人 QQ 列表"""
        owner_str = str(self.config.get("owner_qqs", "") or "")
        return set(s.strip() for s in re.split(r"[,\n，]", owner_str) if s.strip())

    def _normalize_interval(self) -> int:
        """检查间隔（秒），写死为 1800 秒（30 分钟），不提供配置项"""
        return 1800

    def _is_group_enabled(self, umo: str) -> bool:
        """判断某个群是否已开启推送"""
        return umo in self.state["enabled_groups"]

    def _enable_group(self, umo: str, group_id: str = ""):
        """开启某个群的推送"""
        self.state["enabled_groups"][umo] = group_id
        self._save_state()

    def _disable_group(self, umo: str):
        """关闭某个群的推送"""
        self.state["enabled_groups"].pop(umo, None)
        self._save_state()

    def _is_admin_user(self, event: AstrMessageEvent) -> bool:
        """判断消息发送者是否有管理权限（主人 / 群主 / 群管理员）"""
        sender_id = str(event.get_sender_id())

        # 主人 QQ 直接通过
        if sender_id in self._get_owner_set():
            return True

        # 尝试从原始消息中提取 OneBot 协议的 role 字段
        try:
            raw = event.message_obj.raw_message
            role = ""
            if isinstance(raw, dict):
                role = str(raw.get("sender", {}).get("role", ""))
            elif hasattr(raw, "sender"):
                sender = getattr(raw, "sender", None)
                if isinstance(sender, dict):
                    role = str(sender.get("role", ""))
                elif hasattr(sender, "role"):
                    role = str(getattr(sender, "role", ""))
            if role in ("owner", "admin"):
                return True
        except Exception:
            pass

        return False

    # ==================== 版本检查 ====================

    @staticmethod
    def _parse_version(v: str) -> list:
        """将版本号字符串解析为数字列表，例如 'v1.10.2' -> [1, 10, 2]"""
        return [int(x) for x in re.findall(r"\d+", v)]

    @classmethod
    def _is_newer_version(cls, remote: str, local: str) -> bool:
        """判断 remote 版本号是否高于 local"""
        return cls._parse_version(remote) > cls._parse_version(local)

    def _has_update(self) -> bool:
        """是否检测到有可更新的新版本"""
        if not self.latest_version:
            return False
        return self._is_newer_version(self.latest_version, CURRENT_VERSION)

    async def _check_latest_version(self):
        """拉取远端 metadata.yaml 解析最新版本号，失败时静默忽略"""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(LATEST_VERSION_URL) as resp:
                    if resp.status != 200:
                        return
                    text = await resp.text()
            m = re.search(r"^version:\s*v?([\d.]+)", text, re.MULTILINE)
            if not m:
                return
            remote = m.group(1)
            self.latest_version = remote
            if self._is_newer_version(remote, CURRENT_VERSION):
                logger.warn(
                    f"[Seven欧卡教程网推送] 发现新版本 v{remote}，"
                    f"当前 v{CURRENT_VERSION}，请尽快更新"
                )
        except Exception:
            # 版本检查失败不影响主功能
            pass

    def _build_update_notice(self) -> str:
        """构造更新提示尾巴，未检测到新版本时返回空字符串"""
        if not self._has_update():
            return ""
        return (
            f"\n\n⚠️ 推送插件有新版本 v{self.latest_version}"
            f"（当前 v{CURRENT_VERSION}），请管理员尽快更新"
        )

    @staticmethod
    def _decode_html(text: str) -> str:
        """简易 HTML 实体解码和标签清除"""
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&nbsp;", " ")
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&quot;", '"')
        text = text.replace("&#39;", "'")
        return text.strip()

    # ==================== API 请求 ====================

    async def _fetch_latest_post(self) -> dict | None:
        """从配置的接口地址获取最新帖子"""
        api_url = str(self.config.get("post_api_url", "") or "").strip()
        if not api_url:
            logger.warn("[Seven欧卡教程网推送] 获取帖子失败: 未配置接口地址")
            return None

        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    api_url, headers={"Accept": "application/json"}
                ) as resp:
                    if resp.status != 200:
                        logger.warn(
                            f"[Seven欧卡教程网推送] 获取帖子失败: {api_url} -> HTTP {resp.status}"
                        )
                        return None
                    data = await resp.json()

            # 兼容多种 API 返回格式
            posts: list = []
            if isinstance(data.get("data"), list):
                posts = data["data"]
            elif isinstance(data.get("data"), dict):
                root = data["data"]
                if isinstance(root.get("list"), list):
                    posts = root["list"]
                elif isinstance(root.get("items"), list):
                    posts = root["items"]
            elif isinstance(data.get("list"), list):
                posts = data["list"]

            if not posts:
                logger.warn(f"[Seven欧卡教程网推送] {api_url} -> 返回数据中没有帖子")
                return None

            post = posts[0]
            title = self._decode_html(str(post.get("title", ""))) or "[无标题]"
            post_id = post.get("id")
            profile_url = str(self.config.get("profile_url", ""))

            if post_id is not None:
                url = f"https://ets2.seventmp.cn/post/{post_id}"
            else:
                url = post.get("url") or post.get("link") or profile_url

            return {"title": title, "url": url}
        except Exception as e:
            logger.warn(f"[Seven欧卡教程网推送] 获取帖子失败: {api_url} -> {e}")
            return None

    # ==================== 推送逻辑 ====================

    def _build_post_message(self, post: dict) -> str:
        """构造帖子推送消息，附加更新提示尾巴（如有）"""
        return f"{post['title']}\n{post['url']}{self._build_update_notice()}"

    def _build_status_message(self, umo: str) -> str:
        """构造推送状态消息"""
        last_check = "暂无"
        if self.state["last_check_time"]:
            tz = timezone(timedelta(hours=8))
            dt = datetime.fromtimestamp(
                self.state["last_check_time"] / 1000, tz=tz
            )
            last_check = dt.strftime("%Y/%m/%d %H:%M:%S")

        profile_url = str(self.config.get("profile_url", ""))
        version_line = f"插件版本: v{CURRENT_VERSION}"
        if self._has_update():
            version_line += f" → 有新版本 v{self.latest_version} 可更新"
        return "\n".join(
            [
                "[Seven欧卡教程网 推送状态]",
                version_line,
                f"当前群推送: {'已开启' if self._is_group_enabled(umo) else '未开启'}",
                f"检查间隔: {self._normalize_interval()} 秒",
                f"接口地址: {self.config.get('post_api_url', '')}",
                f"最后检查: {last_check}",
                f"最后帖子: {self.state['last_post_title'] or '暂无'}",
                f"帖子链接: {self.state['last_post_url'] or profile_url}",
            ]
        )

    async def _broadcast(self, target_umos: list, message: str):
        """向目标群列表广播消息"""
        chain = MessageChain().message(message)
        for umo in target_umos:
            try:
                await self.context.send_message(umo, chain)
            except Exception as e:
                logger.warn(f"[Seven欧卡教程网推送] 推送到 {umo} 失败: {e}")

    async def _check_and_notify(self, manual_umo: str = None) -> dict:
        """检查最新帖子，如有更新则推送通知"""
        if self.checking:
            return {"updated": False, "reason": "正在检查中，请稍后再试"}

        self.checking = True
        try:
            latest = await self._fetch_latest_post()
            self.state["last_check_time"] = int(time.time() * 1000)
            self._save_state()

            if not latest:
                return {"updated": False, "post": None, "reason": "获取最新帖子失败"}

            changed = (
                latest["url"] != self.state["last_post_url"]
                or latest["title"] != self.state["last_post_title"]
            )

            if not changed:
                return {"updated": False, "post": latest, "reason": "暂无新帖子"}

            first_seen = (
                not self.state["last_post_url"]
                and not self.state["last_post_title"]
            )
            self.state["last_post_url"] = latest["url"]
            self.state["last_post_title"] = latest["title"]
            self._save_state()

            if first_seen and not manual_umo:
                logger.info(
                    f"[Seven欧卡教程网推送] 首次启动，已记录当前最新帖子: {latest['title']}"
                )
                return {
                    "updated": False,
                    "post": latest,
                    "reason": "首次启动，已记录当前最新帖子",
                }

            # 推送到目标群
            target_umos = (
                [manual_umo]
                if manual_umo
                else list(self.state["enabled_groups"].keys())
            )
            await self._broadcast(target_umos, self._build_post_message(latest))

            return {"updated": True, "post": latest}
        finally:
            self.checking = False

    # ==================== 定时器 ====================

    async def _timer_loop(self):
        """定时检查帖子更新的主循环"""
        try:
            while True:
                interval = self._normalize_interval()
                await asyncio.sleep(interval)
                try:
                    await self._check_latest_version()
                    await self._check_and_notify()
                except Exception as e:
                    logger.warn(f"[Seven欧卡教程网推送] 定时检查异常: {e}")
        except asyncio.CancelledError:
            pass

    # ==================== 群消息处理 ====================

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，处理推送相关指令"""
        msg = event.message_str.strip()
        umo = event.unified_msg_origin

        # ets推送状态 —— 所有人可用
        if msg == "ets推送状态":
            yield event.plain_result(self._build_status_message(umo))
            event.stop_event()
            return

        # 以下指令需要管理员权限
        if msg not in ("ets推送开启", "ets推送关闭", "ets推送检查"):
            return

        if not self._is_admin_user(event):
            return

        # ets推送开启
        if msg == "ets推送开启":
            group_id = getattr(event.message_obj, "group_id", "") or ""
            self._enable_group(umo, str(group_id))
            yield event.plain_result("[Seven欧卡教程网 推送]\n当前群已开启主页帖子推送")
            event.stop_event()
            return

        # ets推送关闭
        if msg == "ets推送关闭":
            self._disable_group(umo)
            yield event.plain_result("[Seven欧卡教程网 推送]\n当前群已关闭主页帖子推送")
            event.stop_event()
            return

        # ets推送检查
        if msg == "ets推送检查":
            result = await self._check_and_notify(umo)
            # 如果已更新且有帖子，推送消息已发送，无需额外回复
            if result.get("updated") and result.get("post"):
                event.stop_event()
                return

            post = result.get("post")
            if post:
                reply = "\n".join(
                    [
                        "[Seven欧卡教程网 推送]",
                        result.get("reason", "检查完成"),
                        post["title"],
                        post["url"],
                    ]
                )
            else:
                reply = "\n".join(
                    [
                        "[Seven欧卡教程网 推送]",
                        result.get("reason", "检查失败"),
                    ]
                )
            yield event.plain_result(reply)
            event.stop_event()
            return

    # ==================== 生命周期 ====================

    async def terminate(self):
        """插件卸载 / 停用时清理资源"""
        if self.timer_task:
            self.timer_task.cancel()
            try:
                await self.timer_task
            except asyncio.CancelledError:
                pass
        logger.info("[Seven欧卡教程网推送] 插件已停止")
