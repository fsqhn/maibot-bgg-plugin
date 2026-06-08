from typing import Optional
import json
from pathlib import Path

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml, text/xml, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://boardgamegeek.com/",
    "Connection": "keep-alive",
}

# ===== BGG API2（官方推荐） =====
BGG_API_SEARCH_V2 = "https://boardgamegeek.com/xmlapi2/search"
BGG_API_THING = "https://boardgamegeek.com/xmlapi2/thing"
BGG_SEARCH_URL = "https://boardgamegeek.com/geeksearch.php"
BGG_BASE_URL = "https://boardgamegeek.com"

# ===== “Powered by BGG” logo（XML API 条款要求展示） =====
BGG_POWERED_BY_IMAGE_URL = (
    "https://cf.geekdo-images.com/HZy35cmzmmyV9BarSuk6ug__imagepage/img/"
    "FOGhR5OgYhcg-1jdqT5i5W8Xfbg=/fit-in/900x600/filters:no_upscale():strip_icc()/pic7779581.png"
)

# ==================== BGG API Token (硬编码) ====================
BGG_API_TOKEN = "a45425e8-aee4-42f2-9111-2190723fbb2b"

def make_bgg_api_headers(api_token: Optional[str] = None) -> dict:
    """
    为 BGG API 请求构造请求头。
    官方建议使用 xmlapi2 根路径，并带 Bearer token。
    """
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "application/xml, text/xml, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
    }
    token_to_use = api_token if api_token is not None else BGG_API_TOKEN
    if token_to_use:
        headers["Authorization"] = f"Bearer {token_to_use}"
    return headers


def _load_json(relative_path: str) -> dict:
    here = Path(__file__).parent
    p = here / relative_path
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_terms() -> dict:
    return _load_json("data/terms.json")


def load_alias() -> dict:
    return _load_json("data/alias.json")
