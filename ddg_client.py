from typing import List, Tuple, Optional
from ddgs import DDGS
from src.plugin_system.apis import llm_api
from src.common.logger import get_logger

logger = get_logger("bgg_search_plugin.ddg_client")


async def fetch_english_candidates_from_ddg(
    keyword: str,
    proxy: Optional[str] = None,
    verbose: bool = False,
) -> Tuple[List[str], str]:
    candidates = []
    try:
        ddgs_proxy = proxy if proxy else None
        search_query = f"{keyword}的英文名是什么"
        if verbose:
            logger.info(f"[DDGS 搜索] 开始搜索关键词: {search_query}")

        results = list(DDGS(proxy=ddgs_proxy).text(search_query, region="zh-CN", max_results=20))

        if verbose:
            logger.info(f"[DDGS 搜索] 共获取 {len(results)} 条结果")
            # 新增：显示每条搜索结果
            logger.info("[DDGS 搜索] 搜索结果详情：")
            for idx, r in enumerate(results):
                title = r.get("title", "")
                body = r.get("body", "")
                logger.info(f"  结果 {idx+1}:")
                logger.info(f"    标题: {title}")
                logger.info(f"    摘要: {body[:150]}{'...' if len(body) > 150 else ''}")

        models = llm_api.get_available_models()
        if not models:
            print("没有可用的LLM模型")
            return []

        target_key = "utils"
        if target_key in models:
            model_config = models[target_key]
            if verbose:
                logger.info(f"[LLM 提取] 指定使用模型: {target_key}")
        else:
            logger.warning(f"[LLM 提取] 系统中未找到 {target_key} 模型，尝试回退策略...")
            for name, conf in models.items():
                if name != "replay":
                    model_config = conf
                    if verbose:
                        logger.info(f"[LLM 提取] 回退使用模型: {name}")
                    break

        prompt = (
            "从以下搜索结果中提取桌游的英文名称。\n"
            "注意：\n"
            "1. 请提取具体的桌游名称，不要提取通用词汇（如'tabletop game'、'board game'等）\n"
            "2. 优先从标题中提取，标题无英文则检查摘要\n"
            "3. 英文名称通常包含多个单词，不是单个通用词\n"
            "4. 忽略搜索结果中的广告、无关内容\n"
            "5. 同时提取中文名称\n"
            "\n"
            "输出格式（严格按照此格式）：\n"
            "中文名：[中文名称]\n"
            "英文名：[英文名称]\n"
            "\n"
            "搜索结果如下：\n\n"
        )
        for idx, r in enumerate(results):
            title = r.get("title", "")
            body = r.get("body", "")
            prompt += f"结果 {idx+1}:\n标题：{title}\n摘要：{body[:200]}...\n\n"

        if verbose:
            logger.info("[LLM 提取] 正在调用LLM处理搜索结果...")
            logger.info(f"[LLM 提取] 发送给 LLM 的 prompt（前500字符）: {prompt[:500]}...")

        success, llm_response, _, used_model = await llm_api.generate_with_model(
            prompt=prompt,
            model_config=model_config,
            request_type="plugin.generate",
        )

        if verbose:
            logger.info(f"[LLM 提取] LLM 原始响应: {llm_response}")

        if not success or not llm_response or not llm_response.strip():
            raise ValueError("LLM生成失败或返回空结果")

        lines = [line.strip() for line in llm_response.split("\n") if line.strip()]
        cn_name = ""
        en_candidates = []

        COMMON_TERMS_TO_FILTER = [
            "tabletop game",
            "board game",
            "boardgame",
            "card game",
            "dice game",
            "party game",
            "strategy game",
            "family game",
            "game",
            "tabletop",
        ]

        for line in lines:
            if line.startswith("中文名："):
                cn_name = line.replace("中文名：", "").strip()
            elif line.startswith("英文名："):
                en_name = line.replace("英文名：", "").strip()
                if en_name:
                    en_name_lower = en_name.lower().strip()
                    if (
                        en_name_lower not in COMMON_TERMS_TO_FILTER
                        and len(en_name) >= 3
                        and not en_name_lower.startswith("http")
                    ):
                        en_candidates.append(en_name)

        if not en_candidates:
            raise ValueError("LLM未提取到有效英文名")

        seen = set()
        unique_candidates = []
        for name in en_candidates:
            if name not in seen:
                seen.add(name)
                unique_candidates.append(name)

        if verbose:
            logger.info(f"[LLM 提取] 最终提取结果 - 中文名: {cn_name}, 英文名候选: {unique_candidates}")
        return unique_candidates, cn_name

    except Exception as e:
        logger.error(f"[LLM 提取异常] {e}")
        raise
