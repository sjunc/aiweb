"""
ml_classifier.py — 언론사별 어휘/표현/주제 학습 기반 정치 성향 분류기

동작 방식:
  1. MEDIA_DB(26개 언론사)의 기사를 수집하여 학습 데이터 구축
  2. 어휘(TF-IDF), 표현(n-gram), 주제(LDA), 감정 분석 특징 추출
  3. 5단계(극좌/좌/중도/우/극우) 분류 모델 학습
  4. 학습된 모델로 임의의 새 기사 분류 (5단계 + 3단계 %)

사용 예:
    from ml_classifier import BiasClassifier
    clf = BiasClassifier()
    clf.crawl_and_build_dataset()   # 26개 언론사 기사 수집
    clf.train()                      # 특징 추출 + 모델 학습
    clf.save("model.pkl")
    
    result = clf.predict("새 뉴스 기사 제목", "새 뉴스 기사 본문")
    # -> {"stance": "right", "stance_label": "우",
    #     "progressive": 0.15, "centrist": 0.25, "conservative": 0.60}

의존성: scikit-learn, konlpy, requests, beautifulsoup4, numpy
"""

import re
import json
import pickle
import hashlib
from pathlib import Path
from collections import Counter

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import LatentDirichletAllocation
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, FeatureUnion
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score

# ──────────────────────────────────────────────
# 한국어 형태소 분석기 (konlpy)
# ──────────────────────────────────────────────
try:
    from konlpy.tag import Okt
    _okt = Okt()
    HAS_KONLPY = True
except ImportError:
    HAS_KONLPY = False
    _okt = None


# ──────────────────────────────────────────────
# 정치 성향 어휘 사전 (도메인 시드)
# ──────────────────────────────────────────────
PROGRESSIVE_WORDS = {
    "사회적약자", "양극화", "차별", "인권", "공공성", "복지", "불평등", "포용",
    "다양성", "연대", "혐오", "탄압", "검열", "민주주의", "노동", "조세정의",
    "기후위기", "탈원전", "기본소득", "무상교육", "건강보험", "최저임금",
}

CONSERVATIVE_WORDS = {
    "자유시장", "재정건전성", "규제완화", "자율", "전통", "효율성", "경쟁력",
    "국가안보", "법치", "질서", "자유", "책임", "자립", "성장", "기업",
    "감세", "탈규제", "원전", "민영화", "자유무역", "강력한국방",
}

CENTER_WORDS = {
    "균형", "타협", "대화", "중도", "실용", "합의", "조정", "현실적",
    "단계적", "점진적", "중립", "조율", "절충",
}

POLAR_VERBS = {"비판", "규탄", "반대", "지지", "촉구", "요구", "환영", "반발"}


# ──────────────────────────────────────────────
# 감정 사전 (단순 극성 점수)
# ──────────────────────────────────────────────
POSITIVE_LEXICON = {
    "긍정", "희망", "낙관", "기대", "개선", "성과", "발전", "회복",
    "협력", "합의", "성장", "혁신", "자랑", "선진",
}
NEGATIVE_LEXICON = {
    "부정", "위기", "실패", "우려", "악화", "붕괴", "침체", "갈등",
    "대립", "혼란", "불안", "좌초", "파국", "퇴행", "반대",
}


class TextPreprocessor:
    """한국어 뉴스 기사 텍스트 전처리"""

    @staticmethod
    def clean(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"<[^>]+>", "", text)          # HTML 제거
        text = re.sub(r"[^\w\s]", " ", text)          # 특수문자 제거
        text = re.sub(r"\s+", " ", text).strip()      # 연속 공백 제거
        return text.lower()

    @staticmethod
    def tokenize(text: str) -> list[str]:
        """konlpy 형태소 분석 (fallback: 단순 어절 분리)"""
        cleaned = TextPreprocessor.clean(text)
        if HAS_KONLPY:
            try:
                return _okt.nouns(cleaned) + _okt.morphs(cleaned)
            except Exception:
                pass
        return cleaned.split()  # fallback

    @staticmethod
    def extract_keywords(tokens: list[str]) -> dict:
        """어휘 사전 기반 정치 키워드 카운트"""
        tokens_set = set(tokens)
        return {
            "prog_score": len(tokens_set & PROGRESSIVE_WORDS),
            "cons_score": len(tokens_set & CONSERVATIVE_WORDS),
            "cent_score": len(tokens_set & CENTER_WORDS),
            "polar_verbs": len(tokens_set & POLAR_VERBS),
        }

    @staticmethod
    def sentiment(tokens: list[str]) -> dict:
        """긍정/부정 어휘 비율"""
        tokens_set = set(tokens)
        pos = len(tokens_set & POSITIVE_LEXICON)
        neg = len(tokens_set & NEGATIVE_LEXICON)
        total = pos + neg or 1
        return {"pos_ratio": pos / total, "neg_ratio": neg / total}


class BiasClassifier:
    """
    5단계 정치 성향 분류기
    
    - craw: 네이버/직접 수집 → 학습 데이터셋 구축
    - train: TF-IDF + LDA + 특징 공학 → LogisticRegression 학습
    - predict: 새 기사 → 5단계 분류 + 3단계 확률
    - save/load: 모델 영속화
    """

    STANCE_MAP = {
        "far_left": 0, "left": 1, "center": 2, "right": 3, "far_right": 4,
    }
    STANCE_REVERSE = {v: k for k, v in STANCE_MAP.items()}
    STANCE_LABEL = {
        "far_left": "극좌", "left": "좌", "center": "중도",
        "right": "우", "far_right": "극우",
    }

    def __init__(self, model_path: str = "bias_model.pkl"):
        self.model_path = model_path
        self.pipeline: Pipeline | None = None
        self.tfidf: TfidfVectorizer | None = None
        self.lda: LatentDirichletAllocation | None = None
        self._preprocessor = TextPreprocessor()

    # ── 1. 데이터 수집 ──────────────────────────

    def crawl_and_build_dataset(self, max_per_outlet: int = 50) -> list[dict]:
        """
        26개 언론사별 기사 수집 → 레이블링된 데이터셋 반환
        
        각 항목: {"text": "기사 제목 + 본문", "stance": "left", "press": "한겨레"}
        
        TODO: 
        - 각 언론사 RSS 또는 사이트별 크롤러 구현 필요
        - 현재는 스켈레톤; naver_news.py의 fetch_trending_news()를 
          확장하여 특정 언론사 도메인 필터링 후 다량 수집
        """
        from naver_news import MEDIA_DB, NEWS_CATEGORIES, classify_media, clean_html
        import requests
        import os
        from dotenv import load_dotenv

        load_dotenv()
        client_id = os.environ.get("NAVER_CLIENT_ID", "")
        client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")

        dataset = []
        headers = {
            "User-Agent": "Mozilla/5.0",
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret
        }

        for stance, outlets in MEDIA_DB.items():
            collected = 0
            for cat in NEWS_CATEGORIES:
                if collected >= max_per_outlet:
                    break
                try:
                    resp = requests.get(
                        "https://openapi.naver.com/v1/search/news.json",
                        headers=headers,
                        params={"query": f"{cat} {list(outlets.values())[0]}",
                                "display": 20, "sort": "date"},
                        timeout=8,
                    )
                    if resp.status_code != 200:
                        continue
                    for item in resp.json().get("items", []):
                        link = item.get("originallink") or item.get("link")
                        s, press = classify_media(link)
                        if s != stance or press not in outlets.values():
                            continue
                        text = clean_html(item.get("title", "")) + " " + \
                               clean_html(item.get("description", ""))
                        dataset.append({
                            "text": text,
                            "stance": s,
                            "press": press,
                            "category": cat,
                        })
                        collected += 1
                        if collected >= max_per_outlet:
                            break
                except requests.RequestException:
                    continue

        return dataset

    # ── 2. 특징 추출 ──────────────────────────

    def _extract_features(self, texts: list[str]) -> np.ndarray:
        """
        각 텍스트에 대해 수동 특징 벡터 생성:
        - 정치 키워드 점수 (3종)
        - 감정 극성 비율 (2종)
        - 형태소 다양성
        - 텍스트 길이
        """
        features = []
        for t in texts:
            tokens = self._preprocessor.tokenize(t)
            kw = self._preprocessor.extract_keywords(tokens)
            sent = self._preprocessor.sentiment(tokens)
            feats = [
                kw["prog_score"],
                kw["cons_score"],
                kw["cent_score"],
                kw["polar_verbs"],
                sent["pos_ratio"],
                sent["neg_ratio"],
                len(set(tokens)) / (len(tokens) + 1),  # 형태소 다양성
                min(len(t), 500) / 500,                 # 정규화된 길이
            ]
            features.append(feats)
        return np.array(features)

    # ── 3. 학습 ──────────────────────────────

    def train(self, dataset: list[dict] | None = None,
              save_after: bool = True) -> dict:
        """
        전체 파이프라인 학습
        
        1. TF-IDF 벡터화 (1-gram ~ 3-gram, 최대 5000 특징)
        2. LDA 토픽 모델링 (10개 토픽)
        3. 수동 특징 (키워드/감정/형태소)
        4. LogisticRegression Multi-class 분류
        
        Returns: {"accuracy": ..., "f1_macro": ..., "report": ...}
        """
        from sklearn.metrics import classification_report

        if dataset is None:
            dataset = self.crawl_and_build_dataset()

        texts = [d["text"] for d in dataset]
        labels = [self.STANCE_MAP[d["stance"]] for d in dataset]

        # TF-IDF
        self.tfidf = TfidfVectorizer(
            max_features=5000, ngram_range=(1, 3),
            min_df=2, max_df=0.8, sublinear_tf=True,
        )
        tfidf_matrix = self.tfidf.fit_transform(texts)

        # LDA
        self.lda = LatentDirichletAllocation(
            n_components=10, random_state=42, max_iter=50,
        )
        lda_features = self.lda.fit_transform(tfidf_matrix)

        # 수동 특징
        manual_features = self._extract_features(texts)

        # 특징 결합
        X = np.hstack([
            tfidf_matrix.toarray(),
            lda_features,
            manual_features,
        ])

        # 분류기 학습
        self.pipeline = LogisticRegression(
            solver="lbfgs",
            max_iter=1000, C=1.0, random_state=42,
        )
        self.pipeline.fit(X, labels)

        # 평가
        preds = self.pipeline.predict(X)
        acc = (preds == np.array(labels)).mean()
        report = classification_report(labels, preds,
                                       labels=list(self.STANCE_MAP.values()),
                                       target_names=list(self.STANCE_MAP.keys()),
                                       output_dict=True)

        result = {
            "accuracy": acc,
            "f1_macro": report.get("macro avg", {}).get("f1-score", 0),
            "report": report,
            "dataset_size": len(dataset),
        }

        if save_after:
            self.save()
        return result

    # ── 4. 예측 ──────────────────────────────

    def predict(self, title: str, description: str = "") -> dict:
        """
        새 기사에 대한 5단계 분류 + 3단계 확률
        
        Returns:
            {"stance": "right",
             "stance_label": "우",
             "progressive": 0.15,
             "centrist": 0.25,
             "conservative": 0.60,
             "confidence": 0.72}
        """
        if self.pipeline is None:
            raise RuntimeError("모델이 학습되지 않았습니다. train() 또는 load()를 먼저 호출하세요.")

        text = title + " " + description

        # TF-IDF
        tfidf_matrix = self.tfidf.transform([text])

        # LDA
        lda_features = self.lda.transform(tfidf_matrix)

        # 수동 특징
        manual_features = self._extract_features([text])

        # 결합
        X = np.hstack([tfidf_matrix.toarray(), lda_features, manual_features])

        # 예측
        label = self.pipeline.predict(X)[0]
        raw_probs = self.pipeline.predict_proba(X)[0]
        
        # 5개 클래스에 대한 확률 배열 생성 (학습 데이터 부족으로 특정 클래스가 빠졌을 때 대비)
        probs = np.zeros(5)
        for idx, cls in enumerate(self.pipeline.classes_):
            probs[cls] = raw_probs[idx]

        stance_key = self.STANCE_REVERSE[label]

        # 3단계 %로 변환
        progressive = probs[0] + probs[1]  # far_left + left
        centrist = probs[2]                 # center
        conservative = probs[3] + probs[4]  # right + far_right
        total = progressive + centrist + conservative or 1

        return {
            "stance": stance_key,
            "stance_label": self.STANCE_LABEL[stance_key],
            "progressive": round(progressive / total * 100, 1),
            "centrist": round(centrist / total * 100, 1),
            "conservative": round(conservative / total * 100, 1),
            "confidence": round(float(probs.max()), 3),
        }

    def predict_batch(self, articles: list[dict]) -> list[dict]:
        """여러 기사 일괄 예측"""
        return [self.predict(a.get("title", ""), a.get("description", ""))
                for a in articles]

    # ── 5. 영속화 ─────────────────────────────

    def save(self, path: str | None = None):
        """모델을 pickle 파일로 저장"""
        p = path or self.model_path
        with open(p, "wb") as f:
            pickle.dump({
                "pipeline": self.pipeline,
                "tfidf": self.tfidf,
                "lda": self.lda,
            }, f)

    def load(self, path: str | None = None) -> bool:
        """저장된 모델 불러오기"""
        p = path or self.model_path
        fpath = Path(p)
        if not fpath.exists():
            return False
        with open(p, "rb") as f:
            data = pickle.load(f)
        self.pipeline = data["pipeline"]
        self.tfidf = data["tfidf"]
        self.lda = data["lda"]
        return True


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
if __name__ == "__main__":
    clf = BiasClassifier()

    if clf.load():
        print("저장된 모델을 불러왔습니다.")
    else:
        print("모델을 발견하지 못했습니다. crawl → train 순서로 실행합니다.")
        print("26개 언론사 기사 수집 중...")
        dataset = clf.crawl_and_build_dataset(max_per_outlet=20)
        print(f"{len(dataset)}개 기사 수집 완료. 학습 시작...")
        result = clf.train(dataset)
        print(f"학습 완료  accuracy={result['accuracy']:.3f}  "
              f"f1_macro={result['f1_macro']:.3f}")

    # 테스트 예측
    test = clf.predict("윤석열 대통령이 오늘 국회에서 시정연설을 했다")
    print(json.dumps(test, ensure_ascii=False, indent=2))
