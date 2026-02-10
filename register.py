import os
import json
from typing import Dict, Tuple, List, Type

from src.plugin_system import BaseCommand, ComponentInfo
from src.plugin_system.apis import chat_api
from src.common.logger import get_logger

logger = get_logger("bgg_search_plugin.register")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
ALIAS_FILE = os.path.join(DATA_DIR, "alias.json")


def save_alias(alias: Dict[str, str]) -> bool:
    """写入 alias.json (全量覆盖)"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ALIAS_FILE, "w", encoding="utf-8") as f:
            json.dump(alias, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"[save_alias] 保存 alias.json 失败: {e}")
        return False


def load_alias_from_file() -> Dict[str, str]:
    """从 alias.json 加载现有桌游别名"""
    try:
        if os.path.exists(ALIAS_FILE):
            with open(ALIAS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    except Exception as e:
        logger.error(f"[load_alias_from_file] 读取 alias.json 失败: {e}")
        return {}


def clear_alias_file() -> bool:
    """
    清空 alias.json (用于紧急修复)
    """
    try:
        if os.path.exists(ALIAS_FILE):
            os.remove(ALIAS_FILE)
            logger.info(f"[clear_alias_file] 已清空 {ALIAS_FILE}")
            return True
        return False
    except Exception as e:
        logger.error(f"[clear_alias_file] 清空失败: {e}")
        return False


class BoardgameRegisterCommand(BaseCommand):
    """
    处理桌游登记/删除。
    格式：
    - 登记：/桌游登记 中文名/英文名
    - 删除：/桌游登记 中文名/删除
    - 清空：/桌游登记 清空
    """

    command_name = "boardgame_register"
    command_description = "登记或删除桌游中英文名。"
    command_pattern = r"^/桌游登记\s+(?P<cn_name>[^/]+)/(?P<en_name>[^/]*)$"

    async def execute(self) -> Tuple[bool, str, bool]:
        # 提取参数
        cn_name = self.matched_groups.get("cn_name", "").strip()
        en_name = self.matched_groups.get("en_name", "").strip()

        # 1. 特殊命令——清空数据（紧急修复用）
        if cn_name == "清空":
            ok = clear_alias_file()
            if ok:
                await self.send_text("✅ 已清空损坏的数据，请重新登记")
                return True, "数据已清空", True
            else:
                await self.send_text("❌ 清空数据失败")
                return True, "清空失败", True

        # 2. 优先检查删除指令（在其他所有校验之前）
        if en_name.lower() in ["删除", "delete", "remove", "del"]:
            if not cn_name:
                await self.send_text("❌ 删除格式：`/桌游登记 中文名/删除`")
                return True, "删除格式错误", True

            existing_alias = load_alias_from_file()
            if cn_name in existing_alias:
                existing_alias.pop(cn_name)
                ok = save_alias(existing_alias)
                if ok:
                    await self.send_text(f"✅ 已删除桌游「{cn_name}」的登记记录")
                    return True, "删除成功", True
                else:
                    await self.send_text("❌ 删除失败，请重试。")
                    return True, "删除失败", False
            else:
                await self.send_text(f"⚠️ 该游戏「{cn_name}」未在词典中，无需删除")
                return True, "记录不存在", False

        # 3. 基础校验
        if not cn_name:
            await self.send_text("❌ 请提供中文名，格式：`/桌游登记 中文名/英文名`")
            return True, "格式错误", True

        if not en_name:
            await self.send_text("❌ 请提供英文名，格式：`/桌游登记 中文名/英文名`")
            return True, "格式错误", True

        # 4. 英文名格式校验（至少包含一个字母）
        if not any(c.isalnum() for c in en_name):
            await self.send_text(
                "❌ 英文名格式不正确，请输入有效的英文名（至少包含一个字母），"
                "或使用 `/桌游登记 中文名/删除` 来删除记录"
            )
            return True, "英文名格式错误", True

        # 5. 读取现有术语
        existing_alias = load_alias_from_file()
        existing_en = existing_alias.get(cn_name)

        # 6. 执行逻辑
        try:
            if existing_en:
                # 已存在 -> 提示删除再录入
                msg = (
                    f"⚠️ 该游戏「{cn_name}」已在词典中，现有英文名为：{existing_en}\n"
                    f"请发送 `/桌游登记 {cn_name}/删除`"
                    f"删除现有记录后再重新录入"
                )
                await self.send_text(msg)
                return True, "记录已存在", False
            else:
                # 不存在 -> 保存
                new_alias = {cn_name: en_name}
                
                ok = save_alias(new_alias)
                if ok:
                    msg = f"✅ 成功登记：{cn_name} → {en_name}"
                    await self.send_text(msg)
                    return True, "成功登记", True
                else:
                    await self.send_text("❌ 保存失败，请重试。")
                    return True, "保存失败", False
        except Exception as e:
            logger.error(f"[BoardgameRegisterCommand] 执行异常: {e}")
            await self.send_text("⚠️ 内部错误，请联系管理员。")
            return True, "内部错误", False


def get_register_components() -> List[Tuple[ComponentInfo, Type]]:
    """
    返回"桌游登记"相关的所有 Command 组件
    """
    return [
        (BoardgameRegisterCommand.get_command_info(), BoardgameRegisterCommand),
    ]
