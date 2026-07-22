> ⚠️ **ARCHIVED 2026-07-13** — v1 설계 문서(2025-02 작성). AWS Lambda/ECS + DynamoDB + Pinecone + Slack 기반 멀티에이전트 구상으로, 코드로 구현되지 않았음.
> 기능 아이디어의 원형(지원 상태 추적, 적응형 알림, RAG 프로필 등)으로 보존한다. 현재 유효한 설계는 저장소 루트의 [DESIGN.md](../../DESIGN.md) (v3).

# MatchWise: Job Hunting AI Agent

> **개인 프로젝트**: 채용 공고 자동 수집/분석 Multi-Agent 시스템 with 개인화 학습 및 지원 관리

## 프로젝트 개요

**목적**: 매일 자동으로 채용 공고를 크롤링하고, AI로 분석하여, 나에게 맞는 공고를 Slack으로 알림받으며, 지원 과정을 체계적으로 관리하는 시스템

**핵심 가치**:
- 실제로 매일 사용 가능한 프로덕션급 시스템
- 초저비용 운영
- 포트폴리오용 Multi-Agent 아키텍처
- LangGraph 기반 자율 Agent 구현
- **개인화된 학습 및 적응형 알림**
- **전방위 지원 프로세스 관리**

## 핵심 기능

### 1. 스마트 채용 공고 수집 및 매칭
- ✅ **자동 크롤링**: 주요 채용 사이트에서 매일 새로운 공고 수집
- ✅ **이력 기반 매칭**: 사용자 이력과 JD를 비교하여 적합도 점수 계산
- ✅ **주기적 리스트업**: 매칭도 높은 공고를 정기적으로 Slack 알림

### 2. 상세 Gap 분석 및 액션 가이드
- ✅ **심층 분석**: 사용자가 관심 공고 요청 시 상세 분석 제공
  - 매칭도 높은 이유 설명
  - JD와 사용자 이력의 Gap 분석
  - 이력서 작성 시 강조할 포인트 제안
  - 역량 보완을 위한 학습/프로젝트 가이드

### 3. 적응형 알림 시스템
- ✅ **상태 기반 알림 주기 조절**
  - 이력서 작성 중: 알림 주기 감소 (주 1-2회)
  - 면접 준비 중: 관련 공고만 필터링
  - 코딩테스트/과제 진행 중: 알림 일시 정지
  - 평상시: 일일 또는 격일 알림
- ✅ **사용자 활동 패턴 학습**: 피드백 기록으로 최적 알림 시간 학습

### 4. 개인화 선호도 학습
- ✅ **피드백 기반 프로필 업데이트**
  - "이런 공고 별로" → 부정 패턴 학습 (Few-shot 예시 추가)
  - "이런 공고 좋아" → 선호 패턴 강화
  - RAG 방식으로 사용자 프로필 문서 실시간 업데이트
- ✅ **자동 필터링**: 학습된 선호도로 공고 자동 필터링

### 5. 지원 상태 추적 및 관리
- ✅ **상태 기록**: 지원 의사 표시 시 DynamoDB에 상태 저장
  - 지원 예정 → 지원 완료 → 서류 통과 → 면접 → 최종 결과
- ✅ **주기적 리마인더**: 지원한 공고의 마감일, 예상 결과 발표일 추적
- ✅ **투두리스트 제안**: 각 단계별 해야 할 일 자동 제안
  - 서류 준비, 포트폴리오 업데이트, 면접 준비 등

### 6. 기업 정보 자동 수집
- ✅ **기본 정보 크롤링**
  - 회사 설립일, 규모, 매출/수익구조
  - 투자 라운드, 주요 투자자
  - 인재상, 복리후생
- ✅ **기술 정보 수집**
  - 기술 스택 (채용 공고, 기술 블로그 분석)
  - 기술 블로그 주요 포스트 요약
  - 현직자 리뷰 (Blind, 잡플래닛 등)
  - GitHub organization 분석 (있을 경우)

### 7. 스마트 진행 상황 관리
- ✅ **자동 리와인드**: 지원 공고 마감일 기준으로 리마인더
  - D-7: 첫 알림
  - D-3: 진행 상황 체크
  - D-1: 최종 확인
- ✅ **진행 상황 대화형 체크**: Slack으로 현재 상태 질문
- ✅ **단계별 팁 제공**: 각 단계에 맞는 실용적인 조언 제공
  - 이력서 작성 팁, 포트폴리오 구성 조언
  - 면접 예상 질문, 기술 면접 준비 자료
  - 코딩테스트 유형별 학습 자료

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
  - GPT-4o-mini (OCR + 텍스트 분석 + Gap 분석)
  - text-embedding-3-small (임베딩)
- **프레임워크**: LangChain 0.1.x, LangGraph
- **Vector DB Client**: Pinecone Python SDK
- **Retrieval**: RAG for 사용자 프로필 및 선호도

### 개발
- **언어**: Python 3.12
- **백엔드**: FastAPI + Mangum (Lambda adapter)
- **크롤링**: Playwright + BeautifulSoup4
- **PDF 처리**: reportlab, pypdf
- **알림**: Slack Bolt (Interactive Components)

## 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                    EventBridge Scheduler                        │
│  - 크롤링: 매일 02:00 KST                                        │
│  - 분석: 매일 05:00 KST                                          │
│  - 알림: 동적 조정 (사용자 상태 기반)                              │
│  - 리와인드: 매일 10:00 KST (마감일 체크)                          │
└────────────────────────┬────────────────────────────────────────┘
                         │
                ┌────────▼──────────────────┐
                │  Lambda (Orchestrator)    │
                │  Main Agent Graph         │
                │  - 상황 분석               │
                │  - 워크플로우 라우팅         │
                └────────┬──────────────────┘
                         │
     ┌───────────────────┼───────────────────┬──────────────┐
     │                   │                   │              │
┌────▼─────────┐  ┌──────▼────────┐  ┌──────▼───────┐  ┌──▼──────────┐
│ Crawling     │  │ Data Pipeline │  │ Analysis     │  │ Preference  │
│ Agent        │  │ Agent         │  │ Agent        │  │ Learning    │
│ (ECS Fargate)│  │ (Lambda)      │  │ (Lambda)     │  │ Agent       │
│              │  │               │  │              │  │ (Lambda)    │
│ Tools:       │  │ Tools:        │  │ Tools:       │  │ Tools:      │
│ - crawl_site │  │ - save_s3     │  │ - match      │  │ - learn     │
│ - ocr_extract│  │ - embed       │  │ - gap_detail │  │ - update    │
│              │  │ - dedupe      │  │ - resume_tip │  │ - profile   │
└──────────────┘  └───────────────┘  └──────────────┘  └─────────────┘

┌────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ Company        │  │ Application      │  │ Notification     │
│ Intelligence   │  │ Manager Agent    │  │ Agent            │
│ Agent          │  │ (Lambda)         │  │ (Lambda)         │
│ (Lambda)       │  │                  │  │                  │
│ Tools:         │  │ Tools:           │  │ Tools:           │
│ - basic_info   │  │ - track_status   │  │ - check_state    │
│ - tech_stack   │  │ - remind         │  │ - adjust_freq    │
│ - tech_blog    │  │ - todo_suggest   │  │ - send_slack     │
│ - reviews      │  │ - deadline_check │  │                  │
└────────────────┘  └──────────────────┘  └──────────────────┘
         │                   │                      │
         └───────────────────┼──────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
┌───────▼────┐      ┌────────▼────────┐   ┌──────▼──────┐
│  Pinecone  │      │   DynamoDB      │   │    Slack    │
│ (Vector DB)│      │  - 공고 상태     │   │  (Interactive)│
│ - JD 임베딩 │      │  - 지원 현황     │   │  - 알림      │
│ - 이력 임베딩│      │  - 사용자 상태   │   │  - 피드백    │
│ - 선호도    │      │  - 선호도 히스토리│   │  - 상호작용  │
└────────────┘      └─────────────────┘   └─────────────┘
        │
┌───────▼────┐
│     S3     │
│ - PDF/이미지│
│ - 사용자    │
│   프로필    │
└────────────┘
```

## Multi-Agent 구조 (확장)

| Agent | 역할 | 주요 Tools | 새로 추가된 기능 |
|-------|------|-----------|-----------------|
| **Orchestrator** | 전체 워크플로우 관리 | assess_situation, create_plan, route_task | 사용자 상태 기반 라우팅 |
| **Crawling** | 채용 사이트 크롤링 | crawl_site, ocr_extract, dedupe | 중복 제거, 증분 업데이트 |
| **Data Pipeline** | 데이터 저장/인덱싱 | save_s3, embed, vector_db | 이력 임베딩 생성 |
| **Analysis** | 공고 적합도 분석 | match_score, gap_analysis, gap_detail, resume_tip | 상세 Gap 분석, 이력서 팁 |
| **Preference Learning** | 피드백 학습 | learn_feedback, update_weights, update_profile | RAG 기반 프로필 업데이트 |
| **Company Intelligence** | 회사 심층 분석 | basic_info, tech_stack, tech_blog, reviews, github_org | 기술 스택 + 현직자 리뷰 |
| **Application Manager** | 지원 상태 관리 | track_status, remind, todo_suggest, deadline_check | **NEW**: 지원 추적 시스템 |
| **Notification** | 알림 전략 결정 | check_state, adjust_frequency, send_slack | 상태 기반 알림 조절 |

## 데이터 플로우 (확장)

### 1. 크롤링 (매일 02:00 KST)
```
EventBridge → ECS Task → Playwright 크롤링 → OCR/파싱 → 
중복 체크 (DynamoDB) → S3 + DynamoDB (새 공고만)
```

### 2. 분석 및 매칭 (매일 05:00 KST)
```
EventBridge → Lambda → DynamoDB 조회 (새 공고) → 
사용자 이력 로드 (S3) → GPT-4o-mini 분석 → 
임베딩 생성 (JD + 이력) → Pinecone 저장 → 
코사인 유사도 계산 → 매칭 점수 저장 (DynamoDB)
```

### 3. 적응형 브리핑 (동적 스케줄)
```
EventBridge → Lambda → 사용자 상태 체크 (DynamoDB) →
[상태별 분기]
  - 평상시: 고적합도(80%+) 공고 일일 알림
  - 이력서 작성 중: 주 1회 요약 알림
  - 면접 준비 중: 해당 기업 관련 공고만
  - 코테/과제 중: 알림 일시 정지
→ GPT-4o-mini 브리핑 생성 → Slack 전송
```

### 4. 피드백 학습 (실시간)
```
Slack 버튼 클릭 → API Gateway → Lambda → 
[피드백 유형별 처리]
  - "관심 있음": 긍정 패턴 학습 → Vector DB 가중치 조정
  - "별로임": 부정 패턴 학습 → 필터 규칙 업데이트
  - "이런 거 그만": Few-shot 예시 추가 → S3 프로필 업데이트
→ DynamoDB 히스토리 저장
```

### 5. 상세 분석 요청 (사용자 트리거)
```
Slack 명령어 ("/analyze [job_id]") → API Gateway → Lambda →
Company Intelligence Agent 실행:
  - 기본 정보 크롤링 (회사 홈페이지, Crunchbase)
  - 기술 정보 수집 (기술 블로그, Stack Overflow Jobs)
  - 현직자 리뷰 (Blind, Glassdoor)
→ Analysis Agent 실행:
  - 매칭도 상세 설명
  - Gap 분석 (부족한 스킬, 경험)
  - 이력서 작성 가이드 (강조 포인트, 키워드)
  - 역량 보완 액션 플랜 (학습, 프로젝트)
→ Slack 상세 리포트 전송 (Markdown 형식)
```

### 6. 지원 상태 관리 (사용자 트리거 + 자동)
```
[지원 의사 표시]
Slack 버튼 ("지원하기") → Lambda → DynamoDB 상태 업데이트
  (상태: "지원 예정", 마감일, 예상 결과일 저장)

[자동 리마인더] (매일 10:00 KST)
EventBridge → Lambda → DynamoDB 쿼리 (지원 중인 공고) →
마감일 기준 분기:
  - D-7: "1주일 남았어요" + 투두리스트
  - D-3: "진행 상황 어때요?" (Interactive Message)
  - D-1: "내일 마감!" + 최종 체크리스트
→ Slack 대화형 메시지 전송

[상태 업데이트] (사용자 응답)
Slack 응답 → Lambda → DynamoDB 상태 업데이트 →
다음 단계 투두리스트 제안
```

### 7. 회사 정보 딥다이브 (이력서 작성 선언 시)
```
Slack 명령어 ("/prepare [company_name]") → Lambda →
Company Intelligence Agent 병렬 실행:
  ├─ basic_info: 설립, 규모, 수익, 투자, 인재상
  ├─ tech_stack: JD 분석 + 기술 블로그 파싱
  ├─ tech_blog: 최근 6개월 주요 포스트 요약
  ├─ reviews: Blind/Glassdoor 리뷰 감성 분석
  └─ github_org: (있을 경우) 주요 레포지토리 분석
→ 통합 리포트 생성 (Notion 스타일 Markdown) →
S3 저장 + Slack 공유 (파일 첨부)
```

## 사용자 상태 관리

### 상태 정의 (DynamoDB Schema)
```python
{
  "user_id": "U12345",
  "current_state": "preparing_resume",  # idle, preparing_resume, interviewing, coding_test
  "state_since": "2025-02-01T10:00:00Z",
  "notification_freq": "weekly",  # daily, weekly, paused
  "active_applications": [
    {
      "job_id": "job_001",
      "company": "Company A",
      "position": "Backend Developer",
      "status": "applied",  # interested, applied, screened, interviewing, offered, rejected
      "deadline": "2025-02-20",
      "expected_result": "2025-02-25",
      "todos": [
        {"task": "포트폴리오 업데이트", "done": true},
        {"task": "기술 면접 준비", "done": false}
      ]
    }
  ],
  "preferences": {
    "positive_patterns": [...],
    "negative_patterns": [...],
    "min_match_score": 75
  }
}
```

### 상태 전이 다이어그램
```
idle → preparing_resume → applied → screening → interviewing → [offered/rejected]
  ↑                                                                      ↓
  └──────────────────────────────────────────────────────────────────────┘
                              (cycle)

각 상태별 알림 주기:
- idle: daily (고적합도 공고만)
- preparing_resume: weekly (요약)
- interviewing: 해당 기업 관련만
- coding_test: paused
```

## 사용자 프로필 관리 (RAG)

### 프로필 문서 구조 (S3 저장)
```markdown
# User Profile: [user_id]

## Basic Info
- Name: 홍길동
- Current Role: Backend Developer
- Years of Experience: 3년
- Target Role: Senior Backend Developer
- Target Companies: 스타트업, 테크 기업

## Skills
### Strong
- Python (Django, FastAPI)
- AWS (Lambda, ECS, RDS)
- PostgreSQL, Redis

### Learning
- Kubernetes
- Golang

## Preferences
### Positive Patterns (Few-shot Examples)
- "AWS 기반 백엔드 개발" → 좋아함 (2025-02-01)
- "스타트업, 시리즈 A 이상" → 좋아함 (2025-02-05)

### Negative Patterns (Few-shot Examples)
- "PHP, 레거시 시스템" → 싫어함 (2025-02-03)
- "SI, 대기업 계열사" → 싫어함 (2025-02-07)

## Work History
- 2022-현재: OO 스타트업, Backend Developer
  - 결제 시스템 개발 (Django, PostgreSQL)
  - AWS Lambda 기반 서버리스 아키텍처 설계
  
## Projects
- [포트폴리오 링크]

## Resume Highlights (자동 추출)
- "3년 차 백엔드 개발자"
- "월 100만 트랜잭션 처리"
- "AWS 서버리스 아키텍처 경험"
```

### 프로필 업데이트 로직
```python
async def update_user_profile(
    user_id: str, 
    feedback: dict
) -> None:
    """
    사용자 피드백을 받아 프로필을 Few-shot 방식으로 업데이트
    
    Args:
        user_id: 사용자 ID
        feedback: {"type": "positive/negative", "job_id": "...", "reason": "..."}
    """
    # 1. S3에서 현재 프로필 로드
    profile = await s3_client.get_object(f"profiles/{user_id}.md")
    
    # 2. 해당 공고 내용 조회
    job = await dynamodb_client.get_item("jobs", job_id)
    
    # 3. GPT-4o-mini로 패턴 추출
    pattern = await llm.extract_pattern(job, feedback["reason"])
    # 예: "Python + AWS + 스타트업" → 긍정 패턴
    
    # 4. Few-shot 예시로 프로필에 추가
    if feedback["type"] == "positive":
        profile["preferences"]["positive_patterns"].append({
            "pattern": pattern,
            "example": job["title"],
            "date": datetime.now()
        })
    else:
        profile["preferences"]["negative_patterns"].append({
            "pattern": pattern,
            "example": job["title"],
            "date": datetime.now()
        })
    
    # 5. S3에 업데이트된 프로필 저장
    await s3_client.put_object(f"profiles/{user_id}.md", profile)
    
    # 6. Vector DB 가중치 조정
    await update_vector_weights(user_id, pattern, feedback["type"])
```

## Slack 인터랙션 설계

### 1. 일일 브리핑 메시지
```
📬 오늘의 추천 공고 (2건)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏢 [Company A] Senior Backend Developer
📊 매칭도: 92% | 💰 6000-8000만원 | ⏰ D-5

✅ 강점: AWS 경험, Python/Django, 서버리스
⚠️ Gap: Kubernetes 경험 부족

[관심 있음] [별로임] [상세 분석]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏢 [Company B] Backend Engineer
📊 매칭도: 85% | 💰 5500-7000만원 | ⏰ D-12

✅ 강점: FastAPI, PostgreSQL
⚠️ Gap: 대규모 트래픽 경험

[관심 있음] [별로임] [상세 분석]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 2. 상세 분석 리포트 (Interactive)
```
🔍 [Company A] 상세 분석

━━━ 📊 매칭도 분석 ━━━
✨ 강점 (92% 매칭 이유)
• Python/Django 3년 경력 → JD의 "Python 3년 이상" 부합
• AWS Lambda 경험 → "서버리스 아키텍처 경험자 우대"
• PostgreSQL → "RDBMS 설계 경험"

⚠️ Gap 분석
• Kubernetes: JD 요구 "필수", 당신은 "학습 중"
  → 해결: [Kubernetes 입문 강의] + 개인 프로젝트로 EKS 경험 쌓기
• Golang: JD 우대 "Golang 경험", 당신은 경험 없음
  → 해결: 현재 Python 프로젝트 일부를 Golang으로 리팩토링

━━━ 📝 이력서 작성 팁 ━━━
1. 강조할 키워드: "AWS Lambda", "100만 트랜잭션", "Django"
2. 추가 어필 포인트: "서버리스 마이그레이션으로 비용 40% 절감"
3. 피할 표현: "레거시 시스템 유지보수" (회사는 신규 서비스 개발자 찾음)

━━━ 🏢 회사 정보 ━━━
📌 기본: 시리즈 B, 120명, 핀테크, 연 매출 50억
💻 기술: Python, Go, Kubernetes, AWS, PostgreSQL
📝 블로그: "서버리스로 월 1000만 요청 처리하기" (2024-12)
💬 리뷰: Glassdoor 4.2★ "성장 빠름, 기술 스택 최신"

[이력서 쓸래] [지원하기] [관심 없음]
```

### 3. 지원 관리 메시지
```
📌 지원 현황 업데이트

[Company A] Senior Backend Developer
상태: 서류 접수 완료
마감: D-3 (2025-02-17)

✅ 완료
• 이력서 작성
• 포트폴리오 업데이트

⬜ 해야 할 일
• 기술 면접 예상 질문 정리
• Kubernetes 학습 마무리
• 코딩테스트 연습 (알고리즘)

진행 상황 어때요?
[순조롭게 진행 중] [좀 막혀요] [포기할래요]
```

### 4. 리와인드 메시지
```
⏰ [Company A] 마감 D-1!

내일이 마감이에요. 마지막 체크리스트 확인해보세요!

✅ 최종 체크리스트
□ 이력서 오타 확인
□ 포트폴리오 링크 테스트
□ 자기소개서 최종 검토
□ 첨부 파일 확인 (PDF 용량)

[모두 완료] [좀 더 시간 필요해요]
```

## 코딩 규칙

### Python Style
```python
# Type hints 필수
async def analyze_job(
    job: dict, 
    user_profile: UserProfile
) -> AnalysisResult:
    """
    공고를 분석하고 적합도를 계산합니다.
    
    Args:
        job: 공고 데이터 (job_id, text, company 포함)
        user_profile: 사용자 프로필 (이력, 선호도)
        
    Returns:
        AnalysisResult: 분석 결과 (match_score, gaps, tips)
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
feat: Add Application Manager Agent
feat: Add adaptive notification system
fix: Fix OCR encoding issue
docs: Update README with new features
refactor: Improve error handling in preference learning
test: Add unit tests for Gap Analysis
```

## 로컬 개발 환경

### 필수 도구
- Python 3.12
- Docker & Docker Compose
- AWS CLI
- Terraform 1.6.x

### 환경변수 (.env)
```bash
# AWS
AWS_REGION=ap-northeast-2
AWS_ACCOUNT_ID=123456789012

# OpenAI
OPENAI_API_KEY=sk-...

# Pinecone
PINECONE_API_KEY=...
PINECONE_ENV=us-west1-gcp

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_APP_TOKEN=xapp-...

# User Profile
USER_ID=U12345
USER_PROFILE_S3_BUCKET=matchwise-profiles
```

## 프로젝트 구조 (확장)

```
matchwise/
├── agents/                    # 각 Agent 구현
│   ├── orchestrator.py        # Main orchestrator
│   ├── crawling_agent.py
│   ├── analysis_agent.py
│   ├── preference_agent.py    # NEW: 선호도 학습
│   ├── application_agent.py   # NEW: 지원 관리
│   ├── company_intel_agent.py # NEW: 회사 정보 수집
│   ├── notification_agent.py  # 확장: 적응형 알림
│   └── main_graph.py          # LangGraph 정의
│
├── tools/                     # Agent Tools
│   ├── crawling/
│   │   ├── site_crawler.py
│   │   └── ocr_extractor.py
│   ├── analysis/
│   │   ├── matcher.py
│   │   ├── gap_analyzer.py    # 확장: 상세 분석
│   │   └── resume_advisor.py  # NEW: 이력서 팁
│   ├── profile/
│   │   ├── rag_updater.py     # NEW: RAG 프로필 업데이트
│   │   └── pattern_learner.py # NEW: Few-shot 학습
│   ├── company/
│   │   ├── basic_info.py      # NEW: 기본 정보
│   │   ├── tech_stack.py      # NEW: 기술 스택
│   │   └── review_scraper.py  # NEW: 리뷰 수집
│   └── application/
│       ├── status_tracker.py  # NEW: 상태 추적
│       └── todo_generator.py  # NEW: 투두 생성
│
├── storage/                   # DB 클라이언트
│   ├── dynamodb_client.py
│   ├── s3_client.py
│   └── pinecone_client.py
│
├── shared/                    # 공통 모듈
│   ├── config.py
│   ├── models.py              # 확장: 새 데이터 모델
│   ├── utils.py
│   └── prompts/
│       ├── analysis.py
│       ├── gap_analysis.py    # NEW
│       ├── resume_tips.py     # NEW
│       └── company_research.py # NEW
│
├── api/                       # API 엔드포인트
│   ├── slack_handler.py       # 확장: Interactive Components
│   └── webhook.py
│
├── infrastructure/            # Terraform
│   ├── main.tf
│   ├── modules/
│   │   ├── lambda/
│   │   ├── ecs/
│   │   ├── dynamodb/
│   │   ├── s3/
│   │   └── eventbridge/       # 확장: 동적 스케줄
│   └── variables.tf
│
└── tests/
    ├── unit/
    └── integration/
```

## 개발 우선순위

### Phase 1: 기초 (1주)
- [ ] 환경 설정 (Docker, AWS, API 키)
- [ ] Crawling Agent 개발
- [ ] 로컬 테스트

### Phase 2: AI 통합 (1주)
- [ ] Pinecone 연동
- [ ] LangChain Agent 설정
- [ ] 기본 분석 파이프라인

### Phase 3: 핵심 기능 개발 (2주)
- [ ] Analysis Agent 확장 (Gap 분석, 이력서 팁)
- [ ] Preference Learning Agent 구현 (RAG, Few-shot)
- [ ] Company Intelligence Agent 구현
- [ ] Application Manager Agent 구현
- [ ] 적응형 Notification Agent

### Phase 4: AWS 배포 (1주)
- [ ] Terraform 인프라
- [ ] Lambda 배포
- [ ] Slack Bot 연동 (Interactive Components)
- [ ] EventBridge 동적 스케줄링

### Phase 5: 안정화 및 최적화 (1주)
- [ ] 모니터링 설정
- [ ] 에러 처리
- [ ] 성능 최적화 (비용, 속도)
- [ ] 문서화

## 알려진 제약사항

1. **Lambda 15분 제한**: 복잡한 크롤링은 ECS 사용
2. **Pinecone Free Tier**: 100K vectors 제한
3. **이미지 OCR 비용**: 이미지 공고가 많으면 비용 증가
4. **Playwright 메모리**: Lambda는 1GB+ 메모리 필요
5. **RAG 프로필 크기**: Few-shot 예시가 많아지면 S3 파일 커짐 → 주기적 압축 필요
6. **회사 정보 크롤링**: 일부 사이트 차단/CAPTCHA → Fallback 로직 필요

## 비용 최적화 전략

### 예상 월 비용 (무료 티어 활용)
- **Pinecone**: $0 (Free Tier: 1 index, 100K vectors)
- **DynamoDB**: $0 (Free Tier: 25GB, 25 RCU/WCU)
- **S3**: $0 (Free Tier: 5GB, 20K GET, 2K PUT)
- **Lambda**: ~$5 (Free Tier: 100만 요청/월 초과분)
- **ECS Fargate Spot**: ~$10 (매일 1시간 실행)
- **OpenAI API**: ~$15 (GPT-4o-mini, 매일 50개 공고 분석)
- **CloudWatch**: $0 (Free Tier: 5GB, 10 alarms)

**총 예상 비용**: ~$30/월

### 비용 절감 팁
1. **Spot Instance**: ECS Fargate Spot 사용 (일반 대비 70% 저렴)
2. **Batch Processing**: 공고 분석을 배치로 처리 (Lambda 호출 수 감소)
3. **Cache**: DynamoDB에 분석 결과 캐싱 (중복 분석 방지)
4. **Incremental Crawling**: 신규 공고만 크롤링 (전체 크롤링 X)
5. **Embedding Cache**: 동일 JD는 임베딩 재사용

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

### RAG 프로필 업데이트 느림
```python
# S3 파일 크기 확인
import boto3
s3 = boto3.client('s3')
response = s3.head_object(Bucket='profiles', Key='user123.md')
print(f"File size: {response['ContentLength']} bytes")

# 10MB 초과 시 압축
if response['ContentLength'] > 10 * 1024 * 1024:
    # 오래된 Few-shot 예시 제거 (6개월 이상)
    profile = clean_old_examples(profile, months=6)
```

### Slack Interactive Component 응답 느림
```python
# 3초 내 응답 필수 (Slack 제약)
# 무거운 작업은 별도 Lambda로 비동기 처리
@app.action("detailed_analysis")
async def handle_analysis(ack, body):
    await ack()  # 즉시 응답 (3초 내)
    
    # 무거운 작업은 별도 Lambda 호출 (비동기)
    lambda_client.invoke(
        FunctionName='detailed-analysis-worker',
        InvocationType='Event',  # 비동기
        Payload=json.dumps(body)
    )
```

## 테스트 전략

### Unit Tests
```python
# tests/unit/test_gap_analyzer.py
import pytest
from tools.analysis.gap_analyzer import GapAnalyzer

@pytest.mark.asyncio
async def test_gap_analysis():
    analyzer = GapAnalyzer()
    
    job = {
        "requirements": ["Python", "AWS", "Kubernetes"],
        "experience": "3년 이상"
    }
    
    user_profile = {
        "skills": ["Python", "AWS"],
        "experience": "3년"
    }
    
    result = await analyzer.analyze(job, user_profile)
    
    assert result.gaps == ["Kubernetes"]
    assert result.match_score > 60
    assert "Kubernetes" in result.learning_plan
```

### Integration Tests
```python
# tests/integration/test_end_to_end.py
@pytest.mark.asyncio
async def test_full_workflow():
    # 1. 크롤링
    jobs = await crawler.crawl("wanted.co.kr")
    assert len(jobs) > 0
    
    # 2. 분석
    analysis = await analyzer.analyze(jobs[0])
    assert analysis.match_score > 0
    
    # 3. 알림
    notification = await notifier.send(analysis)
    assert notification.sent == True
```

## 모니터링

### CloudWatch 대시보드
- **크롤링 성공률**: 매일 크롤링된 공고 수, 실패 건수
- **분석 처리 시간**: 공고당 평균 분석 시간
- **매칭도 분포**: 80% 이상, 60-80%, 60% 미만
- **알림 전송 성공률**: Slack 전송 성공/실패
- **Lambda 비용**: 일일 Lambda 호출 수 및 비용

### Sentry 에러 추적
```python
import sentry_sdk

sentry_sdk.init(
    dsn="https://...",
    traces_sample_rate=0.1,  # 10% 트레이싱
    environment="production"
)

# 에러 자동 추적
try:
    result = await analyze_job(job)
except Exception as e:
    sentry_sdk.capture_exception(e)
    raise
```

## 참고 자료

- **LangChain Docs**: https://python.langchain.com/docs/
- **LangGraph Tutorial**: https://langchain-ai.github.io/langgraph/
- **Pinecone Docs**: https://docs.pinecone.io/
- **Playwright Docs**: https://playwright.dev/python/
- **AWS Lambda Python**: https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html
- **Slack Bolt Python**: https://slack.dev/bolt-python/
- **RAG Best Practices**: https://www.pinecone.io/learn/retrieval-augmented-generation/
- **Few-shot Prompting**: https://www.promptingguide.ai/techniques/fewshot

## 향후 개선 계획

### Phase 6: 고급 기능 (선택)
- [ ] **멀티 모달 분석**: 이미지 공고 내 로고, 사무실 사진 분석 → 회사 문화 예측
- [ ] **네트워크 분석**: LinkedIn API 연동 → 현직자 연결 추천
- [ ] **연봉 예측**: 공고 + 사용자 경력 → GPT로 예상 연봉 범위 계산
- [ ] **면접 예상 질문 생성**: 회사 + 포지션 → GPT로 맞춤 면접 질문 생성
- [ ] **음성 알림**: Slack 대신 전화/SMS로 긴급 공고 알림 (Twilio 연동)
- [ ] **협업 필터링**: 유사한 프로필의 다른 사용자가 지원한 공고 추천

### Phase 7: 확장성
- [ ] **멀티 유저**: 친구/동료 초대 기능 (DynamoDB 파티션 키 설계)
- [ ] **웹 대시보드**: React로 웹 UI 구축 (Slack 외 추가 채널)
- [ ] **모바일 앱**: React Native로 푸시 알림
- [ ] **SaaS화**: Stripe 결제 연동, 프리미엄 기능 제공

---

**Last Updated**: 2025-02-12
**Version**: 2.0.0
**Author**: [Your Name]
**License**: MIT
