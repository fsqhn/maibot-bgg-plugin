from typing import Optional, Any, Callable, Awaitable, List, Tuple
import logging
import httpx

logger = logging.getLogger("bgg_search_plugin.bgg_client")

from .utils import load_alias
from .jishi_client import search_jishi_games, fetch_jishi_detail, select_best_match
from .ddg_client import fetch_ddg_raw_results, extract_english_from_merged_results
from .tavily_client import search_tavily
from .bgg_api import bgg_thing_details_api, _try_bgg_full_search

# ============================================================
# 集石辅助函数
# ============================================================
async def _try_jishi(cn_name: str, jishi_cookie: str, proxy: Optional[str], verbose: bool, log: Any) -> Optional[dict]:
    jishi_candidates = await search_jishi_games(cn_name, jishi_cookie, proxy, verbose)
    if not jishi_candidates:
        return None
    detailed_candidates = []
    for cand in jishi_candidates:
        detail = await fetch_jishi_detail(cand['jishi_id'], jishi_cookie, proxy, verbose)
        if detail:
            detailed_candidates.append(detail)
    if not detailed_candidates:
        return None
    return select_best_match(cn_name, detailed_candidates)

def _enrich_with_jishi_info(details: dict, jishi_result: dict) -> dict:
    if jishi_result.get("cn_name"):
        details["cn_name"] = jishi_result["cn_name"]
    for key in ("cn_description", "jishi_score", "language_requirement", "table_requirement", "jishi_categories"):
        if jishi_result.get(key):
            details[key] = jishi_result[key]
    return details

def _make_jishi_partial_result(jishi_result: dict, source: str, name_source: str, bgg_source: str) -> dict:
    return {
        "name": jishi_result.get("en_name", ""),
        "cn_name": jishi_result.get("cn_name"),
        "bgg_id": jishi_result.get("bgg_id"),
        "bgg_url": (f"https://boardgamegeek.com/boardgame/{jishi_result['bgg_id']}" if jishi_result.get("bgg_id") else ""),
        "bgg_failed": True,
        "cn_description": jishi_result.get("cn_description"),
        "jishi_score": jishi_result.get("jishi_score"),
        "language_requirement": jishi_result.get("language_requirement"),
        "_source": source, "_name_source": name_source, "_bgg_source": bgg_source,
    }

# ============================================================
# 主入口：集石 → BGG直接搜索(API2) → DDG+Tavily+AI
# ============================================================
async def resolve_boardgame_by_cn_name(
    cn_name: str,
    proxy: Optional[str] = None,
    verbose: bool = False,
    api_token: Optional[str] = None,
    jishi_cookie: Optional[str] = None,
    tavily_api_key: Optional[str] = None,
    llm_caller: Optional[Callable[[str], Awaitable[str]]] = None,
    custom_logger: Any = None,
) -> Optional[dict]:
    log = custom_logger or logger

    # Step 1: 本地词典
    alias_dict = load_alias()
    raw = alias_dict.get(cn_name, "").strip()
    candidates: List[str] = []
    from_alias = False
    if raw:
        candidates = [c.strip() for c in raw.split("|") if c.strip()]
        from_alias = True
        if verbose: log.info(f"[数据来源] 从本地词典找到候选: {candidates}")
    extracted_cn_name = cn_name

    client = httpx.AsyncClient(timeout=20.0, proxy=proxy)
    try:
        # Step 2: 集石
        jishi_result = None
        if jishi_cookie and not from_alias:
            if verbose: log.info("[流程] Step 2: 尝试集石数据源...")
            jishi_result = await _try_jishi(cn_name, jishi_cookie, proxy, verbose, log)
            if jishi_result:
                if jishi_result.get("bgg_id"):
                    if verbose: log.info(f"[流程] 集石命中 (BGG ID={jishi_result['bgg_id']})，获取BGG详情")
                    details = await bgg_thing_details_api(jishi_result["bgg_id"], client, verbose, api_token, custom_logger)
                    if details:
                        details = _enrich_with_jishi_info(details, jishi_result)
                        details.update({"_source": "集石→BGG_API2", "_name_source": "集石", "_bgg_source": "BGG_API2"})
                        return details
                    bgg_url = f"https://boardgamegeek.com/boardgame/{jishi_result['bgg_id']}"
                    from .bgg_api import web_thing_details
                    details = await web_thing_details(bgg_url, client, verbose=verbose)
                    if details:
                        details = _enrich_with_jishi_info(details, jishi_result)
                        details.update({"_source": "集石→BGG网页抓取", "_name_source": "集石", "_bgg_source": "BGG网页抓取"})
                        return details
                    return _make_jishi_partial_result(jishi_result, "集石→BGG(详情获取失败)", "集石", "BGG获取失败")
                else:
                    jishi_cn = jishi_result.get("cn_name", "")
                    if jishi_cn and jishi_cn not in candidates:
                        candidates.insert(0, jishi_cn)
                        if verbose: log.info(f"[流程] 集石有结果但无BGG ID，加入候选: {jishi_cn}")

        # Step 3: BGG 直接搜索
        search_list: List[str] = []
        if not from_alias: search_list.append(cn_name)
        search_list.extend(candidates)
        seen: set = set()
        unique_search: List[str] = []
        for s in search_list:
            sl = s.lower()
            if sl not in seen:
                seen.add(sl)
                unique_search.append(s)
        if verbose: log.info(f"[流程] Step 3: BGG直接搜索(API2)，候选: {unique_search}")
        for q in unique_search:
            result = await _try_bgg_full_search(q, client, verbose, api_token, custom_logger)
            if result:
                details, bgg_source = result
                details["_final_query"] = q
                details["cn_name"] = extracted_cn_name
                if jishi_result:
                    details = _enrich_with_jishi_info(details, jishi_result)
                    details["_source"] = f"集石→{bgg_source}"
                    details["_name_source"] = "集石"
                elif from_alias:
                    details["_source"] = f"词典→{bgg_source}"
                    details["_name_source"] = "词典"
                else:
                    details["_source"] = bgg_source
                    details["_name_source"] = "BGG搜索"
                details["_bgg_source"] = bgg_source
                return details

        # Step 4: DDG + Tavily + AI 兜底
        if verbose: log.info("[流程] Step 4: BGG直接搜索无结果，启动DDG+Tavily+AI兜底...")
        ddg_candidates: List[str] = []
        search_source_used = "DDG+AI"

        if llm_caller:
            import asyncio as _asyncio

            # 并行发起 DDG 和 Tavily 搜索
            async def _do_ddg():
                try:
                    return await fetch_ddg_raw_results(cn_name, proxy, verbose, log)
                except Exception as e:
                    if verbose: log.warning(f"[DDG] 搜索失败: {e}")
                    return []

            async def _do_tavily():
                if not tavily_api_key:
                    if verbose: log.info("[Tavily] 未配置API Key，跳过")
                    return []
                try:
                    return await search_tavily(keyword=cn_name, api_key=tavily_api_key, proxy=proxy, max_results=5, verbose=verbose, custom_logger=log)
                except Exception as e:
                    if verbose: log.warning(f"[Tavily] 搜索失败: {e}")
                    return []

            ddg_results, tavily_results = await _asyncio.gather(_do_ddg(), _do_tavily())

            if verbose: log.info(f"[Step4] DDG返回{len(ddg_results)}条，Tavily返回{len(tavily_results)}条")

            # 合并去重
            merged_results: List[dict] = []
            seen_urls: set = set()
            for r in ddg_results:
                url = r.get("href", "") or r.get("link", "")
                if url not in seen_urls:
                    seen_urls.add(url)
                    merged_results.append({"title": r.get("title", ""), "body": r.get("body", "") or r.get("desc", ""), "href": url})
            for r in tavily_results:
                url = r.get("href", "")
                if url not in seen_urls:
                    seen_urls.add(url)
                    merged_results.append(r)

            if verbose: log.info(f"[Step4] 合并去重后共 {len(merged_results)} 条结果")
            # ===== 打印喂给 LLM 的完整搜索结果（便于人工审视） =====
            if verbose and log.isEnabledFor(logging.INFO):
                log.info("[合并搜索结果-喂给LLM的完整内容]")
                for idx, r in enumerate(merged_results):
                    title = r.get("title", "")
                    body = r.get("body", "")
                    href = r.get("href", "")
                    # 限制单条长度，避免刷屏
                    body_preview = body[:300] + "..." if len(body) > 300 else body
                    log.info(f"  [{idx+1}] 标题: {title}")
                    log.info(f"       链接: {href}")
                    log.info(f"       内容: {body_preview}")
                log.info("[合并搜索结果-结束]")
            # ===== END =====
            # 统一 LLM 提取
            if merged_results:
                try:
                    ddg_candidates, ddg_cn_name = await extract_english_from_merged_results(merged_results, verbose, llm_caller, log)
                    if ddg_cn_name: extracted_cn_name = ddg_cn_name
                    candidates.extend(ddg_candidates)

                    if ddg_results and tavily_results:
                        search_source_used = "DDG+Tavily+AI"
                    elif tavily_results:
                        search_source_used = "Tavily+AI"
                    else:
                        search_source_used = "DDG+AI"
                except Exception as e:
                    if verbose: log.warning(f"[Step4] LLM提取异常: {e}")

        # 用提取到的英文名再走一遍 BGG 全链路
        ddg_search: List[str] = []
        seen2: set = set()
        for c in candidates:
            cl = c.lower()
            if cl not in seen2:
                seen2.add(cl)
                ddg_search.append(c)
        if verbose and ddg_search: log.info(f"[流程] {search_source_used} 候选: {ddg_search}")
        for q in ddg_search:
            result = await _try_bgg_full_search(q, client, verbose, api_token, custom_logger)
            if result:
                details, bgg_source = result
                details["_final_query"] = q
                details["cn_name"] = extracted_cn_name
                if jishi_result:
                    details = _enrich_with_jishi_info(details, jishi_result)
                    details["_source"] = f"集石→{search_source_used}→{bgg_source}"
                    details["_name_source"] = "集石(转搜索)"
                elif from_alias:
                    details["_source"] = f"词典→{search_source_used}→{bgg_source}"
                    details["_name_source"] = "词典(转搜索)"
                else:
                    details["_source"] = f"{search_source_used}→{bgg_source}"
                    details["_name_source"] = search_source_used
                details["_bgg_source"] = bgg_source
                return details

        # 全部失败
        if not candidates: return None
        return {
            "name": candidates[0] if candidates else "", "cn_name": extracted_cn_name, "bgg_failed": True,
            "_source": "全部方案失败", "_name_source": "词典" if from_alias else search_source_used, "_bgg_source": "无",
        }
    finally:
        await client.aclose()
