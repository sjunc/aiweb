import re
import html
import requests
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

NEWS_CATEGORIES = ["정치", "경제", "사회", "세계", "문화", "과학", "IT"]


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


def fetch_article_body(url: str, max_len: int = 5000) -> str | None:
    """
    originallink에서 실제 기사 본문 HTML을 긁어와 텍스트만 추출
    
    여러 한국 뉴스 사이트의 공통 패턴을 시도하고,
    실패하면 None 반환 (프론트에서 description fallback).
    """
    import html as html_mod
    if not url:
        return None
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=8)
        resp.encoding = 'utf-8'
        raw = resp.text
    except requests.RequestException:
        return None

    # 공통 article 영역 패턴
    patterns = [
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]*id=["\']?articleBody["\']?[^>]*>(.*?)</div>',
        r'<div[^>]*id=["\']?newsEndContents["\']?[^>]*>(.*?)</div>',
        r'<div[^>]*class=["\'][^"\']*article-body[^"\']*["\'][^>]*>(.*?)</div>',
        r'<div[^>]*class=["\'][^"\']*article_txt[^"\']*["\'][^>]*>(.*?)</div>',
        r'<div[^>]*class=["\'][^"\']*_article_content[^"\']*["\'][^>]*>(.*?)</div>',
        r'<div[^>]*id=["\']?articleContent["\']?[^>]*>(.*?)</div>',
        r'<div[^>]*class=["\'][^"\']*content[^"\']*["\'][^>]*>(.*?)</div>',
        r'<div[^>]*itemprop=["\']articleBody["\'][^>]*>(.*?)</div>',
    ]
    for pat in patterns:
        m = re.search(pat, raw, re.DOTALL)
        if m:
            text = re.sub(r'<script[^>]*>.*?</script>', '', m.group(1), flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', '', text)
            text = html_mod.unescape(text)
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 100:
                return text[:max_len]
    return None


def fetch_og_image(url):
    if not url:
        return None
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/120.0.0.0"
        }, timeout=2.5)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        html_content = resp.text
        
        # Try finding og:image tag with property and content in any order
        m1 = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
        if m1:
            return m1.group(1).strip()
        
        m2 = re.search(r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', html_content, re.IGNORECASE)
        if m2:
            return m2.group(1).strip()
            
        m3 = re.search(r'<meta[^>]*name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
        if m3:
            return m3.group(1).strip()
            
        return None
    except Exception:
        return None


def fetch_trending_news(client_id, client_secret, count=16, categories=None):
    if not client_id or not client_secret:
        raise ValueError("네이버 API 인증 정보가 필요합니다.")

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret
    }

    cats = categories if categories else NEWS_CATEGORIES

    seen_links = set()
    ordered = []

    # 최대 3바퀴 순회
    for _round in range(3):
        if len(ordered) >= count + 1:
            break
        for category in cats:
            if len(ordered) >= count + 1:
                break
            try:
                resp = requests.get(
                    "https://openapi.naver.com/v1/search/news.json",
                    headers=headers,
                    params={"query": category, "display": 20, "sort": "sim"},
                    timeout=8
                )
                if resp.status_code != 200:
                    continue
                for item in resp.json().get("items", []):
                    link = item.get("originallink") or item.get("link")
                    if link and link not in seen_links:
                        seen_links.add(link)
                        stance, press = classify_media(link)
                        article = {
                            "title": clean_html(item.get("title")),
                            "description": clean_html(item.get("description")),
                            "link": link,
                            "press": press,
                            "stance": stance,
                            "stance_label": stance_label(stance),
                            "pubDate": item.get("pubDate", ""),
                            "category": category,
                        }
                        ordered.append(article)
            except requests.RequestException:
                continue

    # Slice items
    main = ordered[0] if ordered else None
    subs = ordered[1:1 + count] if len(ordered) > 1 else []

    # Fetch og:image in parallel using ThreadPoolExecutor
    import concurrent.futures
    all_selected = [main] + subs
    all_selected = [a for a in all_selected if a]

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

    return {"main": main, "subs": subs}
