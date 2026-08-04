# 퀵스타 Open API v1.0 명세 (2026-07-23 확인)

출처: 퀵스타가 API 신청자에게 제공하는 비밀번호 보호 가이드 페이지
(Google Apps Script). 이 파일은 그 가이드에서 확인한 내용을 정리한 것이며,
**추측으로 채운 내용은 없습니다.**

- 공통 Base URL: `https://quickstar.co.kr/elpisapi2/`
- 모든 통신은 JSON
- 응답 공통: `code` = `"1"`(성공) / `"2"`(실패), `message`

## 엔드포인트 목록

| 기능 | 메소드 | 경로 |
|---|---|---|
| 회원가입 | POST | `memberJoin.php` |
| 신청서 접수 | POST | `application_api.php` |
| 신청서 수정 | PUT | `applicationModify_api.php` |
| 신청서 폐기 | DELETE | `applicationDelete_api.php` |
| 신청서 조회 | GET | `applicationInquiry_api.php` |
| 부가서비스 조회 | GET | `extra_service_api.php` |
| 운송방법 조회 | GET | `transport_api.php` |
| 운송회사 조회 | GET | `transportCompany_api.php` |
| 품목 조회 | GET | `hscode_api.php` |

## 1. 회원가입 (POST memberJoin.php)

- 요청 헤더: `key` (필수) — 회원가입용 key 값
- 요청 본문: `userId`, `userPassword`(평문), `userName`, `userEmail`,
  `userTel`(ex 010-1111-1111), `apiName`(API 연동명 고정값)
- 성공 응답: `{"code":"1","message":"success","userId":"...","tokenKey":"..."}`
  - **tokenKey는 이후 모든 API 인증에 사용**

## 2. 신청서 접수 (POST application_api.php)

- 요청 헤더: `user-session` (필수) — 회원가입 응답의 `tokenKey`
- 요청 본문

| 파라미터 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `userId` | varchar | 필수 | 회원아이디 |
| `ctrNum` | varchar | 필수 | 운송방법 번호(tr_no). ex "34"(항공일반) |
| `depositType` | varchar | 필수 | "1": 예치금 자동결제 / null or "": 안 함 |
| `fastType` | varchar | 필수 | "1": 빠른배송 / null or "": 안 함 |
| `bohumType` | int | 선택 | 1: 보험가입 |
| `RecInfo[]` | array | 필수 | 수취인 정보 |
| `extraSvc[]` | array | 선택 | 부가서비스 |
| `itemList[]` | array | 필수 | 상품 정보 |

### RecInfo[] (수취인)

| 파라미터 | 필수 | 설명 |
|---|---|---|
| `receiverName` | 필수 | 수취인명 |
| `zipCode` | 필수 | 우편번호 |
| `addr1` | 필수 | 주소 |
| `addr2` | 선택 | 상세주소 |
| `receiverPhone` | 필수 | 전화번호 |
| `personalNum` | 필수 | 개인통관고유부호 |
| `shipMemo` | 선택 | 택배사 요청사항 |
| `compulsionAgree` | 선택 | 1: 불일치 강제출고 동의 |

### itemList[] (상품)

| 파라미터 | 필수 | 설명 |
|---|---|---|
| `productShno` | 필수 | 품목번호(sh_no) — 품목 조회 API |
| `productHscode` | 필수 | HS코드(sh_hscode) |
| `productNameEng` | 필수 | 영문 상품명(sh_name_eng) |
| `orderNumber` | 선택 | 주문번호 |
| `trackingNumber` | 선택 | 트래킹번호 |
| `productMoney` | 필수 | 단가(float) |
| `productCount` | 필수 | 수량(int) |
| `imgUrl` / `siteUrl` | 선택 | 이미지/상세 URL |
| `option1~4` | 선택 | 옵션1/2, 실제수량(option3), 요청사항(option4) |
| `localFee` | 선택 | 현지운송료 |

### extraSvc[] (부가서비스)

`order`(입고) / `ship`(출고) / `pojang`(소형포장) / `etc`(대형포장) 각각에
op_no를 `"171,172"`처럼 쉼표로 구분해 넣습니다. **끝에 쉼표를 붙이면 오류.**

### 성공 응답

```json
{"code":"1","message":"신청완료","orderNo":"GR2511197392215","invoice":"(CJ)509485438255"}
```

### 주요 오류

- 토큰/아이디 오류: `API 인증에 실패했습니다. 토큰값(user-session)과 아이디(userId)를 다시 확인해주세요.`
- 운송방법 오류: `운송방법을 지정해 주세요.(항공(일반):34, 해운(자가):38, 해운(평택):37, 해운(인천):42, 항공(자가):43)`
- 필수값 누락: `필수값이 누락되었습니다(수취인)`

## 3. 신청서 수정 (PUT applicationModify_api.php)

접수 API 파라미터 + `appCode`(신청번호), `pending`(1 출고보류/0 해제),
`itemCode`(itemList 내 아이템번호).
**신청서 상태 입고대기(301) + 제품 상태 입고대기(1001)일 때만 수정 가능.**

## 4. 신청서 폐기 (DELETE applicationDelete_api.php)

쿼리 파라미터: `userId`, `orderNo`.
**입고대기(301)일 때만 폐기 가능.**

## 5. 신청서 조회 (GET applicationInquiry_api.php)

쿼리 파라미터: `userId`, `orderNo`(그룹번호 ex GR2403208504492)

- 그룹 상태: 빈값=입고대기/입고완료, 303 무게측정, 304 결제대기,
  305 결제확인중, 306 출고준비, 307 출고완료, `_peki` 폐기, `303_peki` 무게측정후폐기
- 제품 상태: 1001 입고대기, 1002 오류입고, 1003 입고완료, 1004 반품신청,
  1005 반품결제대기, 1006 반품결제확인중, 1007 반품완료폐기, 1000 폐기
- 응답 data[]: `groupCode`, `ctrNum`, `invoiceNo`(운송회사 번호), `invoice`(운송장번호),
  `largeInvoiceNo`(1 대신/2 경동/3 건영), `orderState`, `pending`,
  `RecInfo[]`(+`unipassResult` 0불일치/1일치/3기타), `weightList[]`,
  `paymentList[]`(payState 1결제대기/2확인중/3완료), `appList[]`

## 6~9. 조회 API

- **부가서비스** `extra_service_api.php`: 응답 `order[] / ship[] / pojang[] / etc[]`,
  각 항목 `op_type`, `op_no`, `op_name`, `op_memo`, `op_mon3`. (별도 API Key 있음)
- **운송방법** `transport_api.php`: `tr_no` — 34 항공(일반), 37 해운(평택),
  38 해운(자가), 42 해운(인천), 43 항공(자가)
- **운송회사** `transportCompany_api.php`: transportNo/tc_no —
  34/26 CJ대한통운(항공일반), 37/27 CJ(해운평택), 38/28 CJ(해운자가),
  34/32 롯데택배, 42/34 한진택배(해운인천), 43/35 CJ(항공자가)
- **품목** `hscode_api.php`: `sh_no`→productShno, `sh_hscode`→productHscode,
  `sh_name`(한글), `sh_name_eng`→productNameEng, `sh_name_cha`(중문)

## 실제로 호출해보고 확인한 사실 (2026-07-23)

1. **인증**: 발급받은 키는 `tokenKey`가 맞습니다. `user-session` 헤더에 넣고
   `userId`를 같이 보내면 인증이 통과합니다. (읽기 전용 조회 API로 확인)
   회원가입 API는 호출할 필요가 없습니다.
2. **목록 조회 API 4종**(품목/운송방법/운송회사/부가서비스)은 위 tokenKey가 아니라
   **가이드 문서에 공개된 조회 전용 키**를 헤더 `key`에 넣어야 동작합니다.
3. **`itemList`(상품정보)는 문서에 "필수"라고 되어 있지만, 실제로는 없어도
   접수가 성공합니다.** 수취인 정보(RecInfo)만 보내서 신청번호를 정상적으로
   받았습니다 (예: `GR2607236471061`). 그래서 지금 연동은 수취인 정보만 보냅니다.
   - 다만 접수 응답의 `invoice`(운송장번호)는 이때 빈 값으로 옵니다.
     운송장은 나중에 퀵스타 쪽에서 처리된 뒤 신청서 조회 API로 확인해야 할 것으로
     보입니다 (아직 확인 안 함).

## 아직 확인 못 한 점

- 상품정보 없이 접수한 신청서를, 나중에 퀵스타 화면이나 신청서 수정 API로
  상품정보를 채워야 하는지 여부.
- 접수 후 운송장번호가 언제 채워지는지 (신청서 조회 API로 재확인 필요).
