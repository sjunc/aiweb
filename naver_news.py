"""
naver_news.py — 네이버 뉴스 API 기반 실시간 뉴스 수집 및 크롤러

리팩토링 포인트:
- BeautifulSoup, email.utils 등 반복 import를 파일 최상단으로 이동
- _cache_put() 헬퍼로 중복된 캐시 eviction 코드 DRY 처리
- User-Agent를 session 기본 헤더로 한 번만 설정
- fetch_category를 모듈 레벨 함수로 분리하여 테스트 가능성 확보
- link=None 엣지케이스 방어 (중복 필터 버그 수정)
- requests.RequestException 캐치 시 로그 추가
"""
import html
import logging
import re
import threading
import time
import concurrent.futures
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── HTTP 세션 (커넥션 풀링 + 자동 재시도) ──────────────────────────
_http_session = requests.Session()
_retries = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
_http_session.mount("http://", HTTPAdapter(max_retries=_retries, pool_connections=20, pool_maxsize=20))
_http_session.mount("https://", HTTPAdapter(max_retries=_retries, pool_connections=20, pool_maxsize=20))
_http_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})

# ── 언론사 성향 DB ──────────────────────────────────────────────────
STANCE_LABELS: dict[str, str] = {
    "far_left": "극좌",
    "left": "좌",
    "center": "중도",
    "right": "우",
    "far_right": "극우",
    "unknown": "판별불가",
}

MEDIA_DB: dict[str, dict[str, str]] = {
    "far_left": {
        "pressian.com": "프레시안",
        "mediatoday.co.kr": "미디어오늘",
    },
    "left": {
        "imbc.co.kr": "MBC",
        "hani.co.kr": "한겨레",
        "khan.co.kr": "경향신문",
        "jtbc.co.kr": "JTBC",
        "ohmynews.com": "오마이뉴스",
    },
    "center": {
        "yna.co.kr": "연합뉴스",
        "news1.kr": "뉴스1",
        "newsis.com": "뉴시스",
        "hankookilbo.com": "한국일보",
        "kmib.co.kr": "국민일보",
        "sbs.co.kr": "SBS",
    },
    "right": {
        "joongang.co.kr": "중앙일보",
        "donga.com": "동아일보",
        "mk.co.kr": "매일경제",
        "hankyung.com": "한국경제",
        "sedaily.com": "서울경제",
        "kbs.co.kr": "KBS",
        "ytn.co.kr": "YTN",
        "mbn.co.kr": "MBN",
        "segye.com": "세계일보",
        "seoul.co.kr": "서울신문",
    },
    "far_right": {
        "chosun.com": "조선일보",
        "tvchosun.com": "TV조선",
        "ichannela.com": "채널A",
        "munhwa.com": "문화일보",
        "dailian.co.kr": "데일리안",
        "newdaily.co.kr": "뉴데일리",
    },
}

NEWS_CATEGORIES: list[str] = ["경제", "사회", "문화", "과학", "IT"]

# ── 캐시 (스레드 안전) ─────────────────────────────────────────────
_body_cache: dict[str, str] = {}
_body_cache_lock = threading.Lock()
_BODY_CACHE_MAX = 1000
_BODY_CACHE_EVICT = 500

_trending_cache: dict[str, tuple[float, dict]] = {}
_trending_cache_lock = threading.Lock()
_TRENDING_TTL = 60  # seconds


# ── 유틸리티 ───────────────────────────────────────────────────────

def clean_html(text: str) -> str:
    """HTML 태그 및 이스케이프 제거"""
    if not text:
        return ""
    return html.unescape(re.sub(r"<.*?>", "", text))


def classify_media(url: str | None) -> tuple[str, str]:
    """URL에서 언론사 성향과 이름을 분류. 알 수 없으면 ('unknown', '기타') 반환."""
    if not url:
        return "unknown", "기타"
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        return "unknown", "기타"
    for stance, mapping in MEDIA_DB.items():
        for key, name in mapping.items():
            if key in domain:
                return stance, name
    return "unknown", "기타"


def stance_label(stance: str) -> str:
    return STANCE_LABELS.get(stance, "판별불가")


def _cache_put(url: str, text: str) -> None:
    """본문 캐시 저장 + 최대 크기 초과 시 오래된 항목 제거 (스레드 안전)"""
    with _body_cache_lock:
        _body_cache[url] = text
        if len(_body_cache) > _BODY_CACHE_MAX:
            evict_keys = list(_body_cache.keys())[:_BODY_CACHE_EVICT]
            for k in evict_keys:
                del _body_cache[k]
            logger.debug("본문 캐시 eviction: %d개 항목 제거", _BODY_CACHE_EVICT)


# ── 기사 본문 추출 ─────────────────────────────────────────────────

def _extract_body_bs4(html_content: str, max_len: int = 5000) -> str | None:
    """BeautifulSoup으로 기사 본문 추출. 실패 시 None 반환."""
    if not BS4_AVAILABLE:
        return None
    try:
        soup = BeautifulSoup(html_content, "html.parser")

        # 노이즈 태그 제거
        for tag in soup(["script", "style", "header", "footer", "nav", "aside", "form", "iframe", "noscript"]):
            tag.decompose()
        for tag in soup.select(".header, .footer, .nav, .menu, .sidebar, .lang-selector, #header, #footer, .util_box, .sns_share"):
            tag.decompose()

        # 언론사별 주요 본문 셀렉터 (구체적인 것 우선)
        selectors = [
            "#dic_area", "#articleBodyContents", "#articleBody",
            "#newsEndContents", "#articleContent",
            ".article_view", ".article-body", ".article_txt", "._article_content",
            "[itemprop='articleBody']", ".news_body", ".news_content", ".view_con",
        ]
        for sel in selectors:
            elem = soup.select_one(sel)
            if elem:
                text = elem.get_text(separator=" ", strip=True)
                if len(text) > 100:
                    return text[:max_len]

        # Fallback: <p> 태그 종합
        p_texts = [
            p.get_text(separator=" ", strip=True)
            for p in soup.select("p")
            if len(p.get_text(strip=True)) > 30
        ]
        if p_texts:
            text = " ".join(p_texts)
            if len(text) > 100:
                return text[:max_len]

    except Exception as e:
        logger.warning("HTML 본문 파싱 오류: %s", e)
    return None


def fetch_article_body(url: str, max_len: int = 5000) -> str | None:
    """기사 URL에서 본문 텍스트를 추출. 캐시 적중 시 즉시 반환."""
    if not url:
        return None

    with _body_cache_lock:
        cached = _body_cache.get(url)
    if cached:
        return cached

    try:
        resp = _http_session.get(url, timeout=4)
        resp.raise_for_status()
        html_content = resp.content.decode(resp.encoding or "utf-8", errors="ignore")
        body_text = _extract_body_bs4(html_content, max_len)
        if body_text:
            _cache_put(url, body_text)
            return body_text
    except requests.RequestException as e:
        logger.debug("기사 본문 요청 실패 (%s): %s", url, e)
    return None


def fetch_og_image(url: str) -> str | None:
    """기사 URL에서 og:image 또는 첫 번째 이미지 URL을 추출."""
    if not url:
        return None
    try:
        resp = _http_session.get(url, timeout=4.5)
        resp.raise_for_status()
        html_content = resp.content.decode(resp.encoding or "utf-8", errors="ignore")

        # 본문도 동시에 캐싱
        body_text = _extract_body_bs4(html_content)
        if body_text:
            _cache_put(url, body_text)

        # og:image 메타 태그 탐색 (속성 순서 두 가지 대응)
        for pattern in (
            r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
            r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']',
            r'<meta[^>]*name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)["\']',
        ):
            m = re.search(pattern, html_content, re.IGNORECASE)
            if m:
                return html.unescape(m.group(1)).strip()

        # Fallback: BS4로 첫 번째 실제 이미지 탐색
        if BS4_AVAILABLE:
            soup = BeautifulSoup(html_content, "html.parser")
            for img in soup.select("img"):
                src = img.get("src") or img.get("data-src") or ""
                if src.startswith("http") and not any(x in src for x in ("icon", "logo", "blank")):
                    return src

    except requests.RequestException as e:
        logger.debug("og:image 요청 실패 (%s): %s", url, e)
    except Exception as e:
        logger.warning("og:image 파싱 오류 (%s): %s", url, e)
    return None


# ── 카테고리별 뉴스 수집 (병렬 실행용) ─────────────────────────────

def _fetch_category(
    category: str,
    headers: dict[str, str],
    per_cat_limit: int,
    cutoff: datetime,
) -> list[dict]:
    """단일 카테고리 네이버 뉴스 검색 결과 반환. 실패 시 빈 리스트."""
    results: list[dict] = []
    try:
        resp = _http_session.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers=headers,
            params={"query": category, "display": 50, "sort": "sim"},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning("네이버 API 응답 이상 [%s]: HTTP %d", category, resp.status_code)
            return results

        for item in resp.json().get("items", []):
            if len(results) >= per_cat_limit:
                break

            # 24시간 이내 필터링
            pub_date_str = item.get("pubDate", "")
            if pub_date_str:
                try:
                    if parsedate_to_datetime(pub_date_str) < cutoff:
                        continue
                except Exception:
                    pass  # 날짜 파싱 실패 시 포함

            # 네이버 뉴스 링크 우선, 없으면 원문 링크
            link_naver = item.get("link", "")
            link_orig = item.get("originallink", "")
            link = link_naver if "n.news.naver.com" in link_naver else (link_orig or link_naver)

            # 링크가 없는 기사는 건너뜀
            if not link:
                continue

            stance, press = classify_media(link)
            results.append({
                "title": clean_html(item.get("title", "")),
                "description": clean_html(item.get("description", "")),
                "link": link,
                "press": press,
                "stance": stance,
                "stance_label": stance_label(stance),
                "pubDate": pub_date_str,
                "category": category,
            })

    except requests.RequestException as e:
        logger.warning("네이버 API 호출 실패 [%s]: %s", category, e)
    return results


# ── 트렌딩 뉴스 메인 함수 ──────────────────────────────────────────

def fetch_trending_news(
    client_id: str,
    client_secret: str,
    count: int = 16,
    categories: list[str] | None = None,
) -> dict:
    """
    실시간 트렌딩 뉴스를 수집하고 중복 제거 후 반환.

    Returns:
        {"main": article | None, "subs": list[article]}
    """
    if not client_id or not client_secret:
        raise ValueError("네이버 API 인증 정보가 필요합니다.")

    cats = categories or NEWS_CATEGORIES
    cache_key = ",".join(sorted(cats))

    # 60초 TTL 캐시
    with _trending_cache_lock:
        entry = _trending_cache.get(cache_key)
        if entry and (time.time() - entry[0]) < _TRENDING_TTL:
            logger.debug("트렌딩 뉴스 캐시 적중 [%s]", cache_key)
            return entry[1]

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    per_cat_limit = 20 if len(cats) == 1 else 3
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    # 모든 카테고리 병렬 호출
    all_results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(cats), 10)) as executor:
        future_map = {
            executor.submit(_fetch_category, cat, headers, per_cat_limit, cutoff): cat
            for cat in cats
        }
        for future in concurrent.futures.as_completed(future_map):
            try:
                all_results.extend(future.result())
            except Exception as e:
                logger.error("카테고리 수집 오류 [%s]: %s", future_map[future], e)

    # 중복 제거 (URL 기준 + 제목 의미 유사도)
    seen_links: set[str] = set()
    selected_token_sets: list[set[str]] = []
    ordered: list[dict] = []

    for art in all_results:
        link = art.get("link") or ""
        if not link or link in seen_links:
            continue

        tokens = set(re.findall(r"[가-힣a-zA-Z0-9]{2,}", art.get("title", "")))
        is_dup = any(
            len(tokens & ext) >= 3 or (tokens and len(tokens & ext) / len(tokens) > 0.4)
            for ext in selected_token_sets
        )
        if not is_dup:
            seen_links.add(link)
            selected_token_sets.append(tokens)
            ordered.append(art)

        if len(ordered) >= count + 1:
            break

    main = ordered[0] if ordered else None
    subs = ordered[1 : 1 + count] if len(ordered) > 1 else []

    # 이미지가 필요한 기사만 병렬로 og:image 수집
    needs_image = [a for a in ([main] + subs[6:14]) if a]
    if needs_image:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_art = {executor.submit(fetch_og_image, a["link"]): a for a in needs_image}
            for future in concurrent.futures.as_completed(future_to_art):
                art = future_to_art[future]
                try:
                    art["image_url"] = future.result()
                except Exception:
                    art["image_url"] = None

    result = {"main": main, "subs": subs}
    with _trending_cache_lock:
        _trending_cache[cache_key] = (time.time(), result)

    logger.info("트렌딩 뉴스 수집 완료: main=%s, subs=%d건", bool(main), len(subs))
    return result
