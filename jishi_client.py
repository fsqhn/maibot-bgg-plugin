import httpx
import re
import logging
import urllib.parse
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

# 修复沙箱报错：不再使用 from src.common.logger import get_logger
logger = logging.getLogger("bgg_search_plugin.jishi_client")

JISHI_BASE_URL = "https://www.gstonegames.com"

# 模拟完整的 Edge 浏览器请求头
COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
}

def calculate_similarity(a: str, b: str) -> float:
    """简单的文本相似度计算"""
    a_lower = a.lower().strip()
    b_lower = b.lower().strip()
    if a_lower == b_lower: return 1.0
    if a_lower in b_lower or b_lower in a_lower: return 0.8
    return 0.0

async def search_jishi_games(keyword: str, cookie: str, proxy: Optional[str], verbose: bool = False) -> List[Dict]:
    """搜索集石，按热度排序，提取前3个结果的基础信息"""
    encoded_keyword = urllib.parse.quote(keyword)
    url = f"{JISHI_BASE_URL}/game/?hot_sort=1&keyword={encoded_keyword}"
    headers = {**COMMON_HEADERS, "Referer": JISHI_BASE_URL, "Cookie": cookie}
    async with httpx.AsyncClient(proxy=proxy, timeout=15.0, follow_redirects=True) as client:
        try:
            if verbose: logger.info(f"[Jishi Search] 请求 URL: {url}")
            logger.debug(f"[Jishi Search] Headers: User-Agent={headers['User-Agent'][:30]}... Cookie存在={bool(cookie)}")
            resp = await client.get(url, headers=headers)
            if resp.status_code == 403:
                logger.error(f"[Jishi Search] 403 Forbidden。可能原因：Cookie无效或请求头被拦截。")
                if verbose: logger.warning("提示：请检查 config.toml 中的 jishi.cookie 是否过期，或抓包更新 Cookie。")
                return []
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            goods_div = soup.find("div", class_="goods")
            if not goods_div:
                if verbose: logger.info("[Jishi Search] 未找到结果容器 (class='goods')")
                return []
            items = goods_div.find_all("div", class_="goods-list", limit=3)
            results = []
            for item in items:
                title_div = item.find("div", class_="goods-title")
                if not title_div: continue
                link = title_div.find("a")
                if not link: continue
                href = link.get("href", "")
                match = re.search(r"/game/info-(\d+)\.html", href)
                if not match: continue
                jishi_id = match.group(1)
                title = link.get("title", "").strip()
                score_div = item.find("div", class_="goods04")
                list_score = score_div.get_text(strip=True) if score_div else ""
                results.append({
                    "jishi_id": jishi_id,
                    "cn_name": title,
                    "url": f"{JISHI_BASE_URL}/game/info-{jishi_id}.html",
                    "jishi_list_score": list_score
                })
            if verbose: logger.info(f"[Jishi Search] 找到 {len(results)} 个候选")
            return results
        except Exception as e:
            logger.error(f"[Jishi Search] 请求异常: {e}")
            return []

async def fetch_jishi_detail(jishi_id: str, cookie: str, proxy: Optional[str], verbose: bool = False) -> Optional[Dict]:
    """请求集石详情页，提取 BGG ID、简介、语言要求等"""
    url = f"{JISHI_BASE_URL}/game/info-{jishi_id}.html"
    headers = {**COMMON_HEADERS, "Referer": JISHI_BASE_URL, "Cookie": cookie}
    async with httpx.AsyncClient(proxy=proxy, timeout=15.0, follow_redirects=True) as client:
        try:
            if verbose: logger.info(f"[Jishi Detail] 抓取详情: {url}")
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            
            cn_name = ""
            title_h2 = soup.find("h2")
            if title_h2: cn_name = title_h2.get_text(strip=True)
            
            bgg_id = None
            bgg_link = soup.find("a", href=re.compile(r"boardgamegeek\.com/boardgame/(\d+)"))
            if bgg_link:
                match = re.search(r"/boardgame/(\d+)", bgg_link.get("href", ""))
                if match: bgg_id = match.group(1)
                
            en_name = ""
            en_desc_p = soup.find("p", attrs={"v-if": re.compile(r"\(curtLang=='eng'\)")})
            if en_desc_p:
                text = en_desc_p.get_text()
                m = re.search(r"([A-Z][A-Za-z0-9:& ]+?) is a", text)
                if m: en_name = m.group(1).strip()
                
            cn_desc = ""
            cn_desc_p = soup.find("p", attrs={"v-if": re.compile(r"\(curtLang=='sch'\)")})
            if cn_desc_p:
                raw_text = cn_desc_p.get_text(strip=True)
                if ("暂" in raw_text and "简介" in raw_text) or len(raw_text) < 15:
                    if verbose: logger.info(f"[Jishi Detail] 检测到无效中文简介: '{raw_text}'，已忽略，将使用BGG简介。")
                    cn_desc = ""
                else:
                    cn_desc = raw_text
                    
            jishi_score = ""
            score_div = soup.find("div", class_="score")
            if score_div:
                p = score_div.find("p")
                if p: jishi_score = p.get_text(strip=True)
                
            lang_req = ""
            for p in soup.select("div.introduce p"):
                text = p.get_text(strip=True)
                if "语言要求" in text:
                    lang_req = text.replace("语言要求：", "").strip()
                    break
                    
            table_req = ""
            for p in soup.select("div.introduce p"):
                text = p.get_text(strip=True)
                if "桌面要求" in text:
                    table_req = text.replace("桌面要求：", "").strip()
                    break
                    
            jishi_categories = []
            detail_title_p = soup.select_one("div.details-title p")
            if detail_title_p:
                text = detail_title_p.get_text(strip=True)
                parts = text.split("/")
                for part in parts:
                    p = part.strip()
                    if p in ["德式", "美式", "聚会", "抽象", "战棋", "卡牌", "扮演", "亲子", "解谜"]:
                        jishi_categories.append(p)
                        
            return {
                "jishi_id": jishi_id, "cn_name": cn_name, "bgg_id": bgg_id, "en_name": en_name,
                "cn_description": cn_desc, "jishi_score": jishi_score,
                "language_requirement": lang_req, "table_requirement": table_req,
                "jishi_categories": jishi_categories
            }
        except Exception as e:
            logger.error(f"[Jishi Detail] 抓取异常 {jishi_id}: {e}")
            return None

def select_best_match(query: str, candidates: List[Dict]) -> Optional[Dict]:
    """智能选择最佳匹配结果"""
    best_match = None
    best_score = -1
    query_lower = query.lower()
    is_searching_expansion = any(x in query_lower for x in ['扩展', 'promo', '版', '扩'])
    for cand in candidates:
        if not cand: continue
        name = cand.get("cn_name", "")
        score = 0
        if name.lower() == query_lower: score += 100
        elif name.lower().startswith(query_lower): score += 80
        elif query_lower in name.lower(): score += 50
        if not is_searching_expansion:
            if ':' in name or '扩展' in name: score -= 30
        current_bg_id = int(cand.get("bgg_id")) if cand.get("bgg_id") else 999999
        if best_match:
            best_bg_id = int(best_match.get("bgg_id")) if best_match.get("bgg_id") else 999999
            if score > best_score:
                best_match = cand
                best_score = score
            elif score == best_score:
                if current_bg_id < best_bg_id: best_match = cand
        else:
            best_match = cand
            best_score = score
    return best_match
