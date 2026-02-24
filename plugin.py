import os
from typing import List, Tuple, Type

from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseCommand,
    BaseTool,
    ComponentInfo,
    ConfigField,
    ToolParamType,
)
from src.common.logger import get_logger
import httpx
import base64

from .bgg_client import bgg_thing_details_api, resolve_boardgame_by_cn_name
from .utils import load_terms
from .register import BoardgameRegisterCommand

logger = get_logger("bgg_search_plugin")


class BoardgameQueryTool(BaseTool):
    """桌游信息查询工具（供 LLM 调用）"""

    name = "boardgame_query"
    description = (
        "根据桌游的中文名 / 英文名 / 黑话简称，查询该桌游的详细信息，"
        "包括英文名、发行年份、BGG 排名、评分、重度、人数、时长、年龄、"
        "游戏类型、游戏机制、最佳游玩人数、语言依赖、完整英文简介以及 BGG 链接。"
    )

    parameters = [
        (
            "query",
            ToolParamType.STRING,
            "桌游名称，支持中文名/英文名/黑话，如：山屋惊魂、Betrayal at House on the Hill、小黑屋",
            True,
            None,
        ),
    ]

    available_for_llm = True

    async def execute(self, function_args: dict):
        query = function_args.get("query", "").strip()
        if not query:
            return {"name": self.name, "content": "查询失败：未提供桌游名称。"}

        ddgs_proxy = self.get_config("ddgs.proxy", None)
        verbose = self.get_config("plugin.verbose_logging", False)
        jishi_cookie = self.get_config("jishi.cookie", "")

        details = await resolve_boardgame_by_cn_name(
            cn_name=query,
            proxy=ddgs_proxy,
            verbose=verbose,
            jishi_cookie=jishi_cookie,
        )

        if not details:
            return {
                "name": self.name,
                "content": f"未找到桌游「{query}」的相关信息，请尝试更换更准确的名称。",
            }

        bgg_failed = details.get("bgg_failed", False)
        if bgg_failed:
            en_name = details.get("name", "")
            cn_name = details.get("cn_name", query)
            name_source = details.get("_name_source", "未知")
            bgg_source = details.get("_bgg_source", "未知")
            return {
                "name": self.name,
                "content": (
                    f"找到了桌游「{cn_name}」（英文名：{en_name}），"
                    f"但暂时无法从 BGG 获取详细信息（评分、排名、人数等）。\n"
                    f"数据来源：名称来自{name_source}，详情来源{bgg_source}"
                ),
            }

        # 数据提取
        year = details.get("year", "")
        rank = details.get("rank", "N/A")
        avg = details.get("average", "N/A")
        avgw = details.get("avg_weight", "N/A")
        minp = details.get("min_players", "?")
        maxp = details.get("max_players", "?")
        mint = details.get("min_time", "?")
        maxt = details.get("max_time", "?")
        minage = details.get("min_age", "?")
        desc = details.get("description", "")
        bgg_url = details.get("bgg_url", "")
        categories = details.get("categories", [])
        mechanics = details.get("mechanics", [])
        best_numplayers = details.get("best_numplayers", "")
        lang_dependence = details.get("language_dependence", "") # BGG 的
        cn_name = details.get("cn_name", query)
        en_name = details.get("name", "")
        
        # 来源信息
        name_source = details.get("_name_source", "未知")
        bgg_source = details.get("_bgg_source", "未知")

        # 集石特有数据
        cn_desc = details.get("cn_description")
        jishi_score = details.get("jishi_score")
        jishi_cats = details.get("jishi_categories", [])
        table_req = details.get("table_requirement")
        lang_req = details.get("language_requirement") # 集石的语言要求

        # 简介：优先集石中文
        desc_display = cn_desc if cn_desc else (desc or "暂无简介")
        types_str = ", ".join(categories[:6])
        mechanics_str = ", ".join(mechanics[:6])

        lines = [
            f"游戏：{en_name}（中文名：{cn_name}）",
            f"发行年份：{year}",
            f"BGG 排名：{rank}",
            f"评分：{avg}/10",
            f"重度：{avgw}/5",
            f"人数：{minp}-{maxp}",
            f"时长：{mint}-{maxt} 分钟",
            f"年龄：{minage}+",
        ]

        if best_numplayers:
            lines.append(f"最佳游玩人数：{best_numplayers}")
        
        # 语言依赖：优先集石，其次 BGG
        final_lang = lang_req if lang_req else lang_dependence
        if final_lang:
            lines.append(f"语言依赖：{final_lang}")

        lines.extend([
            f"类型：{types_str}",
            f"机制：{mechanics_str}",
            f"简介：{desc_display}",
            f"BGG 链接：{bgg_url}",
            f"数据来源：名称来自{name_source}，详情来自{bgg_source}",
        ])

        content = "\n".join(lines)

        return {
            "name": self.name,
            "content": content,
            "data": {
                "cn_name": cn_name,
                "en_name": en_name,
                "year": year,
                "rank": rank,
                "average": avg,
                "avg_weight": avgw,
                "min_players": minp,
                "max_players": maxp,
                "min_time": mint,
                "max_time": maxt,
                "min_age": minage,
                "best_numplayers": best_numplayers,
                "language_dependence": final_lang,
                "categories": categories,
                "mechanics": mechanics,
                "description": desc_display,
                "bgg_url": bgg_url,
                "name_source": name_source,
                "bgg_source": bgg_source,
            },
        }


class BoardgameCommand(BaseCommand):
    """响应 /桌游 命令"""

    command_name = "boardgame"
    command_description = (
        "查询桌游信息（支持中文/简称/黑话，自动通过通用搜索抽取英文名），示例：/桌游 肥肠面"
    )
    command_pattern = r"^/桌游\s+(?P<keyword>.+)$"

    async def execute(self) -> Tuple[bool, str, bool]:
        keyword = self.matched_groups.get("keyword", "").strip()
        if not keyword:
            await self.send_text("请输入要查询的桌游名称，例如：/桌游 肥肠面")
            return True, "未提供关键词", True

        await self.send_text(f"🔍 正在查询桌游：{keyword}，正在抽取英文名候选...")

        ddgs_proxy = self.get_config("ddgs.proxy", None)
        verbose = self.get_config("plugin.verbose_logging", True)
        enable_ai_translate = self.get_config("ai_translate.enabled", False)
        jishi_cookie = self.get_config("jishi.cookie", "")

        details = await resolve_boardgame_by_cn_name(
            cn_name=keyword,
            proxy=ddgs_proxy,
            verbose=verbose,
            jishi_cookie=jishi_cookie,
        )

        bgg_failed = details.get("bgg_failed", False)
        
        # 来源信息
        name_source = details.get("_name_source", "未知")
        bgg_source = details.get("_bgg_source", "未知")
        
        en_name = details.get("name", "")
        cn_name = details.get("cn_name", keyword)

        if bgg_failed:
            source_text = f"📛 名称来源：{name_source}\n📚 详情来源：{bgg_source}"
            text = (
                f"🇨🇳 中文名：{cn_name}\n"
                f"🇺🇸 英文名：{en_name}\n"
                f"\n"
                f"⚠️ 注意：BGG 暂时无法访问，未能获取详细信息（评分、排名、玩家数等）\n"
                f"{source_text}"
            )
            await self.send_text(text)
            return True, f"已提取桌游信息（BGG未响应）：{en_name}", True

        # 提取 BGG 基础数据
        year = details.get("year", "")
        rank = details.get("rank", "N/A")
        avg = details.get("average", "N/A")
        avgw = details.get("avg_weight", "N/A")
        minp = details.get("min_players", "?")
        maxp = details.get("max_players", "?")
        mint = details.get("min_time", "?")
        maxt = details.get("max_time", "?")
        minage = details.get("min_age", "?")
        desc = details.get("description", "")
        image = details.get("image", "")
        bgg_id = details.get("bgg_id", "")
        bgg_url = f"https://boardgamegeek.com/boardgame/{bgg_id}"
        categories = details.get("categories", [])
        mechanics = details.get("mechanics", [])
        best_numplayers = details.get("best_numplayers", "")
        lang_dependence = details.get("language_dependence", "") # BGG
        
        # 提取集石特有数据
        cn_desc = details.get("cn_description")
        jishi_score = details.get("jishi_score")
        jishi_cats = details.get("jishi_categories", [])
        table_req = details.get("table_requirement")
        lang_req = details.get("language_requirement") # 集石

        # 翻译逻辑
        translated_categories = []
        translated_mechanics = []
        translated_desc = desc

        if enable_ai_translate:
            try:
                translate_prompt = (
                    "请将以下桌游信息中的 '游戏类型'、'游戏机制' 和 '简介' 翻译成中文。\n"
                    "请保持专业术语的准确性。\n"
                    "输出格式要求（严格按照此格式，不要包含其他内容）：\n"
                    "类型：[翻译后的类型列表，用顿号分隔]\n"
                    "机制：[翻译后的机制列表，用顿号分隔]\n"
                    "简介：[翻译后的简介]\n\n"
                    "原始内容：\n"
                    f"类型：{', '.join(categories)}\n"
                    f"机制：{', '.join(mechanics)}\n"
                    f"简介：{desc}\n"
                )

                logger.info("[AI 翻译] 正在请求 AI 翻译游戏信息...")

                from src.plugin_system.apis import llm_api

                models = llm_api.get_available_models()
                model_config = None
                target_key = "utils"
                if target_key in models:
                    model_config = models[target_key]
                else:
                    for name_cfg, conf in models.items():
                        if name_cfg != "embedding":
                            model_config = conf
                            break

                if model_config:
                    success, ai_response, _, _ = await llm_api.generate_with_model(
                        prompt=translate_prompt,
                        model_config=model_config,
                        request_type="plugin.translate",
                    )
                    if success and ai_response:
                        for line in ai_response.split("\n"):
                            if line.startswith("类型："):
                                translated_categories = [
                                    x.strip()
                                    for x in line.replace("类型：", "").strip().split("、")
                                    if x.strip()
                                ]
                            elif line.startswith("机制："):
                                translated_mechanics = [
                                    x.strip()
                                    for x in line.replace("机制：", "").strip().split("、")
                                    if x.strip()
                                ]
                            elif line.startswith("简介："):
                                translated_desc = line.replace("简介：", "").strip()
                        logger.info("[AI 翻译] AI 翻译完成")
                    else:
                        logger.warning("[AI 翻译] AI 翻译失败，回退到词典/原文")
            except Exception as e:
                logger.error(f"[AI 翻译] 异常: {e}")

        term_map = load_terms()
        if not translated_categories and categories:
            translated_categories = [term_map.get(c, c) for c in categories]
        if not translated_mechanics and mechanics:
            translated_mechanics = [term_map.get(m, m) for m in mechanics]

        categories_text = "、".join(translated_categories[:5]) if translated_categories else "暂无"
        mechanics_text = "、".join(translated_mechanics[:5]) if translated_mechanics else "暂无"

        # 简介优先级：集石中文 > AI翻译 > 原文
        if cn_desc:
            desc_display = cn_desc
        else:
            if not enable_ai_translate:
                desc_display = (desc or "暂无简介")[:200] + "..." if len(desc or "") > 200 else (desc or "暂无简介")
            else:
                desc_display = translated_desc

        final_query = details.get("_final_query", "")
        source_text = f"📛 名称来源：{name_source} | 📚 详情来源：{bgg_source}"

        text = (
            f"🇨🇳 中文名：{cn_name}\n"
            f"🇺🇸 英文名：{en_name}\n"
            f"📅 发行年份：{year}\n"
            f"🏆 BGG 排名：{rank}   ⭐ 评分：{avg}/10\n"
            f"🧠 重度：{avgw}/5   👥 人数：{minp}-{maxp} 人\n"
            f"⏳ 时长：{mint}-{maxt} 分钟   🚼 年龄：{minage}+\n"
            f"📚 游戏类型：{categories_text}\n"
            f"⚙️ 游戏机制：{mechanics_text}\n"
        )
        
        if jishi_score:
            text += f"🔥 集石评分：{jishi_score}\n"
        if jishi_cats:
            text += f"🏷️ 集石分类：{'、'.join(jishi_cats)}\n"

        if best_numplayers:
            text += f"👍 最佳人数：{best_numplayers} 人\n"
            
        # 语言依赖：优先集石，其次 BGG
        final_lang = lang_req if lang_req else lang_dependence
        if final_lang:
            text += f"🌍 语言依赖：{final_lang}\n"
            
        if table_req:
            text += f"📏 桌面要求：{table_req}\n"

        text += (
            f"📝 简介：{desc_display}\n"
            f"{source_text}\n"
            f"🔗 BGG 链接：{bgg_url}"
        )

        if final_query:
            text = f"🔍 最终通过搜索词「{final_query}」命中 BGG 条目。\n\n" + text

        await self.send_text(text)

        if image:
            try:
                from .utils import HEADERS

                img_headers = {
                    "User-Agent": HEADERS["User-Agent"],
                    "Accept": "image/*",
                    "Accept-Encoding": "identity",
                }
                img_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True, proxy=ddgs_proxy)
                img_resp = await img_client.get(image, headers=img_headers)
                await img_client.aclose()

                img_resp.raise_for_status()

                ct = img_resp.headers.get("content-type", "").lower()
                if not ct.startswith("image/"):
                    logger.warning(f"[发送图片] BGG 返回的 URL 不是图片 Content-Type: {ct}, URL={image}")
                else:
                    img_b64 = base64.b64encode(img_resp.content).decode("utf-8")
                    ok = await self.send_image(img_b64)
                    if not ok:
                        logger.error("[发送图片] send_image 返回 False")
            except Exception as e:
                print(f"[发送图片失败] {e}")
                logger.error(f"[发送图片失败] {e}")

        return True, f"已查询桌游：{en_name}", True


@register_plugin
class bggsearchplugin(BasePlugin):
    """bgg_search_plugin插件 - 桌游信息查询"""

    plugin_name: str = "fsqhn_bgg_search_plugin"
    enable_plugin: bool = True
    dependencies: List[str] = []
    
    python_dependencies = [
        "httpx",
        "ddgs",
        "beautifulsoup4"
    ]
    
    config_file_name: str = "config.toml"

    config_section_descriptions = {
        "plugin": "插件启用配置",
        "jishi": "集石数据源配置 (优先级最高，需提供Cookie)",
        "ddgs": "DuckDuckGo 搜索代理配置（如无代理可留空或删除该节）",
        "ai_translate": "AI 翻译配置（翻译类型、机制和简介）",
    }

    config_schema = {
        "plugin": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用本插件",
            ),
            "verbose_logging": ConfigField(
                type=bool,
                default=False,
                description="是否在后台显示详细日志",
            ),
        },
        "jishi": {
            "cookie": ConfigField(
                type=str,
                default="",
                description="集石(Gstone)网站的Cookie。",
                example="PHPSESSID=xxxx; _gstone_uid=xxxx;",
            ),
        },
        "ddgs": {
            "proxy": ConfigField(
                type=str,
                default="",
                description="DuckDuckGo 搜索使用的代理地址",
                example="http://127.0.0.1:10809",
            ),
        },
        "ai_translate": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否开启 AI 全文翻译。",
            ),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
    
        return [
            (BoardgameCommand.get_command_info(), BoardgameCommand),
            (BoardgameQueryTool.get_tool_info(), BoardgameQueryTool),
            (BoardgameRegisterCommand.get_command_info(), BoardgameRegisterCommand),
        ]
