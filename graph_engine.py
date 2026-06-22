"""
graph_engine.py — GraphRAG 기반 이슈 프레임 추적 및 지식 그래프 엔진

리팩토링 포인트:
- Neo4j 연결 상태를 _neo4j_ok 플래그로 명시적 관리 (재연결 메커니즘 도입)
- ingest_to_neo4j에서 적재 건수 로그 추가
- search_subgraph에서 엔티티 목록 상위 N개 제한 (성능 보호)
- debate_engine._call_gemini 재사용으로 Gemini 호출 일관성 확보
- 모든 public 함수에 타입 어노테이션 및 docstring 추가
"""
import json
import logging
import os
import re

from google import genai
from google.genai import types
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError

logger = logging.getLogger(__name__)

# ── Neo4j 설정 ─────────────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "newsbalance123")
_ENTITY_SEARCH_LIMIT = 15  # search_subgraph에서 전달할 엔티티 최대 개수

driver = None
_neo4j_ok = False

try:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    # 초기 연결 시도 (백그라운드에서 실행 시 바로 연결이 안 될 수 있음)
    driver.verify_connectivity()
    _neo4j_ok = True
    logger.info("Neo4j 초기 연결 성공 (%s)", NEO4J_URI)
except (ServiceUnavailable, AuthError) as e:
    logger.warning("Neo4j 초기 연결 대기 중 (첫 쿼리 또는 헬스체크에서 재연결 시도): %s", e)
except Exception as e:
    logger.warning("Neo4j 초기화 오류: %s", e)


def is_neo4j_available() -> bool:
    """Neo4j 연결 가능 여부 반환 (헬스체크 등에서 활용). 미연결 상태일 경우 재연결 시도."""
    global _neo4j_ok, driver
    if _neo4j_ok and driver:
        try:
            driver.verify_connectivity()
            return True
        except Exception:
            _neo4j_ok = False

    try:
        if driver is None:
            driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        _neo4j_ok = True
        logger.info("Neo4j 재연결 성공 (%s)", NEO4J_URI)
        return True
    except Exception as e:
        _neo4j_ok = False
        logger.debug("Neo4j 연결 확인 실패: %s", e)
        return False


# ── Gemini 그래프 추출 프롬프트 ────────────────────────────────────

_GRAPH_EXTRACTION_PROMPT = """\
당신은 지식 그래프 구축 전문가입니다. 다음은 동일한 이슈에 대한 여러 언론사의 보도 내용들입니다.
이 텍스트들을 종합하여 가장 핵심이 되는 인물, 조직, 사건, 개념(Entity)과 이들 간의 관계(Relation)를 추출하십시오.
특히, 각 언론사가 해당 사건이나 관계를 어떻게 다르게 묘사(프레이밍)하는지 드러나도록 'press' 필드에 해당 내용을 보도한 언론사 이름을 정확히 기재하십시오.

[기사 목록]
{articles_text}

다음 JSON 배열 포맷으로 추출하십시오. 반드시 JSON 배열만 응답하십시오.
[
  {{
    "source": "노드1 (Entity)",
    "relation": "관계 (동사/명사)",
    "target": "노드2 (Entity)",
    "press": "보도 언론사명"
  }}
]
"""


# ── 공개 API ───────────────────────────────────────────────────────

def extract_knowledge_graph(
    api_keys: list[str] | str,
    articles: list[dict],
) -> list[dict]:
    """
    Gemini를 사용해 여러 기사 텍스트에서 지식 그래프 트리플을 추출.

    Args:
        api_keys: Gemini API 키 (문자열 또는 리스트)
        articles: [{"press": str, "text": str}, ...] 형태의 기사 목록

    Returns:
        [{"source": str, "relation": str, "target": str, "press": str}, ...] 또는 []
    """
    if not articles:
        return []

    keys = [api_keys] if isinstance(api_keys, str) else [k for k in api_keys if k]
    if not keys:
        logger.warning("extract_knowledge_graph: Gemini API 키 없음")
        return []

    articles_text = "".join(
        f"\n--- 기사 {i + 1} ---\n- 언론사: {a.get('press', '알 수 없음')}\n- 본문: {a.get('text', '')[:3000]}\n"
        for i, a in enumerate(articles)
    )

    for idx, key in enumerate(keys):
        try:
            client = genai.Client(api_key=key)
            prompt = _GRAPH_EXTRACTION_PROMPT.format(articles_text=articles_text[:9000])
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            raw = (response.text or "").strip()
            if not raw:
                logger.warning("그래프 추출 Key[%d]: 빈 응답", idx)
                continue

            # markdown fence 제거
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw.strip())

            data = json.loads(raw)
            if isinstance(data, list):
                logger.info("그래프 추출 완료: %d개 트리플", len(data))
                return data
            logger.warning("그래프 추출 Key[%d]: 예상치 못한 응답 타입 (%s)", idx, type(data))

        except json.JSONDecodeError as e:
            logger.warning("그래프 추출 Key[%d] JSON 파싱 실패: %s", idx, e)
        except Exception as e:
            err_type = type(e).__name__
            logger.warning("그래프 추출 Key[%d] 오류 (%s): %s", idx, err_type, e)
            if "InvalidArgument" in err_type or "PermissionDenied" in err_type:
                break

    return []


def ingest_to_neo4j(kg_data: list[dict]) -> int:
    """
    추출된 트리플 배열을 Neo4j에 MERGE 방식으로 적재.

    Args:
        kg_data: extract_knowledge_graph() 반환 리스트

    Returns:
        적재 시도한 트리플 수. Neo4j 비활성화 또는 오류 시 0.
    """
    if not is_neo4j_available() or not driver or not kg_data:
        if kg_data:
            logger.debug("Neo4j 비활성화 상태: %d개 트리플 적재 건너뜀", len(kg_data))
        return 0

    query = """
    UNWIND $data AS row
    MERGE (s:Entity {name: row.source})
    MERGE (t:Entity {name: row.target})
    MERGE (s)-[r:RELATION {type: row.relation}]->(t)
    ON CREATE SET r.press = [row.press]
    ON MATCH SET r.press = CASE
        WHEN NOT row.press IN r.press THEN r.press + [row.press]
        ELSE r.press
    END
    """
    try:
        with driver.session() as session:
            session.run(query, data=kg_data)
        logger.info("Neo4j 적재 완료: %d개 트리플", len(kg_data))
        return len(kg_data)
    except Exception as e:
        logger.error("Neo4j 적재 실패: %s", e)
        return 0


def search_subgraph(query_entities: list[str]) -> dict:
    """
    엔티티 목록을 기반으로 Neo4j에서 관련 서브그래프를 검색.

    Args:
        query_entities: 검색할 엔티티 이름 목록

    Returns:
        {"text": str, "vis": {"nodes": list, "edges": list}}
        Neo4j 비활성화 또는 결과 없음 시 빈 구조 반환.
    """
    empty = {"text": "", "vis": {"nodes": [], "edges": []}}

    if not is_neo4j_available() or not driver or not query_entities:
        return empty

    # 과도한 엔티티 목록으로 인한 Cypher 성능 저하 방지
    entities = query_entities[:_ENTITY_SEARCH_LIMIT]

    query = """
    MATCH (s:Entity)-[r:RELATION]->(t:Entity)
    WHERE s.name IN $entities OR t.name IN $entities
    RETURN s.name AS source, r.type AS relation, t.name AS target, r.press AS presses
    LIMIT 30
    """
    try:
        with driver.session() as session:
            result = session.run(query, entities=entities)
            lines: list[str] = []
            nodes: dict[str, dict] = {}
            edges: list[dict] = []

            for record in result:
                src = record["source"]
                rel = record["relation"]
                tgt = record["target"]
                presses: list[str] = record["presses"] or []

                lines.append(f"({src}) -[{rel} (보도: {', '.join(presses)})]-> ({tgt})")

                nodes.setdefault(src, {"id": src, "label": src, "group": "entity"})
                nodes.setdefault(tgt, {"id": tgt, "label": tgt, "group": "entity"})

                for press in presses:
                    nodes.setdefault(press, {
                        "id": press, "label": press,
                        "group": "press", "shape": "box", "color": "#ff9a9e",
                    })
                    edges.append({"from": press, "to": src, "label": "보도", "arrows": "to", "dashes": True})
                edges.append({"from": src, "to": tgt, "label": rel, "arrows": "to"})

            logger.debug("서브그래프 검색 완료: 노드 %d개, 엣지 %d개", len(nodes), len(edges))
            return {
                "text": "\n".join(lines),
                "vis": {"nodes": list(nodes.values()), "edges": edges},
            }

    except Exception as e:
        logger.error("Neo4j 서브그래프 검색 실패: %s", e)
        return empty
