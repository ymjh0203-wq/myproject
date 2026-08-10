# 상품등록 (타오바오 → 스마트스토어/ESM 자동등록)

타오바오에서 상품을 수집 → 번역/키워드 반영/가격계산으로 가공 → **사람이 검수** →
네이버 스마트스토어·옥션/지마켓(ESM PLUS)에 등록하는 반자동 프로그램.

> 흐름: **수집 → 가공 → 검수(사람) → 등록**

## 진행 단계
- [x] Step 1 — Supabase 스키마 설계
- [x] **Step 2 — 타오바오 상품 수집기 MVP** ← 현재 (Playwright, 프로필 재사용)
- [ ] Step 3 — 이후 논의 (가공/키워드/등록)

## 폴더 구조
```
상품등록/
├─ migrations/
│  └─ 001_init.sql        # 초기 스키마 (5개 표 + 인덱스 + 트리거)
├─ apply_migration.py     # .sql 을 Supabase 에 적용하는 스크립트
├─ collector/
│  └─ taobao.py           # Playwright 타오바오 상세페이지 수집 + 캡차 감지
├─ db.py                  # DATABASE_URL 연결 + products_raw upsert
├─ collect.py             # CLI: python collect.py "<타오바오 URL>"
├─ requirements.txt
├─ .env.example           # DATABASE_URL 넣는 곳 (복사해서 .env 로)
└─ README.md
```

## 테이블 개요
| 표 | 역할 |
|---|---|
| `products_raw` | 타오바오 원본 수집 데이터 |
| `products_processed` | 등록용 가공 데이터 (`process_status`: 수집완료/가공중/검수대기/검수완료) |
| `keywords` | 마켓별 인기 키워드 (수집일자별) |
| `category_mapping` | 타오바오 → 마켓 카테고리 매핑 |
| `listings` | 마켓별 등록 이력 (`list_status`: 등록대기/등록완료/판매중/품절/실패) |

> **상태 분리:** 상품 진행상태(마켓 무관)는 `products_processed.process_status`,
> 마켓별 등록 결과는 `listings.list_status` 에 둡니다. 한 상품이 스마트스토어는
> 등록완료·ESM은 실패인 경우를 자연스럽게 표현합니다.

## Step 1 실행 방법
```bash
cd 상품등록
python -m venv .venv && .venv\Scripts\activate   # (선택) 가상환경
pip install -r requirements.txt

copy .env.example .env      # 그리고 .env 안의 DATABASE_URL 을 채우기
python apply_migration.py    # migrations/*.sql 을 Supabase 에 적용
```

## Step 2 실행 방법 (타오바오 수집)
```bash
cd 상품등록
pip install -r requirements.txt
playwright install chromium              # 브라우저 엔진 1회 설치
python 타오바오_로그인_설정.bat            # (최초 1회) 창에서 직접 로그인
python collect.py "https://item.taobao.com/item.htm?id=..." --dry-run   # 저장없이 추출만
python collect.py "https://item.taobao.com/item.htm?id=..."             # products_raw 저장
```
- 최초 1회 `타오바오_로그인_설정.bat` 으로 직접 로그인하면 `.browser_profile/` 에
  세션이 저장되어 이후 유지됩니다.
- `--dry-run` 은 DB 없이 추출 결과만 출력합니다(셀렉터 점검용).
- 로그인/캡차가 보이면 그 창에서 직접 처리합니다(프로그램은 자동으로 뚫지 않음).
- 프로그램은 캡차를 자동으로 뚫지 않습니다(정책·차단 위험). 감지 시 멈추고 알려줍니다.
- 타오바오 HTML 구조는 자주 바뀝니다. 추출이 비는 필드가 있으면 로그에 표시되며,
  `collector/taobao.py` 의 셀렉터를 실제 페이지에 맞게 조정해야 할 수 있습니다.

## ⚠️ 정책/약관 메모
- **타오바오 크롤링은 ToS 위반 소지·차단 위험**이 있어 Step 2 진입 전 방향을 재논의합니다
  (강행 / 공식 오픈API / 수동 URL 붙여넣기 반자동 중 택1).
- 네이버 커머스API·ESM PLUS API 는 판매자 인증·API 키 발급/승인 후 실제 등록 호출이 가능합니다.
