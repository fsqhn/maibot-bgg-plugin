import logging
from typing import List, Tuple, Optional, Callable, Awaitable, Any
from ddgs import DDGS

logger = logging.getLogger("bgg_search_plugin.ddg_client")

COMMON_TERMS_TO_FILTER = [
    "tabletop game", "board game", "boardgame", "card game", "dice game",
    "party game", "strategy game", "family game", "game", "tabletop",
]

def _build_extract_prompt(results: list) -> str:
    prompt = (
        "从以下搜索结果中提取桌游的英文名称。\n"
        "1. 不要提取通用词汇，要具体的桌游名称\n"
        "输出格式：\n中文名：[中文名称]\n英文名：[英文名称]\n\n"
        "搜索结果：\n\n"
    )
    for idx, r in enumerate(results):
        prompt += f"结果{idx+1}:\n标题：{r.get('title', '')}\n摘要：{r.get('body', '')[:200]}\n\n"
    return prompt

def _parse_llm_names(llm_response: str) -> Tuple[List[str], str]:
    lines = [l.strip() for l in llm_response.split("\n") if l.strip()]
    cn_name, en_candidates = "", []
    for line in lines:
        if line.startswith("中文名："): cn_name = line.replace("中文名：", "").strip()
        elif line.startswith("英文名："):
            en = line.replace("英文名：", "").strip()
            if en and en.lower() not in COMMON_TERMS_TO_FILTER and len(en) >= 3 and not en.lower().startswith("http"):
                en_candidates.append(en)
    seen = set()
    return [x for x in en_candidates if not (x in seen or seen.add(x))], cn_name

async def fetch_english_candidates_from_ddg(
    keyword: str,
    proxy: Optional[str] = None,
    verbose: bool = False,
    llm_caller: Optional[Callable[[str], Awaitable[str]]] = None,
    logger: Any = None,
) -> Tuple[List[str], str]:
    """DDG搜索 + LLM提取英文名。llm_caller 必须由 plugin.py 传入"""
    log = logger or logging.getLogger("bgg_search_plugin.ddg_client")

    if not llm_caller:
        raise ValueError("缺少 llm_caller，无法调用 LLM 提取英文名")

    try:
        search_query = f"{keyword}的英文名是什么"
        if verbose: log.info("[DDG] 搜索: %s", search_query)
        results = list(DDGS(proxy=proxy if proxy else None).text(search_query, region="zh-CN", max_results=10))
        if verbose: log.info("[DDG] 搜索返回 %d 条结果", len(results))
        if not results: raise ValueError("DDG搜索无结果")

        prompt = _build_extract_prompt(results)
        if verbose: log.info("[DDG] 调用LLM提取英文名...")
        
        llm_response = await llm_caller(prompt)
        if verbose: log.info("[DDG] LLM返回: %s", llm_response[:200] if llm_response else "(空)")

        unique, cn_name = _parse_llm_names(llm_response)
        if not unique: raise ValueError(f"LLM未提取到有效英文名，原始返回: {llm_response[:200]}")
        
        if verbose: log.info("[DDG] 最终结果: cn=%s, en_candidates=%s", cn_name, unique)
        return unique, cn_name
    except Exception as e:
        if log: log.error("[DDG] 异常: %s", e)
        raise
