from typing import Optional, Any, List
import logging
import asyncio
import httpx
import xml.etree.ElementTree as ET

logger = logging.getLogger("bgg_search_plugin.bgg_api")

from .utils import (
    make_bgg_api_headers,
    BGG_API_SEARCH_V2,
    BGG_API_THING,
)
from .web_client import bgg_search_by_name, bgg_thing_details as web_thing_details

def _is_likely_primary(name: str, query: str) -> int:
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
    for suffix in (": second edition", " – second edition", ": 2nd edition", " – 2nd edition", ": 3rd edition", " – 3rd edition", " (second edition)", " (3rd edition)"):
        name_stripped = name_stripped.replace(suffix, "")
    if name_stripped == query_lower or name_stripped.startswith(query_lower):
        score += 70
    return score

def _collect_from_api2(body: str, query: str, log: Any) -> List[dict]:
    items: List[dict] = []
    try:
        root = ET.fromstring(body.encode("utf-8"))
        for it in root.findall(".//item[@type='boardgame']"):
            it_id = it.get("id", "")
            name_node = it.find("./name[@type='primary']")
            if name_node is None:
                name_node = it.find("./name")
            name = name_node.get("value") if name_node is not None else ""
            if it_id and name:
                items.append({"id": it_id, "name": name, "score": _is_likely_primary(name, query), "source": "V2"})
    except ET.ParseError as e:
        log.error(f"[BGG API V2] XML 解析失败: {e}")
    return items

async def bgg_search_api_candidates(
    query: str, client: httpx.AsyncClient, verbose: bool = False, api_token: Optional[str] = None, custom_logger: Any = None
) -> List[dict]:
    log = custom_logger or logger
    headers = make_bgg_api_headers(api_token)
    all_candidates: List[dict] = []
    try:
        if verbose: log.info(f"[BGG API V2] 搜索: {query}")
        max_retries = 3
        resp = None
        for attempt in range(max_retries):
            resp = await client.get(BGG_API_SEARCH_V2, params={"query": query, "type": "boardgame"}, headers=headers, timeout=15.0)
            if verbose: log.info(f"[BGG API V2] 状态码: {resp.status_code} (尝试 {attempt + 1}/{max_retries})")
            if resp.status_code == 202:
                if verbose: log.info("[BGG API V2] 收到 202，3 秒后重试...")
                await asyncio.sleep(3)
                continue
            break
        if resp is not None and resp.status_code == 200:
            all_candidates.extend(_collect_from_api2(resp.text, query, log))
        elif resp is not None:
            log.warning(f"[BGG API V2] 搜索状态码异常: {resp.status_code}")
    except Exception as e:
        log.error(f"[BGG API V2] 搜索异常: {type(e).__name__}: {e}")

    seen_ids = set()
    unique = []
    for c in all_candidates:
        if c["id"] not in seen_ids:
            seen_ids.add(c["id"])
            unique.append(c)
    unique.sort(key=lambda x: x["score"], reverse=True)
    if verbose: log.info(f"[BGG API 搜索] 共 {len(unique)} 个候选")
    return unique

async def bgg_thing_details_api(
    game_id: str, client: httpx.AsyncClient, verbose: bool = False, api_token: Optional[str] = None, custom_logger: Any = None
) -> Optional[dict]:
    log = custom_logger or logger
    headers = make_bgg_api_headers(api_token)
    max_retries = 3
    resp = None
    xml_text = ""
    for attempt in range(max_retries):
        try:
            if verbose: log.info(f"[BGG API 详情] 获取 ID: {game_id} (尝试 {attempt + 1}/{max_retries})")
            resp = await client.get(BGG_API_THING, params={"id": game_id, "stats": "1"}, headers=headers, timeout=20.0)
            if resp.status_code == 202:
                await asyncio.sleep(3)
                continue
            if resp.status_code != 200:
                return None
            xml_text = resp.text
            break
        except Exception as e:
            log.error(f"[BGG API 详情] 请求异常: {e}")
            if attempt == max_retries - 1: return None
            await asyncio.sleep(2)
    if resp is None: return None
    if not xml_text: return None

    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError as e:
        log.error(f"[BGG API 详情] XML 解析失败: {e}")
        return None

    item = root.find(".//item[@type='boardgame']")
    if item is None: return None

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
                try:
                    numvotes = int(best_result.get("numvotes", "0") or "0")
                except (ValueError, TypeError):
                    numvotes = 0
                if numvotes > best_votes:
                    best_votes = numvotes
                    best_numplayers = numplayers

    lang_dependence = ""
    lang_poll = item.find(".//poll[@name='language_dependence']")
    if lang_poll is not None:
        max_votes = -1
        best_result = None
        for result_node in lang_poll.findall(".//result"):
            try:
                numvotes = int(result_node.get("numvotes", "0"))
            except ValueError:
                numvotes = 0
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

    image_url = get_text(item, "image") or ""
    return {
        "bgg_id": game_id, "name": game_name, "year": get_attr(item, "yearpublished"),
        "description": get_text(item, "description"), "min_players": get_attr(item, "minplayers"),
        "max_players": get_attr(item, "maxplayers"), "min_time": get_attr(item, "minplaytime"),
        "max_time": get_attr(item, "maxplaytime"), "min_age": get_attr(item, "minage"),
        "users_rated": get_attr(item, "statistics/ratings/usersrated"),
        "average": get_attr(item, "statistics/ratings/average"),
        "avg_weight": get_attr(item, "statistics/ratings/averageweight"),
        "rank": str(overall_rank), "strategy_rank": str(strategy_rank), "image": image_url,
        "bgg_url": f"https://boardgamegeek.com/boardgame/{game_id}", "categories": categories,
        "mechanics": mechanics, "best_numplayers": best_numplayers, "language_dependence": lang_dependence,
    }

async def _try_bgg_full_search(
    query: str, client: httpx.AsyncClient, verbose: bool = False, api_token: Optional[str] = None, custom_logger: Any = None, max_candidates: int = 5
) -> Optional[tuple]:
    log = custom_logger or logger
    candidates = await bgg_search_api_candidates(query, client, verbose, api_token, custom_logger)
    for cand in candidates[:max_candidates]:
        game_id = cand["id"]
        details = await bgg_thing_details_api(game_id, client, verbose, api_token, custom_logger)
        if details:
            return details, "BGG_API2"
        if verbose: log.info(f"[BGG] API2详情失败(ID={game_id})，降级网页抓取")
        bgg_url = f"https://boardgamegeek.com/boardgame/{game_id}"
        details = await web_thing_details(bgg_url, client, verbose=verbose)
        if details:
            return details, "BGG网页抓取(API获ID)"
    if verbose: log.info(f"[BGG] API2搜索无有效结果，尝试网页搜索: {query}")
    search_result = await bgg_search_by_name(query, client, verbose)
    if search_result:
        details = await web_thing_details(search_result["url"], client, search_name=search_result.get("name", ""), verbose=verbose)
        if details:
            return details, "BGG网页搜索"
    return None
