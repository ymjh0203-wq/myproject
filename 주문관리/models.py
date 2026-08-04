# ==========================================================
# 상태값 정의 및 매핑 (models.py)
# ----------------------------------------------------------
# 이 프로그램은 상태를 두 가지로 나눠서 관리합니다.
#
#   market_status : 쿠팡(또는 다른 마켓)이 보내준 "원본" 주문 상태 문자열
#                    예) 쿠팡이라면 "ACCEPT", "INSTRUCT" 같은 값이 올 수 있음
#                    (정확한 값은 공식 API 문서 확인 후 채워 넣습니다. 아직 추측하지 않습니다.)
#
#   work_status   : 우리 프로그램이 화면에 보여주고 관리하는 "내부" 작업 상태
#                    아래 5단계 중 하나입니다.
#
# 화면 코드에서는 절대 "신규주문", "발송대기" 같은 문자열을 직접 쓰지 않고,
# 이 파일에 정의된 상수(WORK_STATUS_...)를 가져다 씁니다.
# 나중에 상태 이름을 바꾸고 싶을 때 이 파일 하나만 고치면 되도록 하기 위함입니다.
# ==========================================================

# ----------------------------------------------------------
# 내부 작업 상태 (work_status) - 화면 상단 메뉴 순서와 동일합니다.
# ----------------------------------------------------------
WORK_STATUS_NEW = "신규주문"
WORK_STATUS_READY_TO_SHIP = "발송대기"
WORK_STATUS_SHIPPING = "배송중"
WORK_STATUS_DELIVERED = "배송완료"
WORK_STATUS_PURCHASE_CONFIRMED = "구매확정"
# 취소·반품 등으로 쿠팡 주문목록에서 사라진 주문을 여기로 옮깁니다. 그러면
# 신규주문/발송대기 화면에서 자동으로 빠집니다. (정상 주문 흐름이 아니라
# "정리됨"을 뜻하므로 아래 WORK_STATUS_LIST(정상 단계 순서)에는 넣지 않습니다)
WORK_STATUS_CLOSED = "주문종료(취소·반품 등)"

# 화면 상단 메뉴에 보여줄 순서 그대로 나열한 목록입니다.
WORK_STATUS_LIST = [
    WORK_STATUS_NEW,
    WORK_STATUS_READY_TO_SHIP,
    WORK_STATUS_SHIPPING,
    WORK_STATUS_DELIVERED,
    WORK_STATUS_PURCHASE_CONFIRMED,
]


# ----------------------------------------------------------
# 통관정보 검증 상태 (shipping_information.validation_status)
# ----------------------------------------------------------
VALIDATION_STATUS_FORMAT_PASSED = "형식검사 통과"
VALIDATION_STATUS_MISSING_INFO = "정보누락"
VALIDATION_STATUS_FORMAT_ERROR = "형식오류"
VALIDATION_STATUS_WAITING_OFFICIAL = "공식검증 대기"
VALIDATION_STATUS_OFFICIAL_PASSED = "공식검증 완료"
VALIDATION_STATUS_OFFICIAL_MISMATCH = "공식검증 불일치"
VALIDATION_STATUS_REQUEST_ERROR = "검증 요청 오류"
VALIDATION_STATUS_NEEDS_REVALIDATION = "재검증 필요"

# 이 상태일 때만 발송(다음 단계 진행)을 허용합니다.
SHIPPABLE_VALIDATION_STATUSES = {
    VALIDATION_STATUS_OFFICIAL_PASSED,
}


# ----------------------------------------------------------
# 마켓 원본 상태 -> 내부 작업 상태 매핑
# ----------------------------------------------------------
# 쿠팡 공식 문서(주문상태 정의)로 확인한 값입니다. (2026-07-18 확인)
# 참고용으로 채워뒀지만, 재조회했을 때 이 매핑으로 work_status를 자동으로
# 바꾸는 기능은 아직 만들지 않았습니다 (work_status는 지금은 우리 프로그램
# 안에서 직접 이동시키는 방식으로만 바뀝니다).
MARKET_STATUS_TO_WORK_STATUS = {
    "ACCEPT": WORK_STATUS_NEW,              # 결제완료
    "INSTRUCT": WORK_STATUS_READY_TO_SHIP,  # 상품준비중
    "DEPARTURE": WORK_STATUS_SHIPPING,      # 배송지시(발송)
    "DELIVERING": WORK_STATUS_SHIPPING,     # 배송중
    "FINAL_DELIVERY": WORK_STATUS_DELIVERED,  # 배송완료
}


# ----------------------------------------------------------
# 택배사 코드 (쿠팡 송장업로드 API에 보낼 때 씁니다)
# ----------------------------------------------------------
# 출처: 쿠팡 개발자센터 "Courier Code" 문서 (2026-07-21, 실제 페이지 전체를
# 확인해서 목록 전부를 옮겨왔습니다). AI가 페이지를 요약해서 가져온 내용이라,
# 실제 송장 등록 시 쿠팡이 코드를 거부하면 이 목록의 오타/누락일 수 있으니
# 공식 문서에서 다시 확인해야 합니다.
COURIER_CODES = {
    "HYUNDAI": "롯데택배",
    "KGB": "로젠택배",
    "EPOST": "우체국",
    "HANJIN": "한진택배",
    "CJGLS": "CJ대한통운",
    "KOREX": "대한통운[합병]",
    "KDEXP": "경동택배",
    "DIRECT": "업체직송",
    "ILYANG": "일양택배",
    "CHUNIL": "천일특송",
    "AJOU": "아주택배",
    "CSLOGIS": "SC로지스",
    "DAESIN": "대신택배",
    "CVS": "CVS택배",
    "HDEXP": "합동택배",
    "DHL": "DHL",
    "UPS": "UPS",
    "FEDEX": "FEDEX",
    "REGISTPOST": "우편등기",
    "EMS": "우체국 EMS",
    "TNT": "TNT",
    "USPS": "USPS",
    "IPARCEL": "i-parcel",
    "GSMNTON": "GSM NtoN",
    "SWGEXP": "성원글로벌",
    "PANTOS": "범한판토스",
    "ACIEXPRESS": "ACI Express",
    "DAEWOON": "대운글로벌",
    "AIRBOY": "에어보이익스프레스",
    "KGLNET": "KGL네트웍스",
    "KUNYOUNG": "건영택배",
    "SLX": "SLX택배",
    "HONAM": "우리택배",
    "LINEEXPRESS": "LineExpress",
    "TWOFASTEXP": "2FastsExpress",
    "HPL": "한의사랑택배",
    "GOODSTOLUCK": "굿투럭",
    "KOREXG": "CJ대한통운특",
    "HANDEX": "한덱스",
    "BGF": "BGF포스트",
    "ECMS": "ECMS익스프레스",
    "WONDERS": "원더스퀵",
    "YONGMA": "용마로지스",
    "SEBANG": "세방택배",
    "NHLOGIS": "농협택배",
    "LOTTEGLOBAL": "롯데글로벌",
    "GSIEXPRESS": "GSI익스프레스",
    "EFS": "EFS",
    "DHLGLOBALMAIL": "DHL GlobalMail",
    "HILOGIS": "Hi택배",
    "GPSLOGIX": "GPS로직",
    "CRLX": "시알로지텍",
    "BRIDGE": "브리지로지스",
    "HOMEINNOV": "홈이노베이션로지스",
    "CWAY": "씨웨이",
    "GNETWORK": "자이언트",
    "ACEEXP": "ACE Express",
    "WEVILL": "우리동네택배",
    "FOREVERPS": "퍼레버택배",
    "WARPEX": "워펙스",
    "QXPRESS": "큐익스프레스",
    "SMARTLOGIS": "스마트로지스",
    "HOMEPICK": "홈픽택배",
    "GTSLOGIS": "GTS로지스",
    "ESTHER": "에스더쉬핑",
    "INTRAS": "로토스",
    "EUNHA": "은하쉬핑",
    "UFREIGHT": "유프레이트 코리아",
    "LSERVICE": "엘서비스",
    "TPMLOGIS": "로지스밸리",
    "ZENIELSYSTEM": "제니엘시스템",
    "ANYTRACK": "애니트랙",
    "JLOGIST": "제이로지스트",
    "CHAINLOGIS": "두발히어로(4시간당일택배)",
    "QRUN": "큐런",
    "FRESHSOLUTIONS": "프레시솔루션",
    "HIVECITY": "하이브시티",
    "HANSSEM": "한샘",
    "SFC": "SFC(Santai)",
    "JNET": "J-NET",
    "GENIEGO": "지니고",
    "PANASIA": "판아시아",
    "ELIAN": "elianpost",
    "LOTTECHILSUNG": "롯데칠성",
    "SBGLS": "SBGLS",
    "ALLTAKOREA": "올타코리아",
    "YUNDA": "yunda express",
    "VALEX": "발렉스",
    "KOKUSAI": "국제익스프레스",
    "XINPATEK": "윈핸드해운항공",
    "HEREWEGO": "탱고앤고",
    "WOONGJI": "웅지익스프레스",
    "PINGPONG": "핑퐁",
    "YDH": "YDH",
    "CARGOPLEASE": "화물부탁해",
    "LOGISPOT": "로지스팟",
    "FRESHMATES": "프레시메이트",
    "VROONG": "부릉",
    "NKLS": "NK로지솔루션",
    "DODOFLEX": "도도플렉스",
    "ETOMARS": "이투마스",
    "SHIPNERGY": "배송하기좋은날",
    "VENDORPIA": "벤더피아",
    "COSHIP": "캐나다쉬핑",
    "GDAKOREA": "지디에이코리아",
    "BABABA": "바바바로지스",
    "TEAMFRESH": "팀프레시",
    "HOME1004": "1004홈",
    "NAEUN": "나은물류",
    "ACCCARGO": "acccargo",
    "NTLPS": "엔티엘피스",
    "EKDP": "삼다수가정배송",
    "HOTSINGCARGO": "허싱카고코리아",
    "SINOEX": "SINOTRANS EXPRESS",
    "DRABBIT": "딜리래빗",
    "HOMEPICKTODAY": "홈픽오늘도착",
    "DAERIM": "대림통운",
    "LOGISPARTNER": "로지스파트너",
    "GOBOX": "고박스",
    "FASTBOX": "패스트박스",
    "PANSTAR": "팬스타국제특송",
    "ACTCORE": "에이씨티앤코아물류",
    "KJT": "케이제이티",
    "THEBAO": "더바오",
    "RUSH": "오늘회러쉬",
    "KT": "kt express",
    "IBP": "ibpcorp",
    "HY": "HY",
    "LOGISVALLEY": "로지스밸리",
    "TODAY": "투데이",
    "ONEDAYLOGIS": "라스트마일시스템즈",
    "HKHOLDINGS": "에이치케이홀딩스",
    "JIKGUMOON": "직구문",
    "CUBEFLOW": "큐브플로우",
    "SHFLY": "성훈물류",
    "GBS": "지비에스",
    "BANPOOM": "반품구조대",
    "GLOVIS": "현대글로비스",
    "ARGO": "아르고",
    "JMNP": "딜리박스",
    "SELC": "삼성로지텍",
    "MTINTER": "엠티인터네셔널",
    "GDSP": "골드스넵스",
    "TODAYPICKUP": "오늘의픽업",
    "YJSGLOBAL": "yjs글로벌",
    "DUXGLOBAL": "유로택배",
    "INTERLOGIS": "인터로지스",
    "WOOJIN": "우진인터로지스",
    "GHSPEED": "지에이치스피드",
    "WIDETECH": "와이드테크",
    "ECOHAI": "에코하이",
    "TONAMI": "토나미",
    "DAIICHI": "제1화물",
    "FUKUYAMA": "후쿠야마통운",
    "KURLYNEXTMILE": "컬리넥스트마일",
    "ARAMEX": "ARAMEX",
    "BISNZ": "BISNZ",
    "INNOS": "이노스",
    "SEORIM": "서림물류",
    "WEMOVE": "위무브",
    "POOLATHOME": "풀앳홈",
    "SPARKLE": "스파클직배송",
    "ICS": "ICS",
    "HANMI": "한미포스트",
    "CAINIAO": "CAINIAO",
    "HWATONG": "화통",
    "ESTLA": "이스트라",
    "IK": "IK물류",
    "PULMUONEWATER": "풀무원샘물",
    "TSG": "티에스지로지스",
    "OCS": "ocs코리아",
    "MDLOGIS": "모든로지스",
    "GCS": "지씨에스",
    "FTF": "물류대장LCS",
    "HUBNET": "Hubnet Logistics",
    "WINION_3P": "위니온로지스",
    "WOORIHB": "우리한방택배",
    "LETUS": "레터스",
    "JWTNL": "JWTNL",
    "JCLS": "JCLS",
    "GKGLOBAL": "지케이글로벌",
    "GONELO": "고넬로",
    "CASA": "신세계까사",
    "BRCH": "비알씨에이치",
    "HLDE": "W7",
    "MORNINGGLOBAL": "모닝글로벌",
    "DNDN": "든든택배",
    "KGBLS": "KGB로지스",
    "KGBPS": "KGB택배",
    "DONGBU": "드림택배",
    "YELLOW": "옐로우캡",
    "INNOGIS": "GTX로지스",
    "DADREAM": "다드림",
    "IQS": "굿스포스트",
    "SFEXPRESS": "순풍택배",
    "LGE": "LG전자",
    "WINION": "위니온",
    "WINION2": "위니온(에어컨)",
}


def map_market_status_to_work_status(market_status: str, default: str = WORK_STATUS_NEW) -> str:
    """
    마켓(쿠팡 등)의 원본 주문 상태 문자열을 내부 작업 상태로 변환합니다.
    매핑표에 없는 값이면 default(보통 신규주문)를 돌려줍니다.
    """
    return MARKET_STATUS_TO_WORK_STATUS.get(market_status, default)
