-- ============================================================
-- 001_init.sql
-- 타오바오 → 스마트스토어/ESM 자동 상품등록 프로그램 초기 스키마
-- 대상: Supabase (PostgreSQL), 별도 프로젝트의 public 스키마
-- ------------------------------------------------------------
-- 흐름:  수집(products_raw) → 가공/검수(products_processed)
--        → 마켓별 등록(listings)
--        보조표: keywords(인기 키워드), category_mapping(카테고리 매핑)
-- ------------------------------------------------------------
-- 이 파일은 "여러 번 실행해도 안전"하도록 IF NOT EXISTS 를 씁니다.
-- ============================================================

-- ------------------------------------------------------------
-- 공통: updated_at 자동 갱신용 트리거 함수
--   INSERT/UPDATE 시 updated_at 을 현재 시각으로 자동 세팅합니다.
-- ------------------------------------------------------------
create or replace function set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;


-- ============================================================
-- 1) products_raw : 타오바오 원본 수집 데이터 (가공 전 원자재)
-- ============================================================
create table if not exists products_raw (
    id              bigserial primary key,
    source_platform text        not null default 'taobao',   -- 출처(향후 1688 등 확장 대비)
    source_url      text        not null,                    -- 원본 상세 URL (중복수집 방지 키)
    source_item_id  text,                                    -- 타오바오 상품ID (URL에서 추출)
    title_original  text,                                    -- 원본(중국어) 상품명
    price_original  numeric(12,2),                           -- 원본 가격
    currency        text        not null default 'CNY',      -- 통화 단위
    options         jsonb,                                   -- 옵션 트리(색상/사이즈 등)
    image_urls      jsonb,                                   -- 이미지 URL 목록(배열)
    sales_count     integer,                                 -- 판매량
    review_count    integer,                                 -- 리뷰수
    shop_name       text,                                    -- 판매점 이름
    raw_payload     jsonb,                                   -- 수집 원본 전체(추후 재파싱용)
    collected_at    timestamptz not null default now(),      -- 수집일시
    created_at      timestamptz not null default now(),
    -- 같은 상품 URL은 한 번만 저장(재수집 시 UPDATE로 갱신)
    constraint products_raw_source_url_key unique (source_url)
);

comment on table products_raw is '타오바오에서 수집한 상품 원본 데이터';


-- ============================================================
-- 2) products_processed : 등록용 가공 데이터 (번역/키워드/가격계산 완료)
-- ============================================================
create table if not exists products_processed (
    id                  bigserial primary key,
    raw_id              bigint      not null
                        references products_raw(id) on delete cascade,  -- 원본과 연결
    title_ko            text,                                -- 인기 키워드 반영한 가공 상품명(한국어)
    description_ko      text,                                -- 상세설명(한국어)
    options_ko          jsonb,                               -- 번역된 옵션 트리
    weight_g            numeric(10,1),                       -- 무게(그램) — 직접 측정해서 입력
    shipping_cost_krw   numeric(12,2),                       -- 배송비(원) — 직접 측정/입력
    cost_price_krw      numeric(12,2),                       -- 원가(원화 환산, 상품값)
    sale_price_krw      numeric(12,2),                       -- 최종 판매가(계산 결과)
    margin_rate         numeric(6,3),                        -- 마진율(예: 0.350 = 35%)
    main_image_url      text,                                -- 대표 이미지
    detail_images       jsonb,                               -- 상세 이미지 목록(배열)
    category_smartstore text,                                -- 매핑된 스마트스토어 카테고리
    category_esm        text,                                -- 매핑된 ESM(옥션/지마켓) 카테고리
    -- 상품 자체의 진행 상태(마켓 무관). 등록 결과는 listings.list_status 참고.
    process_status      text        not null default '가공중'
                        check (process_status in ('수집완료','가공중','검수대기','검수완료')),
    processed_at        timestamptz,                         -- 가공 완료 시각
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

comment on table products_processed is '등록용으로 가공/검수하는 상품 데이터';

drop trigger if exists trg_products_processed_updated_at on products_processed;
create trigger trg_products_processed_updated_at
    before update on products_processed
    for each row execute function set_updated_at();


-- ============================================================
-- 3) keywords : 마켓별 인기 키워드 (수집일자별)
-- ============================================================
create table if not exists keywords (
    id             bigserial primary key,
    marketplace    text        not null
                   check (marketplace in ('smartstore','esm')),  -- 마켓 구분
    keyword        text        not null,                    -- 키워드
    rank           integer,                                 -- 인기 순위(있으면)
    search_volume  integer,                                 -- 검색량(있으면, 없으면 NULL)
    collected_date date        not null default current_date, -- 수집일자
    created_at     timestamptz not null default now(),
    -- 같은 마켓/키워드/수집일 조합은 한 번만
    constraint keywords_unique unique (marketplace, keyword, collected_date)
);

comment on table keywords is '마켓별(smartstore/esm) 인기 키워드 수집 결과';


-- ============================================================
-- 4) category_mapping : 타오바오 카테고리 → 마켓별 카테고리 매핑
-- ============================================================
create table if not exists category_mapping (
    id                  bigserial primary key,
    taobao_category_path text       not null,               -- 타오바오 카테고리 경로/코드
    marketplace         text        not null
                        check (marketplace in ('smartstore','esm')),
    market_category_id  text,                                -- 마켓 카테고리 코드(등록 API에 넣는 값)
    market_category_path text,                               -- 마켓 카테고리 경로(사람이 읽는 값)
    created_at          timestamptz not null default now(),
    -- 같은 타오바오 카테고리 × 마켓 조합은 하나의 매핑만
    constraint category_mapping_unique unique (taobao_category_path, marketplace)
);

comment on table category_mapping is '타오바오 카테고리를 마켓별 카테고리로 변환하는 매핑표';


-- ============================================================
-- 5) listings : 마켓별 등록 이력 (한 상품이 마켓마다 한 행)
-- ============================================================
create table if not exists listings (
    id                bigserial primary key,
    processed_id      bigint      not null
                      references products_processed(id) on delete cascade,  -- 가공상품과 연결
    marketplace       text        not null
                      check (marketplace in ('smartstore','esm')),
    -- 마켓 등록 결과 상태(마켓별로 다를 수 있음)
    list_status       text        not null default '등록대기'
                      check (list_status in ('등록대기','등록완료','판매중','품절','실패')),
    market_product_id text,                                  -- 마켓에서 발급한 등록ID(상품번호)
    listed_at         timestamptz,                           -- 등록 완료 시각
    fail_reason       text,                                  -- 실패 사유(실패 시)
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    -- 한 상품을 같은 마켓에 중복 등록하지 않도록
    constraint listings_unique unique (processed_id, marketplace)
);

comment on table listings is '가공 상품을 마켓별로 등록한 이력/상태';

drop trigger if exists trg_listings_updated_at on listings;
create trigger trg_listings_updated_at
    before update on listings
    for each row execute function set_updated_at();


-- ============================================================
-- 인덱스 : 자주 조회할 컬럼들
-- ============================================================
create index if not exists idx_products_raw_item_id      on products_raw(source_item_id);
create index if not exists idx_products_raw_collected     on products_raw(collected_at);
create index if not exists idx_products_processed_raw     on products_processed(raw_id);
create index if not exists idx_products_processed_status  on products_processed(process_status);
create index if not exists idx_keywords_market_date       on keywords(marketplace, collected_date);
create index if not exists idx_listings_processed         on listings(processed_id);
create index if not exists idx_listings_market_status     on listings(marketplace, list_status);
