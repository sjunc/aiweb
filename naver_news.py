import re
import html
import time
import threading
import concurrent.futures
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_http_session = requests.Session()
_retries = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
_http_session.mount('http://', HTTPAdapter(max_retries=_retries, pool_connections=20, pool_maxsize=20))
_http_session.mount('https://', HTTPAdapter(max_retries=_retries, pool_connections=20, pool_maxsize=20))
from urllib.parse import urlparse

STANCE_LABELS = {
    'far_left': '극좌',
    'left': '좌',
    'center': '중도',
    'right': '우',
    'far_right': '극우',
    'unknown': '판별불가',
}

MEDIA_DB = {
    'far_left': {
        'pressian.com': '프레시안', 'mediatoday.co.kr': '미디어오늘',
    },
    'left': {
        'imbc.co.kr': 'MBC', 'hani.co.kr': '한겨레', 'khan.co.kr': '경향신문',
        'jtbc.co.kr': 'JTBC', 'ohmynews.com': '오마이뉴스',
    },
    'center': {
        'yna.co.kr': '연합뉴스', 'news1.kr': '뉴스1', 'newsis.com': '뉴시스',
        'hankookilbo.com': '한국일보', 'kmib.co.kr': '국민일보', 'sbs.co.kr': 'SBS',
    },
    'right': {
        'joongang.co.kr': '중앙일보', 'donga.com': '동아일보',
        'mk.co.kr': '매일경제', 'hankyung.com': '한국경제', 'sedaily.com': '서울경제',
        'kbs.co.kr': 'KBS', 'ytn.co.kr': 'YTN', 'mbn.co.kr': 'MBN',
        'segye.com': '세계일보', 'seoul.co.kr': '서울신문',
    },
    'far_right': {
        'chosun.com': '조선일보', 'tvchosun.com': 'TV조선',
        'ichannela.com': '채널A', 'munhwa.com': '문화일보',
        'dailian.co.kr': '데일리안', 'newdaily.co.kr': '뉴데일리',
    },
}

NEWS_CATEGORIES = ["경제", "사회", "문화", "과학", "IT"]


def clean_html(text):
    if not text:
        return ""
    return html.unescape(re.sub('<.*?>', '', text))


def classify_media(url):
    if not url:
        return 'unknown', '기타'
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        return 'unknown', '기타'
    for stance, mapping in MEDIA_DB.items():
        for key, name in mapping.items():
            if key in domain:
                return stance, name
    return 'unknown', '기타'


def stance_label(stance):
    return STANCE_LABELS.get(stance, '판별불가')


_body_cache = {}
_body_cache_lock = threading.Lock()

def _extract_body_bs4(html_content: str, max_len: int = 5000) -> str | None:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Remove noisy tags
        for noise in soup(["script", "style", "header", "footer", "nav", "aside", "form", "iframe", "noscript"]):
            noise.decompose()
            
        # Remove noisy classes
        for noise in soup.select(".header, .footer, .nav, .menu, .sidebar, .lang-selector, #header, #footer, .util_box, .sns_share"):
            noise.decompose()

        # Common article body selectors (more specific ones first, removed too broad ones like "article" or ".content")
        selectors = [
            "#dic_area", "#articleBodyContents", "#articleBody", 
            "#newsEndContents", "#articleContent", 
            ".article_view", ".article-body", ".article_txt", "._article_content",
            "[itemprop='articleBody']", ".news_body", ".news_content", ".view_con"
        ]
        
        for sel in selectors:
            elem = soup.select_one(sel)
            if elem:
                text = elem.get_text(separator=' ', strip=True)
                if len(text) > 100:
                    return text[:max_len]
                    
        # Fallback: combine all large <p> tags
        p_texts = []
        for p in soup.select('p'):
            t = p.get_text(separator=' ', strip=True)
            if len(t) > 30: p_texts.append(t)
        if p_texts:
            text = " ".join(p_texts)
            if len(text) > 100: return text[:max_len]
    except ImportError:
        pass
    
    return None

def fetch_article_body(url: str, max_len: int = 5000) -> str | None:
    if not url:
        return None
        
    with _body_cache_lock:
        if url in _body_cache:
            return _body_cache[url]

    try:
        resp = _http_session.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=4)
        html_content = resp.content.decode(resp.encoding or 'utf-8', errors='ignore')
        body_text = _extract_body_bs4(html_content, max_len)
        if body_text:
            with _body_cache_lock:
                _body_cache[url] = body_text
                # Prevent memory leak by keeping cache size bounded
                if len(_body_cache) > 1000:
                    keys_to_delete = list(_body_cache.keys())[:500]
                    for k in keys_to_delete:
                        del _body_cache[k]
            return body_text
    except requests.RequestException:
        pass
    return None


def fetch_og_image(url):
    if not url:
        return None
    try:
        resp = _http_session.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=4.5)
        # apparent_encoding is extremely slow (uses chardet), just use headers encoding or utf-8
        html_content = resp.content.decode(resp.encoding or 'utf-8', errors='ignore')
        
        # Cache the body text while we have the HTML!
        body_text = _extract_body_bs4(html_content)
        if body_text:
            with _body_cache_lock:
                _body_cache[url] = body_text
                if len(_body_cache) > 1000:
                    keys_to_delete = list(_body_cache.keys())[:500]
                    for k in keys_to_delete:
                        del _body_cache[k]
        
        # Try finding og:image tag
        m1 = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
        if m1: return html.unescape(m1.group(1))
        
        m2 = re.search(r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', html_content, re.IGNORECASE)
        if m2: return html.unescape(m2.group(1))
        
        # Fallback: Parse the HTML using BS4 and grab the first large image
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            for img in soup.select('img'):
                src = img.get('src') or img.get('data-src')
                if src and src.startswith('http'):
                    # Skip tracking or small icons
                    if 'icon' in src or 'logo' in src or 'blank' in src: continue
                    return src
        except ImportError:
            pass
            
        m3 = re.search(r'<meta[^>]*name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
        if m3:
            return m3.group(1).strip()
            
        return None
    except Exception:
        return None


def fetch_trending_news(client_id, client_secret, count=16, categories=None):
    if not client_id or not client_secret:
        raise ValueError("네이버 API 인증 정보가 필요합니다.")

    cats = categories if categories else NEWS_CATEGORIES
    cache_key = ",".join(sorted(cats))

    # 60초 TTL 캐시 — 같은 카테고리 조합의 반복 요청을 즉시 반환
    with _trending_cache_lock:
        if cache_key in _trending_cache:
            cached_time, cached_data = _trending_cache[cache_key]
            if time.time() - cached_time < 60:
                return cached_data

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret
    }

    # 카테고리가 1개이면 다 가져오고, 여러 개이면 카테고리당 개수를 제한해 골고루 섞음
    per_cat_limit = 20 if len(cats) == 1 else 3

    def fetch_category(category):
        """단일 카테고리에 대한 네이버 API 호출 (병렬 실행용)"""
        results = []
        try:
            resp = _http_session.get(
                "https://openapi.naver.com/v1/search/news.json",
                headers=headers,
                params={"query": category, "display": 50, "sort": "sim"},
                timeout=5
            )
            if resp.status_code != 200:
                return results

            from email.utils import parsedate_to_datetime
            from datetime import datetime, timezone, timedelta
            
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=24)

            for item in resp.json().get("items", []):
                if len(results) >= per_cat_limit:
                    break
                
                # 24시간 이내 주요 뉴스 필터링
                pub_date_str = item.get("pubDate", "")
                if pub_date_str:
                    try:
                        dt = parsedate_to_datetime(pub_date_str)
                        if dt < cutoff:
                            continue
                    except Exception:
                        pass
                
                # Prefer Naver News link for reliable scraping, fallback to originallink
                link_naver = item.get("link")
                link_orig = item.get("originallink")
                link = link_naver if link_naver and "n.news.naver.com" in link_naver else link_orig or link_naver
                
                stance, press = classify_media(link)
                article = {
                    "title": clean_html(item.get("title")),
                    "description": clean_html(item.get("description")),
                    "link": link,
                    "press": press,
                    "stance": stance,
                    "stance_label": stance_label(stance),
                    "pubDate": pub_date_str,
                    "category": category,
                }
                results.append(article)
        except requests.RequestException:
            pass
        return results

    # 모든 카테고리를 동시에 호출 (핵심 속도 향상)
    all_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(cats), 10)) as executor:
        futures = {executor.submit(fetch_category, cat): cat for cat in cats}
        for future in concurrent.futures.as_completed(futures):
            all_results.extend(future.result())

    # 중복 링크 및 제목 제거
    seen_links = set()
    selected_tokens_list = []
    ordered = []
    
    for art in all_results:
        if art["link"] in seen_links: continue
        
        # Tokenize title to catch semantic duplicates (words with 2+ characters)
        tokens = set(re.findall(r'[가-힣a-zA-Z0-9]{2,}', art["title"]))
        
        is_dup = False
        for ext_tokens in selected_tokens_list:
            intersection = tokens.intersection(ext_tokens)
            # If 3 or more meaningful words overlap, or similarity > 40%
            if len(intersection) >= 3 or (len(tokens) > 0 and len(intersection) / len(tokens) > 0.4):
                is_dup = True
                break
                
        if not is_dup:
            seen_links.add(art["link"])
            selected_tokens_list.append(tokens)
            ordered.append(art)
            
        if len(ordered) >= count + 1:
            break

    # Slice items
    main = ordered[0] if ordered else None
    subs = ordered[1:1 + count] if len(ordered) > 1 else []

    # Fetch og:image in parallel using ThreadPoolExecutor
    mini_cards = subs[6:14] if len(subs) > 6 else []
    all_selected = [a for a in ([main] + mini_cards) if a]

    if all_selected:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_art = {executor.submit(fetch_og_image, art.get("link")): art for art in all_selected}
            for future in concurrent.futures.as_completed(future_to_art):
                art = future_to_art[future]
                try:
                    img_url = future.result()
                    art["image_url"] = img_url
                except Exception:
                    art["image_url"] = None

    result = {"main": main, "subs": subs}
    with _trending_cache_lock:
        _trending_cache[cache_key] = (time.time(), result)
    return result


# 서버 시간 기반 캐시 저장소 (스레드 안전)
_trending_cache: dict = {}
_trending_cache_lock = threading.Lock()

