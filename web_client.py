from typing import Optional
import httpx
import json
import html
from bs4 import BeautifulSoup
from .utils import HEADERS, BGG_SEARCH_URL, BGG_BASE_URL
from src.common.logger import get_logger

logger = get_logger("bgg_search_plugin.web_client")


async def bgg_search_by_name(
    query: str,
    client: httpx.AsyncClient,
    verbose: bool = False,
) -> Optional[dict]:
    try:
        params = {"action": "search", "q": query, "objecttype": "boardgame"}
        if verbose:
            logger.info(f"[BGG 网页搜索] 正在搜索: {query}")

        resp = await client.get(BGG_SEARCH_URL, params=params, headers=HEADERS, timeout=15.0)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        game_link = None
        bgg_id = ""
        game_name = ""

        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href", "")
            if (
                "/boardgame/" in href
                and "/boardgamegame" not in href
                and "/boardgameexpansion" not in href
            ):
                parts = href.split("/")
                if len(parts) >= 3:
                    try:
                        potential_id = parts[2]
                        if potential_id.isdigit():
                            bgg_id = potential_id
                            game_link = href
                            game_name = a_tag.text.strip()
                            if not game_name and len(parts) > 3:
                                game_name = parts[3].replace("-", " ").title()
                            break
                    except (ValueError, IndexError):
                        continue

        if not game_link:
            return None

        if game_link.startswith("/"):
            game_link = BGG_BASE_URL + game_link

        if verbose:
            logger.info(f"[BGG 网页搜索] 找到游戏: ID={bgg_id}, 名称={game_name}")
        return {"id": bgg_id, "name": game_name, "url": game_link}

    except Exception as e:
        logger.error(f"[BGG 网页搜索异常] {e}")
        return None


async def bgg_thing_details(
    game_url: str,
    client: httpx.AsyncClient,
    search_name: str = "",
    verbose: bool = False,
) -> Optional[dict]:
    try:
        if verbose:
            logger.info(f"[BGG 详情] 访问: {game_url}")
        resp = await client.get(game_url, headers=HEADERS, timeout=20.0)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        scripts = soup.find_all("script")
        script_data = None

        for script in scripts:
            script_text = script.string or ""
            if "GEEK.geekitemPreload" in script_text and "GEEK.geekitemSettings" in script_text:
                start = script_text.find("GEEK.geekitemPreload = ") + len("GEEK.geekitemPreload = ")
                end = script_text.find("GEEK.geekitemSettings = ") - 3
                if start > 0 and end > start:
                    try:
                        script_data = json.loads(script_text[start:end])
                        break
                    except json.JSONDecodeError:
                        continue

        if not script_data:
            return None

        item = script_data.get("item", {})
        stats = item.get("stats", {})
        rank_info = item.get("rankinfo", [])
        overall_rank = rank_info[0].get("rank") if rank_info else "N/A"
        strategy_rank = rank_info[1].get("rank") if len(rank_info) > 1 else "N/A"

        ld_json = soup.find("script", type="application/ld+json")
        game_name_en = ""
        game_image = ""
        description = ""
        if ld_json:
            try:
                ld_data = json.loads(ld_json.string)
                game_name_en = ld_data.get("name", "")
                game_image = ld_data.get("image", "")
                description = html.unescape(ld_data.get("description", "") or "")
            except json.JSONDecodeError:
                pass

        if not game_name_en and search_name:
            game_name_en = search_name

        avgweight = stats.get("avgweight", "0")
        if isinstance(avgweight, str) and avgweight:
            try:
                avgweight_val = float(avgweight)
                avgweight_str = f"{avgweight_val:.2f}"
            except ValueError:
                avgweight_str = "N/A"
        else:
            avgweight_str = "N/A"

        categories = []
        mechanics = []
        item_links = item.get("links", [])
        for link in item_links:
            if isinstance(link, dict):
                link_type = link.get("type", "")
                link_name = link.get("name", "")
                if link_type == "boardgamecategory":
                    categories.append(link_name)
                elif link_type == "boardgamemechanic":
                    mechanics.append(link_name)

        return {
            "bgg_id": str(item.get("objectid", "")),
            "name": game_name_en,
            "year": str(item.get("yearpublished", "")),
            "description": description,
            "min_players": str(item.get("minplayers", "?")),
            "max_players": str(item.get("maxplayers", "?")),
            "min_time": str(item.get("minplaytime", "?")),
            "max_time": str(item.get("maxplaytime", "?")),
            "min_age": str(item.get("minage", "?")),
            "users_rated": "0",
            "average": "0",
            "avg_weight": avgweight_str,
            "rank": str(overall_rank),
            "strategy_rank": str(strategy_rank),
            "image": game_image,
            "bgg_url": game_url,
            "categories": categories,
            "mechanics": mechanics,
            "best_numplayers": "",
            "language_dependence": "",
        }

    except Exception as e:
        logger.error(f"[BGG 详情异常] {e}")
        return None
