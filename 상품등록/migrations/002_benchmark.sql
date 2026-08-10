-- ============================================================
-- 002_benchmark.sql
-- 벤치마킹 소싱(이미지 검색) 지원용 스키마 추가
-- ------------------------------------------------------------
-- 잘 팔리는 "기준 상품(씨앗)"의 이미지로 타오바오에서 유사 상품을 찾고,
-- 찾은 후보들이 어떤 씨앗에서 나왔는지 연결해서 저장합니다.
-- 여러 번 실행해도 안전(IF NOT EXISTS).
-- ============================================================

-- ------------------------------------------------------------
-- benchmark_seeds : 벤치마킹 기준 상품(씨앗)
--   seed_type='url'   → 한국 마켓 상품 URL 에서 대표이미지를 뽑아 검색
--   seed_type='image' → 사용자가 직접 넣은 로컬 이미지로 검색
-- ------------------------------------------------------------
create table if not exists benchmark_seeds (
    id              bigserial primary key,
    seed_type       text        not null check (seed_type in ('url','image')),
    seed_ref        text        not null,   -- url이면 상품URL, image면 원본 파일 경로/이름
    seed_image_url  text,                   -- URL에서 추출한 대표이미지 주소(있으면)
    seed_image_path text,                   -- 실제 검색에 사용한 로컬 이미지 경로
    note            text,                   -- 메모(선택)
    created_at      timestamptz not null default now()
);

comment on table benchmark_seeds is '벤치마킹 소싱의 기준 상품(씨앗) 이미지 정보';

-- ------------------------------------------------------------
-- products_raw 에 "어떤 씨앗에서, 몇 번째 유사 후보로 나왔는지" 연결 컬럼 추가
-- ------------------------------------------------------------
alter table products_raw
    add column if not exists seed_id bigint references benchmark_seeds(id) on delete set null;

alter table products_raw
    add column if not exists match_rank integer;   -- 이미지검색 결과 순위(1이 가장 유사)

create index if not exists idx_products_raw_seed on products_raw(seed_id);
