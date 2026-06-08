from __future__ import annotations
import os
import base64
import asyncio
import httpx
from typing import Any, Dict, List, Tuple, Callable, Awaitable

from maibot_sdk import (
    MaiBotPlugin,
    Command,
    Tool,
    PluginConfigBase,
    Field,
    CONFIG_RELOAD_SCOPE_SELF,
)
from maibot_sdk.types import ToolParameterInfo, ToolParamType


def _peel_envelope(result: Any, *, max_depth: int = 4) -> Any:
    for _ in range(max_depth):
        if not isinstance(result, dict):
            return result
        if "result" not in result or "success" not in result:
            return result
        inner = result["result"]
        if inner is None:
            return result
        result = inner
    return result

from .bgg_client import resolve_boardgame_by_cn_name
from .utils import load_terms, BGG_POWERED_BY_IMAGE_URL


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件基础配置"
    config_version: str = Field(default="1.0.0", description="配置版本号")
    enabled: bool = Field(default=True, description="是否启用插件")
    verbose_logging: bool = Field(default=False, description="详细日志")


class JishiSection(PluginConfigBase):
    __ui_label__ = "集石数据源"
    cookie: str = Field(default="", description="集石Cookie")


class DdgsSection(PluginConfigBase):
    __ui_label__ = "DDG搜索"
    proxy: str = Field(default="", description="DDG代理地址")


class TavilySection(PluginConfigBase):
    __ui_label__ = "Tavily搜索"
    api_key: str = Field(default="", description="Tavily API Key")
    search_depth: str = Field(default="basic", description="搜索深度：basic或advanced")


class AiTranslateSection(PluginConfigBase):
    __ui_label__ = "AI翻译"
    enabled: bool = Field(default=True, description="是否开启AI翻译")


class BggSearchPluginConfig(PluginConfigBase):
    __ui_label__ = "BGG搜索插件"
    plugin: PluginSection = Field(default_factory=PluginSection)
    jishi: JishiSection = Field(default_factory=lambda: JishiSection())
    ddgs: DdgsSection = Field(default_factory=lambda: DdgsSection())
    tavily: TavilySection = Field(default_factory=lambda: TavilySection())
    ai_translate: AiTranslateSection = Field(default_factory=lambda: AiTranslateSection())


class BggSearchPlugin(MaiBotPlugin):
    plugin_name: str = "fsqhn_bgg_search_plugin"
    enable_plugin: bool = True
    config_model = BggSearchPluginConfig

    async def on_load(self) -> None:
        self.ctx.logger.info("BGG搜索插件已加载")

    async def on_unload(self) -> None:
        self.ctx.logger.info("BGG搜索插件已卸载")

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        if scope == CONFIG_RELOAD_SCOPE_SELF:
            self.ctx.logger.info("插件配置已更新 version=%s", version)

    async def _call_llm_text(self, prompt: str, model: str = "utils") -> str | None:
        log = self.ctx.logger
        try:
            result = await asyncio.wait_for(self.ctx.llm.generate(prompt=prompt, model=model), timeout=60)
            result = _peel_envelope(result)
            if not isinstance(result, dict):
                return None
            success = bool(result.get("success", False))
            response_text = str(result.get("response") or "")
            if not success:
                log.error("[LLM] 调用失败: %s", result.get("error"))
                return None
            return response_text or None
        except Exception as e:
            log.error("[LLM] 调用异常: %s", e)
            return None

    def _make_ddg_llm_caller(self, model: str = "utils") -> Callable[[str], Awaitable[str]]:
        async def caller(prompt: str) -> str:
            r = await self._call_llm_text(prompt, model=model)
            if not r:
                raise ValueError("LLM返回空")
            return r
        return caller

    @Tool("boardgame_query", brief_description="根据桌游中文名/英文名/黑话简称查询详细信息", parameters=[ToolParameterInfo(name="query", param_type=ToolParamType.STRING, description="桌游名称", required=True)])
    async def handle_boardgame_query(self, query: str, **kwargs: Any) -> Dict[str, Any]:
        cfg = self.config
        log = self.ctx.logger
        try:
            details = await resolve_boardgame_by_cn_name(
                cn_name=query,
                proxy=cfg.ddgs.proxy or None,
                verbose=cfg.plugin.verbose_logging,
                jishi_cookie=cfg.jishi.cookie or None,
                tavily_api_key=cfg.tavily.api_key or None,
                llm_caller=self._make_ddg_llm_caller(),
                custom_logger=log,
            )
        except Exception as e:
            return {"name": "boardgame_query", "content": f"查询出错: {e}"}

        if not details:
            return {"name": "boardgame_query", "content": f"未找到桌游「{query}」"}

        if details.get("bgg_failed"):
            return {"name": "boardgame_query", "content": f"找到「{details.get('cn_name')}」但BGG无法获取详情"}

        content, data = await self._format_boardgame_reply(details, cfg=cfg)
        return {"name": "boardgame_query", "content": content, "data": data}

    @Command("boardgame", description="查询桌游信息", pattern=r"^/桌游\s+(?P<keyword>.+)$", timeout=90)
    async def handle_boardgame_command(self, **kwargs: Any) -> Tuple[bool, str, bool]:
        matched = kwargs.get("matched_groups", {})
        keyword = matched.get("keyword", "").strip()
        stream_id = kwargs["stream_id"]

        if not keyword:
            await self.ctx.send.text("请输入桌游名称，例如：/桌游 肥肠面", stream_id)
            return True, "未提供关键词", True

        await self.ctx.send.text(f"🔍 正在查询桌游：{keyword}...", stream_id)
        cfg = self.config
        log = self.ctx.logger
        llm_caller = self._make_ddg_llm_caller()

        try:
            details = await resolve_boardgame_by_cn_name(
                cn_name=keyword,
                proxy=cfg.ddgs.proxy or None,
                verbose=cfg.plugin.verbose_logging,
                jishi_cookie=cfg.jishi.cookie or None,
                tavily_api_key=cfg.tavily.api_key or None,
                llm_caller=llm_caller,
                custom_logger=log,
            )
        except Exception as e:
            log.error("[调试] resolve 异常: %s", e)
            await self.ctx.send.text(f"❌ 查询出错: {e}", stream_id)
            return True, "异常", True

        if not details:
            await self.ctx.send.text(f"😔 未找到桌游「{keyword}」的相关信息", stream_id)
            return True, "未找到", True

        if details.get("bgg_failed"):
            source = details.get("_source", "未知")
            name_source = details.get("_name_source", "未知")
            SOURCE_FRIENDLY = {"集石→BGG(详情获取失败)": "🔴 集石→BGG详情获取失败", "全部方案失败": "🔴 全部方案失败"}
            source_display = SOURCE_FRIENDLY.get(source, f"ℹ️ {source}")
            text = (f"🇨🇳 中文名：{details.get('cn_name')}\n🇺🇸 英文名：{details.get('name')}\n\n"
                    f"⚠️ 注意：BGG 暂时无法访问，未能获取详细信息\n📋 查询方案：{source_display}")
            if details.get("cn_description"):
                text += f"\n📝 {details['cn_description']}"
            await self.ctx.send.text(text, stream_id)
            return True, "BGG未响应", True

        content, data = await self._format_boardgame_reply(details, cfg=cfg)
        await self.ctx.send.text(content, stream_id)

        images_to_send = []
        if data.get("image"):
            images_to_send.append(data["image"])
        bgg_source = details.get("_bgg_source", "")
        if bgg_source and bgg_source != "无":
            images_to_send.append(BGG_POWERED_BY_IMAGE_URL)

        for img_url in images_to_send:
            await self._try_send_image(img_url, stream_id=stream_id, proxy=cfg.ddgs.proxy or None)

        return True, f"已查询: {details.get('name')}", True

    @Command("boardgame_register", description="登记或删除桌游中英文名", pattern=r"^/桌游登记\s+(?P<cn_name>[^/]+)/(?P<en_name>[^/]*)$")
    async def handle_boardgame_register(self, **kwargs: Any) -> Tuple[bool, str, bool]:
        matched = kwargs.get("matched_groups", {})
        cn_name = matched.get("cn_name", "").strip()
        en_name = matched.get("en_name", "").strip()
        stream_id = kwargs["stream_id"]

        if cn_name == "清空":
            from .register import clear_alias_file
            ok = clear_alias_file()
            await self.ctx.send.text("✅ 已清空" if ok else "❌ 清空失败", stream_id)
            return True, "清空", True

        if en_name.lower() in ("删除", "delete", "remove", "del"):
            from .register import load_alias_from_file, save_alias
            existing = load_alias_from_file()
            if cn_name in existing:
                existing.pop(cn_name)
                ok = save_alias(existing)
                await self.ctx.send.text("✅ 已删除" if ok else "❌ 删除失败", stream_id)
                return True, "删除", True
            await self.ctx.send.text(f"⚠️「{cn_name}」未在词典中", stream_id)
            return True, "不存在", False

        if not cn_name or not en_name or not any(c.isalnum() for c in en_name):
            await self.ctx.send.text("❌ 格式：`/桌游登记 中文名/英文名`", stream_id)
            return True, "格式错误", True

        from .register import load_alias_from_file, save_alias
        existing = load_alias_from_file()
        if cn_name in existing:
            await self.ctx.send.text(f"⚠️ 已存在：{existing[cn_name]}，请先删除再录入", stream_id)
            return True, "已存在", False

        ok = save_alias({cn_name: en_name})
        await self.ctx.send.text(f"✅ 登记：{cn_name} → {en_name}" if ok else "❌ 保存失败", stream_id)
        return True, "成功", True

    async def _format_boardgame_reply(self, details: Dict[str, Any], cfg: BggSearchPluginConfig) -> Tuple[str, Dict[str, Any]]:
        cn_name = details.get("cn_name", "")
        en_name = details.get("name", "")
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
        cn_desc = details.get("cn_description") or ""
        jishi_score = details.get("jishi_score") or ""
        jishi_cats = details.get("jishi_categories") or []
        best_numplayers = details.get("best_numplayers", "")
        lang_dep = details.get("language_dependence") or details.get("language_requirement") or ""

        source = details.get("_source", "未知")
        name_source = details.get("_name_source", "")
        bgg_source = details.get("_bgg_source", "")
        final_query = details.get("_final_query", "")

        SOURCE_FRIENDLY = {
            "集石→BGG_API2": "🟢 集石提供名称 → BGG API2获取详情",
            "集石→BGG网页抓取": "🟡 集石提供名称 → BGG网页抓取详情",
            "集石→BGG(详情获取失败)": "🔴 集石提供名称 → BGG详情获取失败",
            "BGG_API2": "🟢 BGG API2 直接搜索",
            "BGG网页抓取(API获ID)": "🟡 BGG API2搜索 → 网页抓取详情",
            "BGG网页搜索": "🟡 BGG 网页搜索 + 网页抓取",
            "DDG+AI→BGG_API2": "🟠 DDG+AI翻译英文名 → BGG API2",
            "DDG+AI→BGG网页抓取(API获ID)": "🟠 DDG+AI翻译英文名 → BGG网页抓取",
            "DDG+AI→BGG网页搜索": "🟠 DDG+AI翻译英文名 → BGG网页搜索",
            "Tavily+AI→BGG_API2": "🟢 Tavily+AI翻译英文名 → BGG API2",
            "Tavily+AI→BGG网页抓取(API获ID)": "🟡 Tavily+AI翻译英文名 → BGG网页抓取",
            "Tavily+AI→BGG网页搜索": "🟡 Tavily+AI翻译英文名 → BGG网页搜索",
            "DDG+Tavily+AI→BGG_API2": "🟢 DDG+Tavily融合搜索+AI → BGG API2",
            "DDG+Tavily+AI→BGG网页抓取(API获ID)": "🟡 DDG+Tavily融合搜索+AI → BGG网页抓取",
            "DDG+Tavily+AI→BGG网页搜索": "🟡 DDG+Tavily融合搜索+AI → BGG网页搜索",
            "词典→BGG_API2": "🟢 词典 → BGG API2",
            "词典→BGG网页抓取(API获ID)": "🟡 词典 → BGG网页抓取",
            "词典→BGG网页搜索": "🟡 词典 → BGG网页搜索",
            "集石→DDG+AI→BGG_API2": "🟠 集石(转DDG) → BGG API2",
            "集石→DDG+AI→BGG网页抓取(API获ID)": "🟠 集石(转DDG) → BGG网页抓取",
            "全部方案失败": "🔴 全部方案失败",
        }
        source_display = SOURCE_FRIENDLY.get(source, f"ℹ️ {source}")

        translated_categories, translated_mechanics, translated_desc = [], [], desc
        if cfg.ai_translate.enabled and (categories or mechanics):
            ai_response = await self._call_llm_text(f"类型：{', '.join(categories)}\n机制：{', '.join(mechanics)}\n简介：{desc}\n\n请按'类型：\n机制：\n简介：'格式翻译为中文。")
            if ai_response:
                for line in ai_response.split("\n"):
                    if line.startswith("类型："):
                        translated_categories = [x.strip() for x in line.replace("类型：", "").split("、") if x.strip()]
                    elif line.startswith("机制："):
                        translated_mechanics = [x.strip() for x in line.replace("机制：", "").split("、") if x.strip()]
                    elif line.startswith("简介："):
                        translated_desc = line.replace("简介：", "").strip()

        term_map = load_terms()
        if not translated_categories and categories:
            translated_categories = [term_map.get(c, c) for c in categories]
        if not translated_mechanics and mechanics:
            translated_mechanics = [term_map.get(m, m) for m in mechanics]

        desc_display = translated_desc or cn_desc or desc or "暂无简介"
        if len(desc_display) > 300:
            desc_display = desc_display[:300] + "..."

        lines = [
            f"🇨🇳 中文名：{cn_name}",
            f"🇺🇸 英文名：{en_name}",
            f"📅 年份：{year}",
            f"🏆 排名：{rank} ⭐ 评分：{avg}/10",
            f"🧠 重度：{avgw}/5 👥 {minp}-{maxp}人 ⏳ {mint}-{maxt}分 🚼 {minage}+",
            f"📚 类型：{'、'.join(translated_categories[:5]) or '暂无'}",
            f"⚙️ 机制：{'、'.join(translated_mechanics[:5]) or '暂无'}",
        ]
        if jishi_score:
            lines.append(f"🔥 集石评分：{jishi_score}")
        if jishi_cats:
            lines.append(f"🏷️ 集石分类：{'、'.join(jishi_cats)}")
        if best_numplayers:
            lines.append(f"👍 最佳人数：{best_numplayers}人")
        if lang_dep:
            lines.append(f"🌍 语言依赖：{lang_dep}")

        lines.append(f"📝 {desc_display}")
        lines.append(f"\n📋 查询方案：{source_display}")
        if final_query:
            lines.append(f"🔍 最终通过搜索词「{final_query}」命中 BGG 条目")
        if bgg_source and bgg_source != "无":
            lines.append("ℹ️ Powered by BGG（logo 将单独发送）")
        lines.append(f"🔗 {bgg_url}")

        text = "\n".join(lines)
        image_url = details.get("image", "")
        data = {"cn_name": cn_name, "en_name": en_name, "image": image_url}
        return text, data

    async def _try_send_image(self, image_url: str, stream_id: str, proxy: str | None) -> None:
        try:
            from .utils import HEADERS
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, proxy=proxy) as client:
                resp = await client.get(image_url, headers={"User-Agent": HEADERS["User-Agent"], "Accept": "image/*"})
                resp.raise_for_status()
                if not (resp.headers.get("content-type") or "").lower().startswith("image/"):
                    return
                await self.ctx.send.image(image_data=base64.b64encode(resp.content).decode(), stream_id=stream_id)
        except Exception as e:
            self.ctx.logger.error("[图片] %s", e)


def create_plugin() -> BggSearchPlugin:
    return BggSearchPlugin()
