import logging
import httpx
from typing import List, Optional, Any

logger = logging.getLogger("bgg_search_plugin.tavily_client")

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


async def search_tavily(
    keyword: str,
    api_key: str,
    proxy: Optional[str] = None,
    max_results: int = 5,
    search_depth: str = "basic",
    include_domains: Optional[List[str]] = None,
    verbose: bool = False,
    custom_logger: Any = None,
) -> List[dict]:
    log = custom_logger or logger

    payload = {
        "api_key": api_key,
        "query": f"{keyword} 桌游 英文名 board game",
        "search_depth": search_depth,
        "include_answer": False,
        "include_raw_content": False,
        "max_results": max_results,
    }
    if include_domains:
        payload["include_domains"] = include_domains

    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=15.0) as client:
            if verbose:
                log.info(f"[Tavily] 搜索: {payload['query']}")
            resp = await client.post(TAVILY_SEARCH_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw_results = data.get("results", [])

            # 标准化为与 DDG 结果相同的格式
            results = []
            for r in raw_results:
                results.append({
                    "title": r.get("title", ""),
                    "body": r.get("content", ""),   # Tavily 用 content，映射到 body
                    "href": r.get("url", ""),
                })

            if verbose:
                log.info(f"[Tavily] 返回 {len(results)} 条结果")
                for i, r in enumerate(results[:3]):
                    log.info(f"  [{i+1}] {r['title']}: {r['body'][:100]}...")
            return results

    except Exception as e:
        log.error(f"[Tavily] 搜索异常: {e}")
        return []
