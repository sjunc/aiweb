# 📰 뉴스밸런스 (NewsBalance) — by 성준

> **Weekly Media Literacy & Bias Report (9-12주차 통합 포트폴리오)**  
> 뉴스 기사의 정치적 편향(진보·중도·보수)을 실시간 머신러닝으로 분석하고, 최신 Gemini 2.5 Flash를 활용하여 팩트 기반의 미디어 리터러시 다각적 리포트를 생성하는 웹 서비스 및 랜딩 페이지 저장소입니다.

* **실시간 데모 및 랜딩 페이지**: [http://100.27.218.145:8000/](http://100.27.218.145:8000/) (Nginx를 통해 http://100.27.218.145/ 로도 프록시 접합됨)

---

## 📁 주요 폴더 및 파일 구조

```text
├── .github/workflows/
│   └── deploy.yml              # GitHub Actions를 통한 AWS EC2 자동 배포 워크플로우 (Node.js 24 런타임 강제)
├── static/                     # 웹 대시보드(데모) 프론트엔드 정적 파일
│   ├── index.html              # 실시간 뉴스피드, 편향 그래프, AI 코멘터리 및 챗봇 UI
│   ├── app.js                  # API 연동, 모달 레이아웃 제어, 이미지 Fallback 및 UI 비동기 로직
│   └── style.css               # 다크 테마 글래스모피즘(Glassmorphism) 스타일시트
├── index.html                  # 단일 칼럼 옛날 신문 양식의 프로젝트 설명 랜딩 페이지
├── style.css                   # 랜딩 페이지 전용 CSS (상단 고정 네비게이션, 반응형 단일 칼럼 최적화)
├── contents.md                 # 랜딩 페이지 내에 동적으로 바인딩(zero-md)되는 프로젝트 요약서
├── app.py                      # FastAPI 백엔드 서버 (API 라우팅 및 static 서빙)
├── debate_engine.py            # Gemini 2.5 Flash API 호출 및 프롬프트 제어 (이중 API 키 자동 로테이션 설계)
├── naver_news.py               # 네이버 뉴스 OpenAPI 수집 및 스레드풀 기반 기사 og:image 병렬 크롤러
├── ml_classifier.py            # 머신러닝 모델 학습 스크립트 (TF-IDF + LDA + Logistic Regression)
├── bias_model.pkl              # 학습이 완료된 245KB 초경량 직렬화 모델 파일
├── Dockerfile                  # FastAPI 앱 컨테이너화를 위한 Docker 명세
├── docker-compose.yml          # FastAPI와 Nginx 리버스 프록시를 묶어주는 Docker Compose 명세
├── nginx.conf                  # Nginx 프록시 라우팅 및 80포트 바인딩 설정
└── requirements.txt            # Python 의존성 파일
```

---

## 🤖 핵심 기술 명세 및 시스템 아키텍처

### 1. 실시간 뉴스 수집 & 병렬 썸네일 크롤링 (`naver_news.py`)
- **이슈 정렬**: 네이버 뉴스 API에서 연관성 및 화제성 순인 `sort=sim` 유사도 기준으로 실시간 기사를 포괄적으로 수집합니다.
- **병렬 크롤러**: 네이버 API가 미제공하는 기사 대표 이미지를 획득하기 위해 `ThreadPoolExecutor`(10개 워커 스레드)를 가동하여 원문 HTML의 Open Graph 태그(`og:image`)를 2.5초 내외로 병렬 크롤링합니다.

### 2. 정치 편향성 ML 분석 파이프라인 (`ml_classifier.py`)
- 26개 언론사의 대량 뉴스 기사 데이터를 토대로 학습된 **TF-IDF + LDA(Latent Dirichlet Allocation) + Logistic Regression(L2 규제)** 파이프라인입니다.
- **245KB 초경량 모델 (`bias_model.pkl`)**로 경량화하여 추론 시 CPU 점유를 최소화하고 1ms 내외로 진보·중도·보수 확률(%)을 계산합니다.

### 3. 사실 기반의 고성능 AI 리포트 (`debate_engine.py`)
- Gemini 2.5 Flash API를 활용하여 4대 항목(편향 포인트 지적, 타 성향 비교, 팩트체크, 매체별 강조 차이) 분석을 제공합니다.
- **속도 향상**: 1~2문장 내외로 응답 길이를 간결하게 제한하여 API 대기 시간을 최소화했습니다.
- **문체 규제**: 모호하고 추측성 짙은 어조(`할 것입니다`, `보입니다`)를 완전히 금지하고, 기사 내 실제 수치나 팩트를 근거로 한 단정적 어조(`합니다`, `입니다`, `확인됩니다`)를 적용합니다.
- **이중 API 키 Fallback**: 기본 API 키에 에러 또는 사용량 초과(Quota Exceeded)가 발생할 경우, 환경 변수로 지정된 백업 키(`GEMINI_API_KEY_BACKUP`)로 **즉시 자동 전환하여 2차 요청을 수행**하는 무중단 시스템이 적용되어 있습니다.

---

## 🚀 배포 가이드 (AWS EC2 + Docker)

### 1. EC2 인프라 및 보안 그룹 준비
- Ubuntu Server 22.04 LTS 가상 서버를 가동하고 탄력적 IP(Elastic IP)를 할당하여 고정합니다.
- 인바운드 보안 그룹에서 포트 `80` (HTTP), `443` (HTTPS), `22` (SSH), `5678` (n8n 자동화 서버용)을 오픈합니다.

### 2. 서버 초기 설정 및 패키지 설치 (SSH 접속)
```bash
# swap 메모리 2GB 추가 (빌드 시 메모리 부족 방지)
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Docker 설치
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
```

### 3. 소스 클론 및 환경 설정
```bash
cd ~
git clone https://github.com/sjunc/aiweb.git newsbalance
cd newsbalance

# .env 파일 생성 및 자격 증명 입력
cat <<EOF > .env
GEMINI_API_KEY=YOUR_PRIMARY_GEMINI_KEY
GEMINI_API_KEY_BACKUP=YOUR_BACKUP_GEMINI_KEY
NAVER_CLIENT_ID=YOUR_NAVER_CLIENT_ID
NAVER_CLIENT_SECRET=YOUR_NAVER_CLIENT_SECRET
EOF
```

### 4. Docker Compose 서비스 실행
```bash
docker compose up -d --build
```
- 실행 이후, Nginx 프록시를 통해 브라우저에서 `http://<EC2_IP>` 또는 매핑된 도메인 접속 시 웹 서비스가 가동됩니다.
