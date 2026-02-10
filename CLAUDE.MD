# MatchWise: Job Hunting AI Agent

> **개인 프로젝트**: 채용 공고 자동 수집/분석 Multi-Agent 시스템

## 프로젝트 개요

**목적**: 매일 자동으로 채용 공고를 크롤링하고, AI로 분석하여, 나에게 맞는 공고를 Slack으로 알림받는 시스템

**핵심 가치**:
- 실제로 매일 사용 가능한 프로덕션급 시스템
- 초저비용 운영
- 포트폴리오용 Multi-Agent 아키텍처
- LangGraph 기반 자율 Agent 구현

## 기술 스택

### 인프라 (AWS)
- **컴퓨팅**: ECS Fargate Spot (크롤러), Lambda (분석/알림)
- **Vector DB**: Pinecone Free Tier
- **상태 관리**: DynamoDB Free Tier
- **파일 저장**: S3 Free Tier
- **스케줄링**: EventBridge
- **모니터링**: CloudWatch + Sentry Free
- **IaC**: Terraform
- **CI/CD**: GitHub Actions

### AI/ML
- **LLM**: 
  - GPT-4o-mini (OCR + 텍스트 분석)
  - text-embedding-3-small (임베딩)
- **프레임워크**: LangChain 0.1.x, LangGraph
- **Vector DB Client**: Pinecone Python SDK

### 개발
- **언어**: Python 3.12
- **백엔드**: FastAPI + Mangum (Lambda adapter)
- **크롤링**: Playwright
- **PDF 처리**: reportlab, pypdf
- **알림**: Slack Bolt

## 시스템 아키텍처

```
                    ┌──────────────────────────┐
                    │   EventBridge (매일)      │
                    │   02:00 / 05:00 / 09:00  │
                    └────────┬─────────────────┘
                             │
                    ┌────────▼─────────────────┐
                    │   Lambda (Orchestrator)  │
                    │   Main Agent Graph       │
                    └────────┬─────────────────┘
                             │
        ┌────────────────────┼────────────────────┬─────────────┐
        │                    │                    │             │
┌───────▼────────┐  ┌────────▼────────┐  ┌──────▼────────┐  ┌──▼─────────┐
│ Crawling       │  │ Data Pipeline   │  │ Analysis      │  │ Preference │
│ Agent          │  │ Agent           │  │ Agent         │  │ Learning   │
│ (ECS Fargate)  │  │ (Lambda)        │  │ (Lambda)      │  │ Agent      │
│                │  │                 │  │               │  │ (Lambda)   │
│ Tools:         │  │ Tools:          │  │ Tools:        │  │ Tools:     │
│ - crawl_site   │  │ - save_s3       │  │ - match       │  │ - learn    │
│ - ocr_extract  │  │ - embed         │  │ - gap_analysis│  │ - update   │
└────────────────┘  └─────────────────┘  └───────────────┘  └────────────┘

┌────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ Company        │  │ Research        │  │ Notification    │
│ Intelligence   │  │ Agent           │  │ Agent           │
│ Agent          │  │ (Lambda)        │  │ (Lambda)        │
│ (Lambda)       │  │                 │  │                 │
│ Tools:         │  │ Tools:          │  │ Tools:          │
│ - tech_blog    │  │ - web_search    │  │ - analyze_load  │
│ - reviews      │  │ - trends        │  │ - send_slack    │
└────────────────┘  └─────────────────┘  └─────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
┌───────▼────┐      ┌────────▼────────┐   ┌──────▼──────┐
│  Pinecone  │      │   DynamoDB      │   │    Slack    │
│ (Vector DB)│      │ (상태/선호도)     │   │   (알림)    │
└────────────┘      └─────────────────┘   └─────────────┘
        │
┌───────▼────┐
│     S3     │
│ (PDF/이미지)│
└────────────┘
```

## Multi-Agent 구조

| Agent | 역할 | 주요 Tools |
|-------|------|-----------|
| **Orchestrator** | 전체 워크플로우 관리 | assess_situation, create_plan |
| **Crawling** | 채용 사이트 크롤링 | crawl_site, ocr_extract |
| **Data Pipeline** | 데이터 저장/인덱싱 | save_s3, embed, vector_db |
| **Analysis** | 공고 적합도 분석 | match_score, gap_analysis |
| **Preference Learning** | 피드백 학습 | learn_feedback, update_weights |
| **Company Intelligence** | 회사 심층 분석 | tech_blog, reviews, salary |
| **Research** | 시장/트렌드 리서치 | web_search, track_trends |
| **Notification** | 알림 전략 결정 | analyze_workload, send_slack |

## 데이터 플로우

### 1. 크롤링 (매일 02:00 KST)
```
EventBridge → ECS Task → Playwright 크롤링 → OCR/파싱 → S3 + DynamoDB
```

### 2. 분석 (매일 05:00 KST)
```
EventBridge → Lambda → DynamoDB 조회 → GPT-4o-mini 분석 → 
임베딩 생성 → Pinecone 저장 → 적합도 계산
```

### 3. 브리핑 (매일 09:00 KST)
```
EventBridge → Lambda → 마감임박/고적합도 공고 조회 → 
GPT-4o-mini 브리핑 생성 → Slack 전송
```

### 4. 피드백 학습 (실시간)
```
Slack 버튼 클릭 → API Gateway → Lambda → 
Preference Agent 학습 → Vector DB 업데이트
```


## 코딩 규칙

### Python Style
```python
# Type hints 필수
async def analyze_job(job: dict) -> AnalysisResult:
    """
    공고를 분석하고 적합도를 계산합니다.
    
    Args:
        job: 공고 데이터 (job_id, text, company 포함)
        
    Returns:
        AnalysisResult: 분석 결과 (match_score, gaps 등)
    """
    pass

# Docstring은 Google Style
# Error 처리는 try-except 명시적으로
# 환경변수는 .env 파일 사용
```

### 파일 네이밍
- Python: `snake_case.py`
- Class: `PascalCase`
- Function/Variable: `snake_case`
- Constants: `UPPER_SNAKE_CASE`

### 커밋 메시지
```
feat: Add Crawling Agent
fix: Fix OCR encoding issue
docs: Update README
refactor: Improve error handling
test: Add unit tests for Analysis Agent
```

## 로컬 개발 환경

### 필수 도구
- Python 3.12
- Docker & Docker Compose
- AWS CLI
- Terraform 1.6.x



## 프로젝트 구조 핵심

```
matchwise/
├── agents/              # 각 Agent 구현
│   ├── orchestrator.py
│   ├── crawling_agent.py
│   ├── analysis_agent.py
│   └── main_graph.py    # LangGraph 정의
├── tools/               # Agent Tools
├── storage/             # DB 클라이언트
├── shared/              # 공통 모듈
│   ├── config.py
│   ├── models.py
│   └── prompts/
└── infrastructure/      # Terraform
```

## 개발 우선순위

### Phase 1: 기초 (1주)
- [ ] 환경 설정 (Docker, AWS, API 키)
- [ ] Crawling Agent 개발
- [ ] 로컬 테스트

### Phase 2: AI 통합 (1주)
- [ ] Pinecone 연동
- [ ] LangChain Agent 설정
- [ ] 분석 파이프라인

### Phase 3: AWS 배포 (1주)
- [ ] Terraform 인프라
- [ ] Lambda 배포
- [ ] Slack Bot 연동

### Phase 4: 안정화 (1주)
- [ ] 모니터링 설정
- [ ] 에러 처리
- [ ] 문서화

## 알려진 제약사항

1. **Lambda 15분 제한**: 복잡한 크롤링은 ECS 사용
2. **Pinecone Free Tier**: 100K vectors 제한 
3. **이미지 OCR 비용**: 이미지 공고가 많으면 비용 증가
4. **Playwright 메모리**: Lambda는 1GB+ 메모리 필요


## 트러블슈팅

### 크롤링 실패
```bash
# Playwright 브라우저 재설치
playwright install --force chromium

# Docker 이미지 재빌드
docker-compose down
docker-compose build --no-cache
```

### Lambda 타임아웃
```python
# Lambda 메모리/타임아웃 증가
# infrastructure/modules/lambda/main.tf
resource "aws_lambda_function" "analyzer" {
  memory_size = 2048  # 1024 → 2048
  timeout     = 300   # 60 → 300
}
```

### Vector DB 연결 실패
```python
# Pinecone 연결 확인
from pinecone import Pinecone
pc = Pinecone(api_key="your-key")
print(pc.list_indexes())
```

## 참고 자료

- **LangChain Docs**: https://python.langchain.com/docs/
- **LangGraph Tutorial**: https://langchain-ai.github.io/langgraph/
- **Pinecone Docs**: https://docs.pinecone.io/
- **Playwright Docs**: https://playwright.dev/python/
- **AWS Lambda Python**: https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html
