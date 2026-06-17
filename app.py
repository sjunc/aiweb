import os
import uvicorn
import json
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import naver_news
import debate_engine
import graph_engine

load_dotenv()

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_KEY_BACKUP = os.environ.get("GEMINI_API_KEY_BACKUP", "")

# API Keys list
GEMINI_KEYS = [k for k in [GEMINI_API_KEY, GEMINI_API_KEY_BACKUP] if k]

if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
    print("[WARN] .env에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 없습니다.")
if not GEMINI_KEYS:
    print("[WARN] Gemini API 키가 설정되어 있지 않습니다.")
else:
    print(f"[OK] {len(GEMINI_KEYS)}개의 Gemini API Key 로드 완료 (백업 자동 전환 활성화)")

# ML 분류기 로드 시도 (없으면 fallback)
ML_AVAILABLE = False
try:
    from ml_classifier import BiasClassifier
    _classifier = BiasClassifier()
    if _classifier.load():
        ML_AVAILABLE = True
        print("[OK] ML 분류기 모델 로드 완료")
    else:
        print("[INFO] ML 모델 파일 없음. python ml_classifier.py 로 학습 필요.")
except ImportError:
    _classifier = None
    print("[INFO] ml_classifier.py 없음. ML 분석 불가.")

app = FastAPI(
    title="뉴스밸런스 (NewsBalance)",
    description="뉴스 기사 정치 성향 분석 (ML 기반 % + Gemini 논평)",
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


class AnalyzeRequest(BaseModel):
    article: dict


class ChatRequest(BaseModel):
    article: dict
    user_message: str


def predict_ml(art: dict) -> dict:
    """ML 분류기로 기사 성향 % 예측 (로컬, 빠름)"""
    if not ML_AVAILABLE or not _classifier:
        return {"progressive": 0, "centrist": 0, "conservative": 0,
                "stance": "unknown", "stance_label": "분석불가", "confidence": 0}
    try:
        return _classifier.predict(
            art.get("title", ""),
            art.get("description", "")
        )
    except Exception as e:
        print(f"ML 예측 오류: {e}")
        return {"progressive": 0, "centrist": 0, "conservative": 0,
                "stance": "unknown", "stance_label": "오류", "confidence": 0}


@app.get("/")
async def read_index():
    path = os.path.join("static", "index.html")
    if not os.path.exists(path):
        raise HTTPException(404, "index.html 없음")
    return FileResponse(path)


@app.post("/api/body")
def get_article_body(req: AnalyzeRequest):
    """기사 본문만 빠르게 크롤링하여 반환 (Gemini 로딩 전 표시용)"""
    art = req.article
    if not art:
        raise HTTPException(400, "기사 정보가 필요합니다.")
    url = art.get("link", "") or art.get("originallink", "")
    description = art.get("description", "")
    body = naver_news.fetch_article_body(url) or description
    return {"body": body}


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "4.0.0",
        "naver_configured": bool(NAVER_CLIENT_ID),
        "gemini_configured": len(GEMINI_KEYS) > 0,
        "ml_available": ML_AVAILABLE,
    }


@app.get("/api/trending")
async def trending_news(category: str = ""):
    """트렌딩 뉴스 목록 + 각 기사의 ML % 함께 반환"""
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise HTTPException(400, "네이버 API 키가 .env에 설정되지 않았습니다.")
    try:
        cats = [c.strip() for c in category.split(",") if c.strip()] if category else None
        data = naver_news.fetch_trending_news(NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, categories=cats)

        # 모든 기사에 ML % 붙이기
        for art in [data.get("main")] + (data.get("subs") or []):
            if art:
                art["ml_analysis"] = predict_ml(art)
        return data
    except Exception as e:
        raise HTTPException(502, f"뉴스 수집 실패: {e}")


def _ingest_graph(api_keys, body, press):
    kg_data = graph_engine.extract_knowledge_graph(api_keys, body, press)
    if kg_data:
        graph_engine.ingest_to_neo4j(kg_data)


@app.post("/api/analyze")
def analyze_article(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    """기사 본문 + Gemini 논평 (ML %는 trending에서 이미 제공)"""
    art = req.article
    if not art:
        raise HTTPException(400, "기사 정보가 필요합니다.")

    title = art.get("title", "")
    description = art.get("description", "")
    url = art.get("link", "") or art.get("originallink", "")

    body = naver_news.fetch_article_body(url) or description

    gemini_result = None
    if GEMINI_KEYS:
        try:
            enrich = {**art, "body": body}
            gemini_result = debate_engine.analyze_commentary(GEMINI_KEYS, enrich, art.get("ml_analysis"))
        except Exception as e:
            print(f"Gemini 논평 오류: {e}")
            gemini_result = {
                "bias_alert": "Gemini 분석을 불러오지 못했습니다.",
                "balanced_view": "",
                "fact_check": "AI 분석 서비스에 일시적 오류가 발생했습니다.",
                "comparison": "",
            }

    return {
        "article": {"title": title, "body": body, "press": art.get("press", "")},
        "gemini_analysis": gemini_result or {
            "bias_alert": "", "balanced_view": "", "fact_check": "", "comparison": "",
        },
    }

@app.post("/api/graph/extract")
def extract_graph(req: AnalyzeRequest):
    art = req.article
    if not art:
        raise HTTPException(400, "기사 정보가 필요합니다.")
        
    url = art.get("link", "") or art.get("originallink", "")
    body = naver_news.fetch_article_body(url) or art.get("description", "")
    press = art.get("press", "")
    
    if not GEMINI_KEYS:
        raise HTTPException(400, "Gemini API 키가 설정되지 않았습니다.")
        
    # Extract
    kg_data = graph_engine.extract_knowledge_graph(GEMINI_KEYS, body, press)
    if kg_data:
        graph_engine.ingest_to_neo4j(kg_data)
        
    # Search Neo4j for connected subgraph
    import re
    keywords = [w for w in re.findall(r'\b\w+\b', art.get("title", "")) if len(w) >= 2]
    subgraph_data = graph_engine.search_subgraph(keywords)
    
    vis_data = subgraph_data.get("vis", {"nodes": [], "edges": []})
    text_data = subgraph_data.get("text", "")
    
    # Fallback: if search_subgraph returned nothing (no keyword overlap), just visualize what we just extracted!
    if not vis_data["nodes"] and kg_data:
        nodes_dict = {}
        edges = []
        for row in kg_data:
            src, rel, tgt, prs = row["source"], row["relation"], row["target"], row["press"]
            if src not in nodes_dict: nodes_dict[src] = {"id": src, "label": src, "group": "entity"}
            if tgt not in nodes_dict: nodes_dict[tgt] = {"id": tgt, "label": tgt, "group": "entity"}
            if prs not in nodes_dict: nodes_dict[prs] = {"id": prs, "label": prs, "group": "press", "shape": "box", "color": "#ff9a9e"}
            
            edges.append({"from": prs, "to": src, "label": "보도", "arrows": "to", "dashes": True})
            edges.append({"from": src, "to": tgt, "label": rel, "arrows": "to"})
            
        vis_data = {"nodes": list(nodes_dict.values()), "edges": edges}
        text_data = "현재 이 기사에서 단독으로 추출된 프레임입니다. (타 매체 연관 데이터 부족)"

    return {
        "subgraph_text": text_data or "현재 해당 이슈에 대해 추출된 지식 그래프가 없습니다.",
        "subgraph_vis": vis_data
    }


@app.post("/api/perspective")
def agent_perspective(req: ChatRequest):
    if not GEMINI_KEYS:
        raise HTTPException(400, "Gemini API 키가 설정되지 않았습니다.")
    try:
        reply = debate_engine.respond_as_panel(GEMINI_KEYS, req.article, req.user_message)
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(502, f"답변 생성 실패: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
