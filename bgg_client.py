from typing import Optional
import httpx
import xml.etree.ElementTree as ET
from .utils import make_bgg_api_headers, BGG_API_SEARCH_V2, BGG_API_SEARCH_V1, BGG_API_THING
from src.common.logger import get_logger

logger = get_logger("bgg_search_plugin.bgg_client")
headers = make_bgg_api_headers()  

async def bgg_search_api_by_name(
    query: str,
    client: httpx.AsyncClient,
    verbose: bool = False,
    api_token: Optional[str] = None,
) -> Optional[str]:
    headers = make_bgg_api_headers(api_token)

    def is_likely_primary(name: str, query: str) -> int:
        score = 0
        name_lower = name.lower()
        query_lower = query.lower()
        if name_lower == query_lower:
            score += 100
        elif name_lower.startswith(query_lower):
            score += 80
        if any(x in name_lower for x in ("promo", "expansion", "exp.", " – ", ": ", " - ")):
            score -= 50
        name_stripped = name_lower
        for suffix in (
            ": second edition",
            " – second edition",
            ": 2nd edition",
            " – 2nd edition",
            ": 3rd edition",
            " – 3rd edition",
            " (second edition)",
            " (3rd edition)",
        ):
            name_stripped = name_stripped.replace(suffix, "")
        if name_stripped == query_lower or name_stripped.startswith(query_lower):
            score += 70
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
                    items_with_score.append(
                        {"id": it_id, "name": name, "score": score, "source": "V2"}
                    )
        except ET.ParseError as e:
            logger.error(f"[BGG API 搜索 V2] XML 解析失败: {e}")
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
                    items_with_score.append(
                        {"id": it_id, "name": name, "score": score, "source": "V1"}
                    )
        except ET.ParseError as e:
            logger.error(f"[BGG API 搜索 V1] XML 解析失败: {e}")
        return items_with_score

    url_v2 = BGG_API_SEARCH_V2
    params_v2 = {"query": query, "type": "boardgame"}

    try:
        if verbose:
            logger.info(f"[BGG API 搜索 V2] 正在搜索: {query}")
        import urllib.parse

        qs = urllib.parse.urlencode(params_v2)
        if verbose:
            print(f"[BGG API 搜索 V2] 请求 URL: {url_v2}?{qs}")

        resp = await client.get(url_v2, params=params_v2, headers=headers, timeout=15.0)
        if verbose:
            print(f"[BGG API 搜索 V2] HTTP 状态码: {resp.status_code}")
            logger.info(f"[BGG API 搜索 V2] HTTP 状态码: {resp.status_code}")

        body = resp.text
        if verbose:
            print(f"[BGG API 搜索 V2] 原始返回（前800字符）：\n{body[:800]}")
            logger.info(f"[BGG API 搜索 V2] 原始返回长度：{len(body)} 字符")

        if resp.status_code == 200:
            candidates = collect_from_api2(body, query)
            if not candidates:
                if verbose:
                    print(f"[BGG API 搜索 V2] 未找到 boardgame 类型的结果")
                    logger.info("[BGG API 搜索 V2] 未找到 boardgame 类型的结果")
            else:
                candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)

                if verbose:
                    print(f"[BGG API 搜索 V2] 候选（已按分数排序）：")
                    for idx, c in enumerate(candidates):
                        print(f"  候选{idx}: id={c['id']} name={c['name']} score={c['score']}")

                for cand in candidates:
                    game_id = cand["id"]
                    name = cand["name"]
                    if verbose:
                        print(f"[BGG API 搜索 V2] 尝试用候选: {name} (id={game_id})")
                        logger.info(f"[BGG API 搜索 V2] 尝试用候选: {name} (id={game_id})")
                    details = await bgg_thing_details_api(game_id, client, verbose, api_token)
                    if details:
                        if verbose:
                            print(f"[BGG API 搜索 V2] 候选 {name} (id={game_id}) 详情获取成功，返回该 ID")
                            logger.info(f"[BGG API 搜索 V2] 候选 {name} (id={game_id}) 详情获取成功，返回该 ID")
                        return game_id
                    else:
                        if verbose:
                            print(f"[BGG API 搜索 V2] 候选 {name} (id={game_id}) 详情失败，尝试下一个候选")
                            logger.warning(f"[BGG API 搜索 V2] 候选 {name} (id={game_id}) 详情失败，尝试下一个候选")
        else:
            if verbose:
                print(f"[BGG API 搜索 V2] 非预期状态码: {resp.status_code}")
                logger.info(f"[BGG API 搜索 V2] 非预期状态码: {resp.status_code}")

    except Exception as e:
        print(f"[BGG API 搜索 V2] 请求异常: {e}")
        logger.error(f"[BGG API 搜索 V2] 请求异常: {e}")

    url_v1 = BGG_API_SEARCH_V1
    params_v1 = {"search": query}

    try:
        if verbose:
            print(f"[BGG API 搜索 V1] 降级尝试搜索: {query}")
            import urllib.parse

        qs = urllib.parse.urlencode(params_v1)
        print(f"[BGG API 搜索 V1] 请求 URL: {url_v1}?{qs}")

        resp = await client.get(url_v1, params=params_v1, headers=headers, timeout=15.0)
        if verbose:
            print(f"[BGG API 搜索 V1] HTTP 状态码: {resp.status_code}")
            logger.info(f"[BGG API 搜索 V1] HTTP 状态码: {resp.status_code}")

        body = resp.text
        if verbose:
            print(f"[BGG API 搜索 V1] 原始返回（前800字符）：\n{body[:800]}")
            logger.info(f"[BGG API 搜索 V1] 原始返回长度：{len(body)} 字符")

        if resp.status_code == 200:
            candidates = collect_from_api1(body, query)
            if not candidates:
                if verbose:
                    print(f"[BGG API 搜索 V1] 未找到 boardgame 类型的结果")
                    logger.info("[BGG API 搜索 V1] 未找到 boardgame 类型的结果")
            else:
                candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)

                if verbose:
                    print(f"[BGG API 搜索 V1] 候选（已按分数排序）：")
                    for idx, c in enumerate(candidates):
                        print(f"  候选{idx}: id={c['id']} name={c['name']} score={c['score']}")

                for cand in candidates:
                    game_id = cand["id"]
                    name = cand["name"]
                    if verbose:
                        print(f"[BGG API 搜索 V1] 尝试用候选: {name} (id={game_id})")
                        logger.info(f"[BGG API 搜索 V1] 尝试用候选: {name} (id={game_id})")
                    details = await bgg_thing_details_api(game_id, client, verbose, api_token)
                    if details:
                        if verbose:
                            print(f"[BGG API 搜索 V1] 候选 {name} (id={game_id}) 详情获取成功，返回该 ID")
                            logger.info(f"[BGG API 搜索 V1] 候选 {name} (id={game_id}) 详情获取成功，返回该 ID")
                        return game_id
                    else:
                        if verbose:
                            print(f"[BGG API 搜索 V1] 候选 {name} (id={game_id}) 详情失败，尝试下一个候选")
                            logger.warning(f"[BGG API 搜索 V1] 候选 {name} (id={game_id}) 详情失败，尝试下一个候选")
        else:
            if verbose:
                print(f"[BGG API 搜索 V1] 非预期状态码: {resp.status_code}")
                logger.info(f"[BGG API 搜索 V1] 非预期状态码: {resp.status_code}")

    except Exception as e:
        print(f"[BGG API 搜索 V1] 请求异常: {e}")
        logger.error(f"[BGG API 搜索 V1] 请求异常: {e}")

    if verbose:
        print("[BGG API 搜索] API2 与 API1 均未返回有效结果")
        logger.info("[BGG API 搜索] API2 与 API1 均未返回有效结果")
    return None


async def bgg_thing_details_api(
    game_id: str,
    client: httpx.AsyncClient,
    verbose: bool = False,
    api_token: Optional[str] = None,
) -> Optional[dict]:
    try:
        params = {"id": game_id, "stats": "1"}
        headers = make_bgg_api_headers(api_token)
        if verbose:
            logger.info(f"[BGG API 详情] 正在获取 ID: {game_id} 的详情")
        resp = await client.get(BGG_API_THING, params=params, headers=headers, timeout=20.0)

        if resp.status_code != 200:
            logger.warning(f"[BGG API 详情] 状态码异常: {resp.status_code}")
            return None

        root = ET.fromstring(resp.text.encode("utf-8"))
        item = root.find(".//item[@type='boardgame']")
        if item is None:
            logger.warning("[BGG API 详情] 未找到 boardgame item 节点")
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

        categories = []
        for cat_node in item.findall(".//link[@type='boardgamecategory']"):
            cat_value = cat_node.get("value", "")
            if cat_value:
                categories.append(cat_value)

        mechanics = []
        for mech_node in item.findall(".//link[@type='boardgamemechanic']"):
            mech_value = mech_node.get("value", "")
            if mech_value:
                mechanics.append(mech_value)

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
            lang_result = lang_poll.find(".//results/result")
            if lang_result is not None:
                lang_value = lang_result.get("value", "")
                lang_level = lang_result.get("level", "")
                if lang_value:
                    level_map = {
                        "1": "无需阅读",
                        "2": "轻微依赖",
                        "3": "中度依赖",
                        "4": "高度依赖",
                        "5": "极度依赖",
                    }
                    level_text = level_map.get(lang_level, "")
                    lang_dependence = f"{lang_value}（{level_text}）" if level_text else lang_value
                else:
                    lang_dependence = lang_value

        return {
            "bgg_id": game_id,
            "name": game_name,
            "year": get_attr(item, "yearpublished"),
            "description": get_text(item, "description"),
            "min_players": get_attr(item, "minplayers"),
            "max_players": get_attr(item, "maxplayers"),
            "min_time": get_attr(item, "minplaytime"),
            "max_time": get_attr(item, "maxplaytime"),
            "min_age": get_attr(item, "minage"),
            "users_rated": get_attr(item, "statistics/ratings/usersrated"),
            "average": get_attr(item, "statistics/ratings/average"),
            "avg_weight": get_attr(item, "statistics/ratings/averageweight"),
            "rank": str(overall_rank),
            "strategy_rank": str(strategy_rank),
            "image": get_text(item, "image"),
            "bgg_url": f"https://boardgamegeek.com/boardgame/{game_id}",
            "categories": categories,
            "mechanics": mechanics,
            "best_numplayers": best_numplayers,
            "language_dependence": lang_dependence,
        }

    except Exception as e:
        logger.error(f"[BGG API 详情异常] {e}")
        return None

from .ddg_client import fetch_english_candidates_from_ddg
from .web_client import bgg_search_by_name, bgg_thing_details as web_thing_details
from .utils import load_alias
import httpx


async def resolve_boardgame_by_cn_name(
    cn_name: str,
    proxy: Optional[str] = None,
    verbose: bool = False,
    api_token: Optional[str] = None,
) -> Optional[dict]:
    alias_dict = load_alias()
    raw = alias_dict.get(cn_name, "").strip()
    candidates = []
    from_alias = False  # 新增：标记是否来自词典
    
    if raw:
        candidates = [c.strip() for c in raw.split("|") if c.strip()]
        from_alias = True  # 标记来自词典
        if verbose:
            logger.info(f"[数据来源] 从本地词典找到英文名候选: {candidates}")

    extracted_cn_name = cn_name
    if not candidates:
        ddg_candidates, extracted_cn_name = await fetch_english_candidates_from_ddg(
            f"{cn_name} 桌游",
            proxy=proxy,
            verbose=verbose,
        )
        candidates.extend(ddg_candidates)
        if verbose:
            logger.info(f"[数据来源] 从 DDG+AI 提取英文名候选: {candidates}")

    if not candidates:
        return None

    client = httpx.AsyncClient(timeout=20.0, proxy=proxy)

    try:
        if verbose:
            logger.info("开始尝试 BGG API 方式查询...")
        for q in candidates:
            try:
                game_id = await bgg_search_api_by_name(q, client, verbose, api_token)
                if game_id:
                    details = await bgg_thing_details_api(game_id, client, verbose, api_token)
                    if details:
                        details["_final_query"] = q
                        details["cn_name"] = extracted_cn_name
                        # 新增：记录完整的数据来源
                        details["_source"] = f"词典→BGG_API" if from_alias else "DDG+AI→BGG_API"
                        details["_name_source"] = "词典" if from_alias else "DDG+AI"
                        details["_bgg_source"] = "BGG_API"
                        if verbose:
                            logger.info(f"[API 成功] 使用查询词 '{q}' 获取到数据")
                        return details
            except Exception as e:
                if verbose:
                    logger.warning(f"[API 查询词 '{q}' 失败: {e}，尝试下一个或回退")
                continue

        if verbose:
            logger.info("API 方式未获取到有效数据，回退到网页抓取方式...")
        search_result = None
        final_query = ""

        for q in candidates:
            result = await bgg_search_by_name(q, client, verbose)
            if result:
                search_result = result
                final_query = q
                break

        if not search_result:
            if verbose:
                logger.info(f"[BGG 查询失败] 返回 LLM 提取的信息")
            return {
                "name": candidates[0] if candidates else "",
                "cn_name": extracted_cn_name,
                "bgg_failed": True,
                "_source": f"词典→仅LLM" if from_alias else "DDG+AI→仅LLM",
                "_name_source": "词典" if from_alias else "DDG+AI",
                "_bgg_source": "仅LLM",
            }

        details = await web_thing_details(
            search_result["url"],
            client,
            search_name=search_result.get("name", ""),
            verbose=verbose,
        )

        if details:
            details["_final_query"] = final_query
            details["cn_name"] = extracted_cn_name
            # 新增：记录完整的数据来源
            details["_source"] = f"词典→网页抓取" if from_alias else "DDG+AI→网页抓取"
            details["_name_source"] = "词典" if from_alias else "DDG+AI"
            details["_bgg_source"] = "网页抓取"
        else:
            return {
                "name": search_result.get("name", ""),
                "bgg_id": search_result.get("id", ""),
                "cn_name": extracted_cn_name,
                "bgg_url": search_result.get("url", ""),
                "bgg_failed": True,
                "_source": f"词典→网页搜索" if from_alias else "DDG+AI→网页搜索",
                "_name_source": "词典" if from_alias else "DDG+AI",
                "_bgg_source": "网页搜索",
            }

        return details

    finally:
        await client.aclose()