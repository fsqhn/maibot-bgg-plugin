import logging
from typing import List, Tuple, Optional, Callable, Awaitable, Any
from ddgs import DDGS

logger = logging.getLogger("bgg_search_plugin.ddg_client")

COMMON_TERMS_TO_FILTER = [
    "tabletop game", "board game", "boardgame", "card game",
    "dice game", "party game", "strategy game", "family game",
    "game", "tabletop",
]

def build_extract_prompt(results: list) -> str:
    """根据搜索结果构建 LLM 提取 prompt"""
    prompt = (
        "从以下搜索结果中提取桌游的英文名称。\n"
        "1. 不要提取通用词汇，要具体的桌游名称\n"
        "2. 只提取和查询词最相关的一个桌游英文名\n"
        "输出格式：\n中文名：[中文名称]\n英文名：[英文名称]\n\n"
        "搜索结果：\n\n"
    )
    for idx, r in enumerate(results):
        prompt += f"结果{idx+1}:\n标题：{r.get('title', '')}\n摘要：{r.get('body', '')[:200]}\n\n"
    return prompt

def parse_llm_names(llm_response: str) -> Tuple[List[str], str]:
    """解析 LLM 返回的中英文名"""
    lines = [l.strip() for l in llm_response.split("\n") if l.strip()]
    cn_name, en_candidates = "", []
    for line in lines:
        if line.startswith("中文名："):
            cn_name = line.replace("中文名：", "").strip()
        elif line.startswith("英文名："):
            en = line.replace("英文名：", "").strip()
            if (en
                    and en.lower() not in COMMON_TERMS_TO_FILTER
                    and len(en) >= 3
                    and not en.lower().startswith("http")):
                en_candidates.append(en)
    seen = set()
    return [x for x in en_candidates if not (x in seen or seen.add(x))], cn_name

async def fetch_ddg_raw_results(
    keyword: str,
    proxy: Optional[str] = None,
    verbose: bool = False,
    logger: Any = None,
) -> List[dict]:
    """仅负责 DDG 搜索，返回原始结果列表，不调用 LLM"""
    log = logger or logging.getLogger("bgg_search_plugin.ddg_client")
    try:
        search_query = f"{keyword} 桌游的英文名是什么"
        if verbose:
            log.info("[DDG] 搜索: %s", search_query)
        results = list(DDGS(proxy=proxy if proxy else None).text(
            search_query, region="zh-CN", max_results=10
        ))
        if verbose:
            log.info("[DDG] 搜索返回 %d 条结果", len(results))
        return results
    except Exception as e:
        log.error("[DDG] 搜索异常: %s", e)
        return []

async def extract_english_from_merged_results(
    results: list,
    verbose: bool = False,
    llm_caller: Optional[Callable[[str], Awaitable[str]]] = None,
    logger: Any = None,
) -> Tuple[List[str], str]:
    """
    接收已合并去重的搜索结果列表，调用 LLM 一次性提取英文名。
    供 bgg_client.py 调用，避免 LLM 被重复调用。
    """
    log = logger or logging.getLogger("bgg_search_plugin.ddg_client")
    if not llm_caller:
        raise ValueError("缺少 llm_caller，无法调用 LLM 提取英文名")
    if not results:
        raise ValueError("搜索结果为空")

    prompt = build_extract_prompt(results)
    if verbose:
        log.info("[合并提取] 调用LLM提取英文名（共 %d 条搜索结果）...", len(results))

    llm_response = await llm_caller(prompt)

    if verbose:
        log.info("[合并提取] LLM返回: %s", llm_response[:200] if llm_response else "(空)")

    unique, cn_name = parse_llm_names(llm_response)

    if not unique:
        raise ValueError(f"LLM未提取到有效英文名，原始返回: {llm_response[:200]}")

    if verbose:
        log.info("[合并提取] 最终结果: cn=%s, en_candidates=%s", cn_name, unique)

    return unique, cn_name

# 保留原有函数作为兼容，内部调用新函数
async def fetch_english_candidates_from_ddg(
    keyword: str,
    proxy: Optional[str] = None,
    verbose: bool = False,
    llm_caller: Optional[Callable[[str], Awaitable[str]]] = None,
    logger: Any = None,
) -> Tuple[List[str], str]:
    """DDG 搜索 + LLM 提取英文名"""
    log = logger or logging.getLogger("bgg_search_plugin.ddg_client")
    if not llm_caller:
        raise ValueError("缺少 llm_caller，无法调用 LLM 提取英文名")

    try:
        results = await fetch_ddg_raw_results(keyword, proxy, verbose, log)
        if not results:
            raise ValueError("DDG搜索无结果")

        return await extract_english_from_merged_results(
            results, verbose=verbose, llm_caller=llm_caller, logger=log
        )

    except Exception as e:
        if log:
            log.error("[DDG] 异常: %s", e)
        raise
