from typing import Optional, Any, Callable, Awaitable
import logging
import httpx
import xml.etree.ElementTree as ET

# 修复沙箱报错：不再使用 from src.common.logger import get_logger
logger = logging.getLogger("bgg_search_plugin.bgg_client")

from .utils import make_bgg_api_headers, BGG_API_SEARCH_V2, BGG_API_SEARCH_V1, BGG_API_THING
from .jishi_client import search_jishi_games, fetch_jishi_detail, select_best_match
from .ddg_client import fetch_english_candidates_from_ddg
from .web_client import bgg_search_by_name, bgg_thing_details as web_thing_details
from .utils import load_alias

headers = make_bgg_api_headers()

async def bgg_search_api_by_name(
    query: str,
    client: httpx.AsyncClient,
    verbose: bool = False,
    api_token: Optional[str] = None,
    custom_logger: Any = None,
) -> Optional[str]:
    log = custom_logger or logger
    headers = make_bgg_api_headers(api_token)
    
    def is_likely_primary(name: str, query: str) -> int:
        score = 0
        name_lower = name.lower()
        query_lower = query.lower()
        if name_lower == query_lower: score += 100
        elif name_lower.startswith(query_lower): score += 80
        if any(x in name_lower for x in ("promo", "expansion", "exp.", " – ", ": ", " - ")): score -= 50
        name_stripped = name_lower
        for suffix in (": second edition", " – second edition", ": 2nd edition", " – 2nd edition", ": 3rd edition", " – 3rd edition", " (second edition)", " (3rd edition)",):
            name_stripped = name_stripped.replace(suffix, "")
            if name_stripped == query_lower or name_stripped.startswith(query_lower): score += 70
        return score

    def collect_from_api2(body: str, query: str) -> list:
        items_with_score = []
        try:
            root = ET.fromstring(body.encode("utf-8"))
            items = root.findall(".//item[@type='boardgame']")
            for it in items:
                it_id = it.get("id", "")
                name_node = it.find("./name[@type='primary']")
                name = name_node.get("value") if name_node is not None else ""
                if it_id and name:
                    score = is_likely_primary(name, query)
                    items_with_score.append({"id": it_id, "name": name, "score": score, "source": "V2"})
        except ET.ParseError as e:
            log.error(f"[BGG API 搜索 V2] XML 解析失败: {e}")
        return items_with_score

    def collect_from_api1(body: str, query: str) -> list:
        items_with_score = []
        try:
            root = ET.fromstring(body.encode("utf-8"))
            items = root.findall("boardgame")
            for it in items:
                it_id = it.get("objectid", "")
                name_node = it.find("./name[@primary='true']")
                name = name_node.text if name_node is not None else ""
                if it_id and name:
                    score = is_likely_primary(name, query)
                    items_with_score.append({"id": it_id, "name": name, "score": score, "source": "V1"})
        except ET.ParseError as e:
            log.error(f"[BGG API 搜索 V1] XML 解析失败: {e}")
        return items_with_score

    url_v2 = BGG_API_SEARCH_V2
    params_v2 = {"query": query, "type": "boardgame"}
    try:
        if verbose: log.info(f"[BGG API 搜索 V2] 正在搜索: {query}")
        resp = await client.get(url_v2, params=params_v2, headers=headers, timeout=15.0)
        if verbose: log.info(f"[BGG API 搜索 V2] HTTP 状态码: {resp.status_code}")
        body = resp.text
        if resp.status_code == 200:
            candidates = collect_from_api2(body, query)
            if not candidates:
                if verbose: log.info("[BGG API 搜索 V2] 未找到 boardgame 类型的结果")
            else:
                candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)
                for cand in candidates:
                    game_id = cand["id"]
                    name = cand["name"]
                    if verbose: log.info(f"[BGG API 搜索 V2] 尝试用候选: {name} (id={game_id})")
                    details = await bgg_thing_details_api(game_id, client, verbose, api_token, custom_logger)
                    if details:
                        if verbose: log.info(f"[BGG API 搜索 V2] 候选 {name} (id={game_id}) 详情获取成功")
                        return game_id
                    else:
                        if verbose: log.warning(f"[BGG API 搜索 V2] 候选 {name} (id={game_id}) 详情失败")
        else:
            if verbose: log.info(f"[BGG API 搜索 V2] 非预期状态码: {resp.status_code}")
    except Exception as e:
        log.error(f"[BGG API 搜索 V2] 请求异常: {e}")

    url_v1 = BGG_API_SEARCH_V1
    params_v1 = {"search": query}
    try:
        if verbose: log.info(f"[BGG API 搜索 V1] 降级尝试搜索: {query}")
        resp = await client.get(url_v1, params=params_v1, headers=headers, timeout=15.0)
        if verbose: log.info(f"[BGG API 搜索 V1] HTTP 状态码: {resp.status_code}")
        body = resp.text
        if resp.status_code == 200:
            candidates = collect_from_api1(body, query)
            if not candidates:
                if verbose: log.info("[BGG API 搜索 V1] 未找到 boardgame 类型的结果")
            else:
                candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)
                for cand in candidates:
                    game_id = cand["id"]
                    name = cand["name"]
                    if verbose: log.info(f"[BGG API 搜索 V1] 尝试用候选: {name} (id={game_id})")
                    details = await bgg_thing_details_api(game_id, client, verbose, api_token, custom_logger)
                    if details:
                        if verbose: log.info(f"[BGG API 搜索 V1] 候选 {name} (id={game_id}) 详情获取成功")
                        return game_id
                    else:
                        if verbose: log.warning(f"[BGG API 搜索 V1] 候选 {name} (id={game_id}) 详情失败")
        else:
            if verbose: log.info(f"[BGG API 搜索 V1] 非预期状态码: {resp.status_code}")
    except Exception as e:
        log.error(f"[BGG API 搜索 V1] 请求异常: {e}")

    if verbose: log.info("[BGG API 搜索] API2 与 API1 均未返回有效结果")
    return None


async def bgg_thing_details_api(
    game_id: str,
    client: httpx.AsyncClient,
    verbose: bool = False,
    api_token: Optional[str] = None,
    custom_logger: Any = None,
) -> Optional[dict]:
    log = custom_logger or logger
    try:
        params = {"id": game_id, "stats": "1"}
        headers = make_bgg_api_headers(api_token)
        if verbose: log.info(f"[BGG API 详情] 正在获取 ID: {game_id} 的详情")
        resp = await client.get(BGG_API_THING, params=params, headers=headers, timeout=20.0)
        if resp.status_code != 200:
            log.warning(f"[BGG API 详情] 状态码异常: {resp.status_code}")
            return None

        root = ET.fromstring(resp.text.encode("utf-8"))
        item = root.find(".//item[@type='boardgame']")
        if item is None:
            log.warning("[BGG API 详情] 未找到 boardgame item 节点")
            return None

        import html
        def get_attr(parent, tag, attr="value", default="?"):
            node = parent.find(tag)
            return node.get(attr, default) if node is not None else default

        def get_text(parent, tag, default=""):
            node = parent.find(tag)
            return html.unescape(node.text) if node is not None and node.text else default

        name_node = item.find("./name[@type='primary']")
        game_name = name_node.get("value") if name_node is not None else "Unknown"

        rank_node = item.find(".//statistics/ratings/ranks/rank[@name='boardgame']")
        overall_rank = rank_node.get("value") if rank_node is not None else "N/A"
        strategy_rank_node = item.find(".//statistics/ratings/ranks/rank[@name='strategygames']")
        strategy_rank = strategy_rank_node.get("value") if strategy_rank_node is not None else "N/A"

        categories = [cat_node.get("value", "") for cat_node in item.findall(".//link[@type='boardgamecategory']") if cat_node.get("value")]
        mechanics = [mech_node.get("value", "") for mech_node in item.findall(".//link[@type='boardgamemechanic']") if mech_node.get("value")]

        best_numplayers = ""
        best_poll = item.find(".//poll[@name='suggested_numplayers']")
        if best_poll is not None:
            best_votes = 0
            for result_node in best_poll.findall(".//results"):
                numplayers = result_node.get("numplayers", "")
                best_result = result_node.find(".//result[@value='Best']")
                if best_result is not None:
                    numvotes = int(best_result.get("numvotes", "0") or "0")
                    if numvotes > best_votes:
                        best_votes = numvotes
                        best_numplayers = numplayers

        lang_dependence = ""
        lang_poll = item.find(".//poll[@name='language_dependence']")
        if lang_poll is not None:
            best_result = None
            max_votes = -1
            for result_node in lang_poll.findall(".//result"):
                try: numvotes = int(result_node.get("numvotes", "0"))
                except ValueError: numvotes = 0
                if numvotes > max_votes:
                    max_votes = numvotes
                    best_result = result_node
            if best_result is not None:
                lang_value = best_result.get("value", "")
                lang_level = best_result.get("level", "")
                if lang_value:
                    level_map = {"1": "无需阅读", "2": "轻微依赖", "3": "中度依赖", "4": "高度依赖", "5": "极度依赖"}
                    level_text = level_map.get(lang_level, "")
                    lang_dependence = f"{lang_value}（{level_text}）" if level_text else lang_value

        return {
            "bgg_id": game_id, "name": game_name, "year": get_attr(item, "yearpublished"),
            "description": get_text(item, "description"),
            "min_players": get_attr(item, "minplayers"), "max_players": get_attr(item, "maxplayers"),
            "min_time": get_attr(item, "minplaytime"), "max_time": get_attr(item, "maxplaytime"),
            "min_age": get_attr(item, "minage"), "users_rated": get_attr(item, "statistics/ratings/usersrated"),
            "average": get_attr(item, "statistics/ratings/average"), "avg_weight": get_attr(item, "statistics/ratings/averageweight"),
            "rank": str(overall_rank), "strategy_rank": str(strategy_rank),
            "image": get_text(item, "image"), "bgg_url": f"https://boardgamegeek.com/boardgame/{game_id}",
            "categories": categories, "mechanics": mechanics,
            "best_numplayers": best_numplayers, "language_dependence": lang_dependence,
        }
    except Exception as e:
        log.error(f"[BGG API 详情异常] {e}")
        return None


async def resolve_boardgame_by_cn_name(
    cn_name: str,
    proxy: Optional[str] = None,
    verbose: bool = False,
    api_token: Optional[str] = None,
    jishi_cookie: Optional[str] = None,
    llm_caller: Optional[Callable[[str], Awaitable[str]]] = None,
    custom_logger: Any = None,
) -> Optional[dict]:
    log = custom_logger or logger
    
    alias_dict = load_alias()
    raw = alias_dict.get(cn_name, "").strip()
    candidates = []
    from_alias = False
    if raw:
        candidates = [c.strip() for c in raw.split("|") if c.strip()]
        from_alias = True
        if verbose: log.info(f"[数据来源] 从本地词典找到英文名候选: {candidates}")

    extracted_cn_name = cn_name

    jishi_result = None
    if jishi_cookie and not from_alias:
        if verbose: log.info(f"[流程] 尝试集石数据源 (热度排序)...")
        jishi_candidates = await search_jishi_games(cn_name, jishi_cookie, proxy, verbose)
        if jishi_candidates:
            detailed_candidates = []
            for cand in jishi_candidates:
                detail = await fetch_jishi_detail(cand['jishi_id'], jishi_cookie, proxy, verbose)
                if detail: detailed_candidates.append(detail)
            if detailed_candidates:
                best_match = select_best_match(cn_name, detailed_candidates)
                if best_match:
                    jishi_result = best_match
                    if verbose: log.info(f"[流程] 集石匹配成功: {jishi_result.get('cn_name')} (BGG ID: {jishi_result.get('bgg_id')})")

    client = httpx.AsyncClient(timeout=20.0, proxy=proxy)
    try:
        if jishi_result and jishi_result.get("bgg_id"):
            if verbose: log.info(f"[流程] 集石 -> BGG API")
            details = await bgg_thing_details_api(jishi_result["bgg_id"], client, verbose, api_token, custom_logger)
            if details:
                details["cn_name"] = jishi_result.get("cn_name", details.get("name"))
                if jishi_result.get("cn_description"): details["cn_description"] = jishi_result.get("cn_description")
                details["jishi_score"] = jishi_result.get("jishi_score")
                details["language_requirement"] = jishi_result.get("language_requirement")
                details["table_requirement"] = jishi_result.get("table_requirement")
                details["jishi_categories"] = jishi_result.get("jishi_categories", [])
                details["_source"] = "集石→BGG_API"
                details["_name_source"] = "集石"
                details["_bgg_source"] = "BGG_API"
                return details
            else:
                return {
                    "name": jishi_result.get("en_name", ""), "cn_name": jishi_result.get("cn_name"),
                    "bgg_id": jishi_result.get("bgg_id"), "bgg_url": f"https://boardgamegeek.com/boardgame/{jishi_result.get('bgg_id')}",
                    "bgg_failed": True, "cn_description": jishi_result.get("cn_description"),
                    "jishi_score": jishi_result.get("jishi_score"), "language_requirement": jishi_result.get("language_requirement"),
                    "_source": "集石→BGG_API(详情失败)", "_name_source": "集石", "_bgg_source": "BGG_API(失败)",
                }

        if jishi_result and not jishi_result.get("bgg_id"):
            if verbose: log.info(f"[流程] 集石无BGG ID -> DDG兜底")
            query_for_ddg = jishi_result.get("cn_name", cn_name)
            ddg_candidates, _ = await fetch_english_candidates_from_ddg(f"{query_for_ddg} 桌游", proxy=proxy, verbose=verbose, llm_caller=llm_caller, logger=log)
            candidates.extend(ddg_candidates)
            if not candidates:
                return {"name": jishi_result.get("en_name", ""), "cn_name": jishi_result.get("cn_name"), "bgg_failed": True, "cn_description": jishi_result.get("cn_description"), "jishi_score": jishi_result.get("jishi_score"), "language_requirement": jishi_result.get("language_requirement"), "_source": "集石→DDG(无结果)", "_name_source": "集石", "_bgg_source": "无"}
            extracted_cn_name = query_for_ddg

        if not jishi_result and not from_alias:
            if verbose: log.info("[流程] 集石无结果 -> DDG兜底")
            ddg_candidates, extracted_cn_name = await fetch_english_candidates_from_ddg(f"{cn_name} 桌游", proxy=proxy, verbose=verbose, llm_caller=llm_caller, logger=log)
            candidates.extend(ddg_candidates)
            
        if not candidates:
            return None

        if verbose: log.info("开始尝试 BGG API 方式查询...")
        for q in candidates:
            try:
                game_id = await bgg_search_api_by_name(q, client, verbose, api_token, custom_logger)
                if game_id:
                    details = await bgg_thing_details_api(game_id, client, verbose, api_token, custom_logger)
                    if details:
                        details["_final_query"] = q
                        details["cn_name"] = extracted_cn_name
                        if jishi_result: src, ns = "集石→DDG→BGG_API", "集石(转DDG)"
                        elif from_alias: src, ns = "词典→BGG_API", "词典"
                        else: src, ns = "DDG+AI→BGG_API", "DDG+AI"
                        details["_source"] = src
                        details["_name_source"] = ns
                        details["_bgg_source"] = "BGG_API"
                        return details
            except Exception as e:
                if verbose: log.warning(f"[API 查询词 '{q}' 失败: {e}")
                continue

        if verbose: log.info("API 方式未获取到有效数据，回退到网页抓取方式...")
        search_result = None
        final_query = ""
        for q in candidates:
            result = await bgg_search_by_name(q, client, verbose)
            if result:
                search_result = result
                final_query = q
                break

        if not search_result:
            return {"name": candidates[0] if candidates else "", "cn_name": extracted_cn_name, "bgg_failed": True, "_source": f"词典→仅LLM" if from_alias else "DDG+AI→仅LLM", "_name_source": "词典" if from_alias else "DDG+AI", "_bgg_source": "仅LLM"}

        details = await web_thing_details(search_result["url"], client, search_name=search_result.get("name", ""), verbose=verbose)
        if details:
            details["_final_query"] = final_query
            details["cn_name"] = extracted_cn_name
            details["_source"] = f"词典→网页抓取" if from_alias else "DDG+AI→网页抓取"
            details["_name_source"] = "词典" if from_alias else "DDG+AI"
            details["_bgg_source"] = "网页抓取"
        else:
            return {"name": search_result.get("name", ""), "bgg_id": search_result.get("id", ""), "cn_name": extracted_cn_name, "bgg_url": search_result.get("url", ""), "bgg_failed": True, "_source": f"词典→网页搜索" if from_alias else "DDG+AI→网页搜索", "_name_source": "词典" if from_alias else "DDG+AI", "_bgg_source": "网页搜索"}
        
        return details
    finally:
        await client.aclose()
