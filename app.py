"""
app.py — 뉴스밸런스 FastAPI 백엔드 서버

리팩토링 포인트:
- ArticleSchema Pydantic 모델로 article dict 필드 타입 보장 (KeyError 원천 차단)
- APP_VERSION 상수로 버전 단일 관리
- _resolve_keys() 헬퍼로 API 키 검증 중복 코드 제거
- _ingest_graph() 데드 코드 제거
- 내부 오류 메시지를 사용자에게 그대로 노출하는 HTTPException 수정
- pathlib.Path로 파일 경로 처리
- /health 엔드포인트에 Neo4j 상태 추가
- 타입 어노테이션 및 docstring 정비
"""
import logging
import os
import re
from pathlib import Path
from typing import Optional

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import debate_engine
import graph_engine
import naver_news

# ── 로깅 설정 ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── 환경 변수 로드 ─────────────────────────────────────────────────
load_dotenv()

APP_VERSION = "4.2.0"

NAVER_CLIENT_ID: str = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET: str = os.environ.get("NAVER_CLIENT_SECRET", "")
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_KEY_BACKUP: str = os.environ.get("GEMINI_API_KEY_BACKUP", "")
GEMINI_KEYS: list[str] = [k for k in [GEMINI_API_KEY, GEMINI_API_KEY_BACKUP] if k]

if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
    logger.warning(".env에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 없습니다.")
if not GEMINI_KEYS:
    logger.warning("Gemini API 키가 설정되어 있지 않습니다. AI 분석 기능이 비활성화됩니다.")
else:
    logger.info("%d개의 Gemini API Key 로드 완료 (백업 자동 전환 활성화)", len(GEMINI_KEYS))

# ── ML 분류기 로드 ─────────────────────────────────────────────────
_classifier = None
ML_AVAILABLE = False
try:
    from ml_classifier import BiasClassifier
    _classifier = BiasClassifier()
    if _classifier.load():
        ML_AVAILABLE = True
        logger.info("ML 분류기 모델 로드 완료")
    else:
        logger.info("ML 모델 파일 없음 — python ml_classifier.py 실행으로 학습 가능")
except ImportError:
    logger.info("ml_classifier.py 없음 — ML 분석 비활성화")

# ── FastAPI 앱 초기화 ──────────────────────────────────────────────
app = FastAPI(
    title="뉴스밸런스 (NewsBalance)",
    description="뉴스 기사 정치 성향 분석 — ML 기반 % + Gemini 2.5 Flash 논평",
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_static_dir = Path("static")
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ── Pydantic 스키마 ────────────────────────────────────────────────

class ArticleSchema(BaseModel):
    """기사 데이터 스키마 — 모든 필드에 기본값을 지정하여 KeyError 원천 차단"""
    title: str = ""
    description: str = ""
    link: str = ""
    press: str = ""
    stance: str = "unknown"
    stance_label: str = "판별불가"
    pubDate: str = ""
    category: str = ""
    image_url: Optional[str] = None
    ml_analysis: Optional[dict] = None


class AnalyzeRequest(BaseModel):
    article: ArticleSchema
    api_key: Optional[str] = Field(default=None, description="사용자 제공 Gemini API 키 (선택)")


class ChatRequest(BaseModel):
    article: ArticleSchema
    user_message: str = Field(..., min_length=1, description="사용자 질문")
    api_key: Optional[str] = Field(default=None, description="사용자 제공 Gemini API 키 (선택)")


# ── 내부 헬퍼 ─────────────────────────────────────────────────────

def _resolve_keys(user_key: Optional[str]) -> list[str]:
    """요청별 API 키 목록을 반환. 유효한 키가 없으면 HTTPException 400."""
    keys = [user_key] if user_key else GEMINI_KEYS
    if not keys:
        raise HTTPException(
            status_code=400,
            detail="Gemini API 키가 설정되지 않았습니다. 화면에서 API 키를 직접 입력하거나 서버 .env를 설정해 주세요.",
        )
    return keys


def _predict_ml(art: ArticleSchema) -> dict:
    """ML 분류기로 기사 성향 % 예측. 모델 없거나 오류 시 기본값 반환."""
    default = {
        "progressive": 0, "centrist": 0, "conservative": 0,
        "stance": "unknown", "stance_label": "분석불가", "confidence": 0,
    }
    if not ML_AVAILABLE or not _classifier:
        return default
    try:
        return _classifier.predict(art.title, art.description)
    except Exception as e:
        logger.error("ML 예측 오류: %s", e)
        return {**default, "stance_label": "오류"}


def _build_vis_from_kg(kg_data: list[dict]) -> dict:
    """Neo4j 검색 결과가 없을 때 추출된 kg_data로 직접 vis.js 데이터 생성"""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for row in kg_data:
        src = row.get("source")
        rel = row.get("relation")
        tgt = row.get("target")
        prs = row.get("press", "언론사")
        if not src or not tgt:
            continue
        nodes.setdefault(src, {"id": src, "label": src, "group": "entity"})
        nodes.setdefault(tgt, {"id": tgt, "label": tgt, "group": "entity"})
        nodes.setdefault(prs, {"id": prs, "label": prs, "group": "press", "shape": "box", "color": "#ff9a9e"})
        edges.append({"from": prs, "to": src, "label": "보도", "arrows": "to", "dashes": True})
        edges.append({"from": src, "to": tgt, "label": rel, "arrows": "to"})
    return {"nodes": list(nodes.values()), "edges": edges}


# ── 라우터 ────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def serve_landing():
    """랜딩 페이지(index.html) 서빙"""
    path = _static_dir / "index.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="index.html을 찾을 수 없습니다.")
    return FileResponse(str(path))


@app.get("/health", summary="서버 헬스체크")
async def health():
    """서버 구성 상태를 반환 (모니터링용)"""
    return {
        "status": "healthy",
        "version": APP_VERSION,
        "naver_configured": bool(NAVER_CLIENT_ID),
        "gemini_configured": len(GEMINI_KEYS) > 0,
        "gemini_key_count": len(GEMINI_KEYS),
        "ml_available": ML_AVAILABLE,
        "neo4j_available": graph_engine.is_neo4j_available(),
    }


@app.get("/api/trending", summary="실시간 트렌딩 뉴스 목록")
async def trending_news(category: str = ""):
    """카테고리별 트렌딩 뉴스를 ML 편향 분석 결과와 함께 반환"""
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise HTTPException(status_code=400, detail="네이버 API 키가 서버에 설정되지 않았습니다.")
    try:
        cats = [c.strip() for c in category.split(",") if c.strip()] if category else None
        data = naver_news.fetch_trending_news(NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, categories=cats)

        # 모든 기사에 ML % 첨부
        for art_dict in [data.get("main")] + (data.get("subs") or []):
            if art_dict:
                art_schema = ArticleSchema(**{k: art_dict[k] for k in ArticleSchema.model_fields if k in art_dict})
                art_dict["ml_analysis"] = _predict_ml(art_schema)

        return data
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("트렌딩 뉴스 수집 실패: %s", e)
        raise HTTPException(status_code=502, detail="뉴스 수집 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


@app.post("/api/body", summary="기사 본문 빠른 크롤링")
def get_article_body(req: AnalyzeRequest):
    """기사 URL에서 본문만 빠르게 크롤링하여 반환 (Gemini 분석 전 미리 표시용)"""
    url = req.article.link
    body = naver_news.fetch_article_body(url) if url else None
    return {"body": body or req.article.description}


@app.post("/api/analyze", summary="기사 Gemini 편향 분석")
def analyze_article(req: AnalyzeRequest):
    """기사 본문을 크롤링하고 Gemini로 편향 논평을 생성하여 반환"""
    keys = _resolve_keys(req.api_key)
    art = req.article

    body = naver_news.fetch_article_body(art.link) if art.link else None
    body = body or art.description

    try:
        enrich = art.model_dump()
        enrich["body"] = body
        gemini_result = debate_engine.analyze_commentary(keys, enrich, art.ml_analysis)
    except Exception as e:
        logger.error("Gemini 논평 생성 실패: %s", e)
        gemini_result = {
            "bias_alert": "AI 분석 서비스에 일시적 오류가 발생했습니다.",
            "balanced_view": "", "fact_check": "", "comparison": "",
            "reframed_neutral": "", "reframed_progressive": "", "reframed_conservative": "",
        }

    return {
        "article": {"title": art.title, "body": body, "press": art.press},
        "gemini_analysis": gemini_result,
    }


@app.post("/api/graph/extract", summary="GraphRAG 지식 그래프 추출")
def extract_graph(req: AnalyzeRequest):
    """기사 및 관련 기사들에서 지식 그래프를 추출하고 Neo4j에 적재 후 서브그래프를 반환"""
    keys = _resolve_keys(req.api_key)
    art = req.article

    body = (naver_news.fetch_article_body(art.link) if art.link else None) or art.description
    articles_for_graph = [{"press": art.press, "text": body}]

    # 관련 기사 추가 수집 (실패해도 메인 기사로 진행)
    if NAVER_CLIENT_ID:
        keywords = " ".join(
            [w for w in re.findall(r"[가-힣]{2,}", art.title) if len(w) >= 2][:3]
        )
        if keywords:
            try:
                resp = requests.get(
                    "https://openapi.naver.com/v1/search/news.json",
                    headers={
                        "X-Naver-Client-Id": NAVER_CLIENT_ID,
                        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
                    },
                    params={"query": keywords, "display": 5, "sort": "sim"},
                    timeout=3,
                )
                if resp.status_code == 200:
                    for item in resp.json().get("items", []):
                        link = item.get("originallink") or item.get("link", "")
                        if not link or link == art.link:
                            continue
                        _, rel_press = naver_news.classify_media(link)
                        rel_body = naver_news.clean_html(item.get("description", ""))
                        if rel_body and rel_press not in (art.press, "기타"):
                            articles_for_graph.append({"press": rel_press, "text": rel_body})
                        if len(articles_for_graph) >= 4:
                            break
            except Exception as e:
                logger.warning("관련 기사 수집 실패 (메인 기사만 사용): %s", e)

    kg_data = graph_engine.extract_knowledge_graph(keys, articles_for_graph)
    if kg_data:
        graph_engine.ingest_to_neo4j(kg_data)

    keywords_list = [w for w in re.findall(r"[가-힣]{2,}", art.title) if len(w) >= 2]
    subgraph_data = graph_engine.search_subgraph(keywords_list)

    vis_data = subgraph_data.get("vis", {"nodes": [], "edges": []})
    text_data = subgraph_data.get("text", "")

    # Neo4j 결과가 없으면 방금 추출한 kg_data로 시각화
    if not vis_data["nodes"] and kg_data:
        vis_data = _build_vis_from_kg(kg_data)
        text_data = "현재 분석된 관련 기사들에서 추출된 통합 프레임입니다."

    return {
        "subgraph_text": text_data or "해당 이슈에 대해 추출된 지식 그래프가 없습니다.",
        "subgraph_vis": vis_data,
    }


@app.post("/api/perspective", summary="AI 패널 질의응답")
def agent_perspective(req: ChatRequest):
    """GraphRAG 컨텍스트 기반 AI 패널 답변 생성"""
    keys = _resolve_keys(req.api_key)
    try:
        reply = debate_engine.respond_as_panel(keys, req.article.model_dump(), req.user_message)
        return {"reply": reply}
    except Exception as e:
        logger.error("AI 패널 답변 생성 실패: %s", e)
        raise HTTPException(status_code=502, detail="답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
