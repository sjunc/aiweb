"""
debate_engine.py — Gemini 기반 뉴스 편향 논평 생성기

ML 분류기(ml_classifier.py)가 수치 %를 담당하고,
Gemini는 언어적 편향 포인트, 타 성향 언론사 비교, 균형 시각, 팩트체크만 제공한다.
"""

import json
from google import genai
from google.genai import types

COMMENTARY_PROMPT = """
당신은 미디어 리터러시 교육 전문가입니다.
ML 분류기가 이미 이 기사의 정치 성향을 수치화했습니다.
당신은 그 결과를 바탕으로 독자가 균형 잡힌 시각을 가질 수 있도록 도와주십시오.

[기사 정보]
- 언론사: {press} (분류: {stance_label})
- 제목: {title}
- 본문: {body}

[ML 분석 결과]
- 진보 성향: {progressive}%
- 중도 성향: {centrist}%
- 보수 성향: {conservative}%

다음 네 가지 항목을 JSON으로 출력하십시오. 음모론은 절대 배제하고 팩트에 기반하십시오.

1. bias_alert: 이 기사의 언어적/표현적 편향 포인트를 2~3문장으로 지적
2. balanced_view: 같은 주제에 대해 다른 성향의 언론사(예: 진보-중도-보수)는 각각 어떤 관점으로 보도하는지 비교 설명 (3~5문장)
3. fact_check: 이 기사에서 확인이 필요하거나 추가 맥락이 필요한 사실/주장 1~2개
4. comparison: 이 기사가 같은 주제를 다루는 중도·진보 언론과 비교했을 때 어떤 차이가 있는지 2~3문장

응답 JSON:
{{{{
  "bias_alert": "...",
  "balanced_view": "...",
  "fact_check": "...",
  "comparison": "..."
}}}}
"""


def analyze_commentary(api_key: str, article: dict, ml_result: dict | None = None) -> dict:
    """Gemini에 편향 논평만 요청 (수치 분석은 ML 모델이 담당)"""
    if not api_key:
        raise ValueError("Gemini API Key가 필요합니다.")

    client = genai.Client(api_key=api_key)

    prompt = COMMENTARY_PROMPT.format(
        press=article.get("press", "알 수 없음"),
        stance_label=article.get("stance_label", "판별불가"),
        title=article.get("title", ""),
        body=(article.get("body") or article.get("description", ""))[:3000],
        progressive=ml_result.get("progressive", 0) if ml_result else 0,
        centrist=ml_result.get("centrist", 0) if ml_result else 0,
        conservative=ml_result.get("conservative", 0) if ml_result else 0,
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
                response_mime_type="application/json",
            )
        )
        return json.loads(response.text)
    except json.JSONDecodeError as je:
        raise Exception(f"JSON 파싱 오류: {je}\n원문: {response.text}")
    except Exception as e:
        raise Exception(f"분석 중 오류: {e}")


def respond_as_panel(api_key, article, user_message):
    if not api_key:
        raise ValueError("Gemini API Key가 필요합니다.")
    client = genai.Client(api_key=api_key)

    prompt = f"""
당신은 미디어 리터러시 교육을 위한 편향 분석 전문가입니다.
다음 기사에 대해 사용자의 질문에 편향되지 않은 시각으로 답변하십시오.

[기사 정보]
- 언론사: {article.get("press", "")}
- 제목: {article.get("title", "")}
- 요약: {article.get("description", "")}

[사용자 질문]
{user_message}

위 기사에 대해 편향되지 않고 균형 잡힌 시각을 유지하며,
사용자의 질문에 논리적이고 정중하게 3문장 내외로 답변하십시오.
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        raise Exception(f"답변 생성 중 오류: {e}")
