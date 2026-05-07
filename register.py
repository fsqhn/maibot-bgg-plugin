import os
import json
import logging
from typing import Dict

# 修复沙箱报错：不再使用 from src.common.logger import get_logger
# 注意：旧版的 BoardgameRegisterCommand 类已被彻底移除
# 因为新版 plugin.py 里已经用 @Command 装饰器重写了 /桌游登记 的逻辑
# 这里只保留底层被调用的文件读写函数，避免引入 src.plugin_system 报错
logger = logging.getLogger("bgg_search_plugin.register")

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
    """清空 alias.json (用于紧急修复)"""
    try:
        if os.path.exists(ALIAS_FILE):
            os.remove(ALIAS_FILE)
            logger.info(f"[clear_alias_file] 已清空 {ALIAS_FILE}")
            return True
        return False
    except Exception as e:
        logger.error(f"[clear_alias_file] 清空失败: {e}")
        return False
