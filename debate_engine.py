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
독자가 기사를 균형 잡힌 시각으로 볼 수 있도록 편향을 분석하십시오.

[기사 정보]
- 언론사: {press} (분류: {stance_label})
- 제목: {title}
- 본문: {body}

[ML 분석 결과]
- 진보 성향: {progressive}%
- 중도 성향: {centrist}%
- 보수 성향: {conservative}%

[응답 규칙 - 가독성 및 속도 극대화]
- 모든 분석은 기사에 나타난 구체적인 팩트(수치, 인물, 발언, 단어 등)를 실제 근거로 직접 제시하십시오.
- 추측성 진술이나 '~할 것입니다', '~것으로 보입니다', '~추정됩니다' 등의 모호하고 불확실한 표현을 절대 사용하지 마십시오.
- 반드시 사실에 기반하여 확실하고 명확한 어조('~합니다', '~입니다', '~이 확인됩니다')로 단정지어 답변하십시오.
- 빠른 응답 속도를 위해 군더더기를 없애고 핵심만 담아 1~2문장 내외로 극도로 간결하게 작성하십시오.

다음 네 가지 항목을 JSON으로 출력하십시오.

1. bias_alert: 기사에서 사용된 구체적인 편향적 표현이나 어휘(실제 근거 포함)를 지적 (1~2문장)
2. balanced_view: 같은 사건에 대해 타 성향의 언론이 취하는 대조적인 관점 비교 (2문장 내외)
3. fact_check: 기사의 주장 중 추가 검증이 필요하거나 다른 팩트와 충돌하는 부분 지적 (1~2문장)
4. comparison: 본 기사가 타 매체와 차별화되게 강조하거나 축소한 보도 방식 (1~2문장)

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

[응답 규칙]
- 추측성 표현이나 '~할 것입니다', '~것으로 보입니다' 등을 사용하지 마십시오.
- 기사나 일반 팩트에 근거하여 단정적이고 명확한 어조('~합니다', '~입니다')로 답변하십시오.
- 논리적이고 정중하게 2~3문장 내외로 간결하게 답변하십시오.
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        raise Exception(f"답변 생성 중 오류: {e}")
