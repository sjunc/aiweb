# 📰 뉴스밸런스 (NewsBalance) — AI 미디어 리터러시 플랫폼

> **뉴스 기사의 정치적 편향을 실시간 머신러닝으로 분석하고, 최신 Gemini 2.5 Flash 및 GraphRAG를 활용해 다각적 팩트체크 리포트를 생성하는 차세대 미디어 리터러시 웹 서비스**

* **실시간 데모 및 랜딩 페이지**: [바로가기](https://s12.aiweb2026.site/) 

---

## 🚀 주요 AI 및 분석 기능 (Key Features)

### 1. 🧠 초경량 머신러닝 편향성 분석 (ML Bias Classifier)
- 대한민국 26개 주요 언론사의 기사 데이터 약 20,000건을 학습한 **TF-IDF + LDA 토픽 모델링 + 다중 클래스 로지스틱 회귀(Logistic Regression)** 파이프라인.
- **245KB 단일 모델 파일 (`bias_model.pkl`)** 로 압축되어 서버 메모리 점유를 최소화하며, 1ms 내외의 초고속 연산으로 진보·중도·보수 확률(%)을 도출합니다.

### 2. 🤖 Gemini 2.5 Flash 기반 다각적 리포트 & 팩트체크
- 분석된 ML 편향성 데이터를 Gemini API 컨텍스트에 주입하여, 단순 요약을 넘어 **기사 내 편향 포인트 지적, 타 성향 보도 비교, 상세 팩트체크**를 생성합니다.
- **프레이밍별 대체 헤드라인 비교:** 동일한 사건을 중립 / 진보 / 보수 관점에서 어떻게 다르게 제목으로 뽑아낼 수 있는지 3가지 버전의 헤드라인을 자동 생성하여 프레이밍 효과를 시각화합니다.
- **대화형 AI 에이전트:** 기사를 읽다가 궁금한 점이나 '숨겨진 프레임' 등에 대해 즉각적으로 AI 패널에게 질문하고 답변을 받을 수 있습니다.

### 3. 🕸️ GraphRAG 프레임 확산 그래프 시각화 (Knowledge Graph)
- Gemini를 활용하여 기사 본문의 주요 키워드(인물, 사건, 개념)와 그들 간의 인과/대립/연관 관계를 추출해 **지식 그래프(Knowledge Graph)** 로 모델링합니다.
- `vis-network`를 연동하여 기사의 숨은 구조와 프레임을 한눈에 파악할 수 있는 동적 시각화 네트워크를 그려냅니다.

---

## 🛠️ 백엔드 및 인프라 아키텍처 (Engineering Highlights)

### 1. 고성능 병렬 뉴스 수집 파이프라인
- **Connection Pooling & Concurrency:** 파이썬 `requests.Session` 객체를 활용한 커넥션 풀링과 10개의 `ThreadPoolExecutor`를 결합하여, 네이버 뉴스 검색 API와 썸네일(`og:image`), 기사 본문을 병목 없이 초고속으로 수집합니다.
- **Semantic Duplicate Filtering:** 단순히 제목의 특수문자만 필터링하는 것을 넘어, 제목을 단어(Token) 단위로 쪼개고 의미적 교집합(Jaccard/Token Overlap)을 비교하여 내용이 같은 중복 뉴스를 완벽하게 걸러냅니다.
- **Robust Fallback Extraction:** 언론사별 파편화된 HTML 구조 대응을 위해 표준화된 '네이버 뉴스 본문'을 최우선 스크래핑하며, `og:image` 누락 시 `BeautifulSoup4`를 이용해 기사 본문의 첫 번째 이미지를 억지로 추출하는 강력한 2차 안전망을 구축했습니다.

### 2. 무중단 API & 캐시 최적화
- **이중 API 키 자동 로테이션:** 메인 Gemini API 키의 한도 초과(Quota Exceeded) 시 환경 변수의 백업 키로 즉시 전환되는 장애 조치(Failover)가 설계되어 있습니다.
- **서버사이드 메모리 캐싱:** 카테고리별 트렌딩 뉴스 목록 및 기사 본문 원문은 TTL(Time-To-Live) 기반의 스레드 안전(Thread-Safe) 딕셔너리로 캐시되어 불필요한 네트워크 I/O를 획기적으로 줄였습니다.

### 3. 도커 기반 배포 (AWS EC2 + Docker Compose)
- 단일 `docker-compose.yml`을 통해 **FastAPI 백엔드 앱 컨테이너**와 **Nginx 웹 서버 프록시**를 묶어 포트 80으로 무중단 서빙합니다.

---

## 📁 주요 폴더 및 파일 구조

```text
├── static/                     # 웹 대시보드(데모) 프론트엔드 정적 파일
│   ├── index.html              # 실시간 뉴스피드, AI 리포트, 대체 헤드라인 및 GraphRAG UI
│   ├── app.js                  # 비동기 API 연동, 모달 제어, Graph 시각화 처리 로직
│   └── style.css               # 다크 테마 글래스모피즘(Glassmorphism) 반응형 스타일
├── index.html                  # 단일 칼럼 옛날 신문 양식의 랜딩 페이지 (설명 페이지)
├── contents.md                 # 랜딩 페이지에 동적으로 바인딩(zero-md)되는 프로젝트 요약 마크다운
├── app.py                      # FastAPI 백엔드 메인 서버 (API 라우팅, CORS, 정적 서빙)
├── debate_engine.py            # Gemini 2.5 Flash API 호출 (AI 논평, 헤드라인 비교 프롬프트 등)
├── graph_engine.py             # GraphRAG 노드/에지 추출 및 그래프 생성 엔진
├── naver_news.py               # 네이버 API 연동, 커넥션 풀링 기반 병렬 스크래퍼 및 중복 필터
├── ml_classifier.py            # 머신러닝 학습 파이프라인 (TF-IDF + LDA + Logistic Regression)
├── bias_model.pkl              # 학습이 완료된 245KB 초경량 분류 모델 직렬화 파일
├── Dockerfile                  # 앱 빌드 명세
└── docker-compose.yml          # FastAPI와 Nginx를 연동하는 컨테이너 오케스트레이션
```

---

## 🚀 로컬 환경 실행 가이드

1. **저장소 클론 및 패키지 설치**
```bash
git clone https://github.com/sjunc/aiweb.git newsbalance
cd newsbalance
pip install -r requirements.txt
```

2. **환경 변수 파일 (`.env`) 세팅**
```env
GEMINI_API_KEY=당신의_제미나이_키
GEMINI_API_KEY_BACKUP=당신의_예비_제미나이_키 (선택)
NAVER_CLIENT_ID=당신의_네이버_클라이언트_ID
NAVER_CLIENT_SECRET=당신의_네이버_시크릿
```

3. **서버 실행**
```bash
# 로컬 개발용 uvicorn 실행
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```
브라우저에서 `http://localhost:8000` (랜딩 페이지) 또는 `http://localhost:8000/static/index.html` (데모 서비스)로 접속합니다.
