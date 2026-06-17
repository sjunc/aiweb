"""
graph_engine.py — GraphRAG 기반 이슈 오염도 및 프레임 추적기

Gemini를 사용하여 기사에서 Entity-Relation을 추출하고 Neo4j에 적재합니다.
"""
import json
import os
from neo4j import GraphDatabase
from google import genai
from google.genai import types

# Neo4j 설정
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "newsbalance123")

driver = None
try:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
except Exception as e:
    print(f"[WARN] Neo4j 연결 실패: {e}")

GRAPH_EXTRACTION_PROMPT = """
당신은 지식 그래프 구축 전문가입니다. 다음 기사 본문에서 가장 핵심이 되는 인물, 조직, 사건, 개념(Entity)과 이들 간의 관계(Relation)를 추출하십시오.
추출할 때, 기사가 특정 사건을 어떤 프레임으로 묘사하고 있는지 드러날 수 있도록 명시적인 동사나 관계형 명사를 사용하십시오.

[기사 정보]
- 언론사: {press}
- 본문: {text}

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

def extract_knowledge_graph(api_keys: list[str] | str, text: str, press_name: str) -> list[dict]:
    """Gemini를 사용해 텍스트에서 지식 그래프 트리플을 추출합니다."""
    if not text:
        return []
    
    keys = [api_keys] if isinstance(api_keys, str) else [k for k in api_keys if k]
    if not keys:
        return []

    for key in keys:
        try:
            client = genai.Client(api_key=key)
            prompt = GRAPH_EXTRACTION_PROMPT.format(press=press_name, text=text[:3000])
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                )
            )
            raw_text = response.text.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()
            data = json.loads(raw_text)
            if isinstance(data, list):
                for item in data:
                    item['press'] = press_name
                return data
            return []
        except Exception as e:
            print(f"[WARN] Graph Extraction Gemini 에러 ({type(e).__name__}): {e}")
            continue
    return []

def ingest_to_neo4j(kg_data: list[dict]):
    """추출된 JSON 배열을 Neo4j에 MERGE 합니다."""
    if not driver or not kg_data:
        return

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
    except Exception as e:
        print(f"[WARN] Neo4j 적재 실패: {e}")

def search_subgraph(query_entities: list[str]) -> dict:
    """엔티티 목록을 기반으로 Neo4j에서 서브그래프를 검색하고 텍스트 및 시각화 데이터를 반환합니다."""
    if not driver or not query_entities:
        return {"text": "", "vis": {"nodes": [], "edges": []}}

    query = """
    MATCH (s:Entity)-[r:RELATION]->(t:Entity)
    WHERE s.name IN $entities OR t.name IN $entities
    RETURN s.name AS source, r.type AS relation, t.name AS target, r.press AS presses
    LIMIT 30
    """
    try:
        with driver.session() as session:
            result = session.run(query, entities=query_entities)
            lines = []
            nodes = {}
            edges = []
            
            for record in result:
                src = record['source']
                rel = record['relation']
                tgt = record['target']
                presses = record['presses'] or []
                
                # Text formulation
                lines.append(f"({src}) -[{rel} (보도: {', '.join(presses)})]-> ({tgt})")
                
                # Vis.js formatting
                if src not in nodes:
                    nodes[src] = {"id": src, "label": src, "group": "entity"}
                if tgt not in nodes:
                    nodes[tgt] = {"id": tgt, "label": tgt, "group": "entity"}
                
                # Create edges for each press to show propagation
                for press in presses:
                    if press not in nodes:
                        nodes[press] = {"id": press, "label": press, "group": "press", "shape": "box", "color": "#ff9a9e"}
                    edges.append({"from": press, "to": src, "label": "보도", "arrows": "to", "dashes": True})
                
                edges.append({"from": src, "to": tgt, "label": rel, "arrows": "to"})

            return {
                "text": "\n".join(lines) if lines else "",
                "vis": {
                    "nodes": list(nodes.values()),
                    "edges": edges
                }
            }
    except Exception as e:
        print(f"[WARN] Neo4j 검색 실패: {e}")
        return {"text": "", "vis": {"nodes": [], "edges": []}}
