"""쿠팡 상품 URL에 *실제로 들어 있는 값*만 추출한다.

이 모듈은 상품 페이지를 크롤링하지 않고 URL 문자열만 분석한다. 따라서 URL에
없는 상품명, 카테고리, 옵션 또는 카탈로그 ID를 추측하지 않는다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit


PRODUCT_PATH_RE = re.compile(r"/(?:vp/)?products/(?P<value>\d+)(?:/|$)", re.IGNORECASE)
OBSERVED_ID_KEY_RE = re.compile(r"(?:catalog|productset).*id$", re.IGNORECASE)
KNOWN_QUERY_KEYS = {
    "productid": "productId",
    "vendoritemid": "vendorItemId",
    "itemid": "itemId",
}


@dataclass(frozen=True)
class Identifier:
    kind: str
    value: str
    source: str
    meaning: str
    official_meaning_confirmed: bool
    wing_search_confirmed: bool = False


@dataclass(frozen=True)
class ParseResult:
    original_url: str
    normalized_url: str
    host: str | None
    is_coupang_url: bool
    identifiers: tuple[Identifier, ...]
    warnings: tuple[str, ...]

    def value_for(self, kind: str) -> str | None:
        for identifier in self.identifiers:
            if identifier.kind == kind:
                return identifier.value
        return None

    def search_candidates(self) -> tuple[Identifier, ...]:
        """WING에서 실제로 시험할 후보 순서.

        2026-08-10 실제 상품 1건 검증에서 productId 검색이 성공했다. vendorItemId와
        itemId는 아직 지원 여부를 확인하지 못했으므로 뒤쪽의 대체 후보로만 둔다.
        """

        by_kind = {item.kind: item for item in self.identifiers}
        return tuple(
            by_kind[kind]
            for kind in ("productId", "vendorItemId", "itemId")
            if kind in by_kind
        )

    def to_dict(self) -> dict:
        return {
            "original_url": self.original_url,
            "normalized_url": self.normalized_url,
            "host": self.host,
            "is_coupang_url": self.is_coupang_url,
            "identifiers": [asdict(item) for item in self.identifiers],
            "warnings": list(self.warnings),
        }


OFFICIAL_MEANINGS = {
    "productId": "노출상품 ID. 결합/분리로 바뀔 수 있는 상품 노출 단위",
    "vendorItemId": "옵션 ID. 쿠팡의 가장 작은 상품 단위",
    "itemId": "상품 조회 응답의 옵션 하위 식별값. 공개 FAQ에는 별도 정의가 없음",
}


def _is_coupang_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.lower().rstrip(".")
    return host == "coupang.com" or host.endswith(".coupang.com") or host == "coupa.ng"


def _normalize_url(raw_url: str) -> str:
    value = raw_url.strip()
    if value and "://" not in value:
        value = "https://" + value
    return value


def _safe_url(parts) -> str:
    """사용자 정보와 fragment를 제외한 저장용 URL을 만든다."""

    host = parts.hostname or ""
    return urlunsplit((parts.scheme.lower(), host.lower(), parts.path, parts.query, ""))


def parse_coupang_url(raw_url: str) -> ParseResult:
    original = raw_url or ""
    normalized_input = _normalize_url(original)
    warnings: list[str] = []

    try:
        parts = urlsplit(normalized_input)
    except ValueError as exc:
        return ParseResult(original, "", None, False, (), (f"URL 형식 오류: {exc}",))

    host = parts.hostname.lower() if parts.hostname else None
    is_coupang = _is_coupang_host(host)
    if not normalized_input:
        warnings.append("상품 URL을 입력하세요.")
    elif parts.scheme.lower() not in {"http", "https"}:
        warnings.append("http 또는 https URL만 사용할 수 있습니다.")
        is_coupang = False
    elif not is_coupang:
        warnings.append("쿠팡 공식 도메인의 상품 URL이 아닙니다.")

    if not is_coupang:
        return ParseResult(original, _safe_url(parts), host, False, (), tuple(warnings))

    found: dict[str, Identifier] = {}
    path_match = PRODUCT_PATH_RE.search(parts.path)
    if path_match:
        found["productId"] = Identifier(
            "productId",
            path_match.group("value"),
            "URL 경로 /vp/products/{값}",
            OFFICIAL_MEANINGS["productId"],
            True,
        )

    observed: list[Identifier] = []
    for raw_key, raw_value in parse_qsl(parts.query, keep_blank_values=True):
        canonical_key = KNOWN_QUERY_KEYS.get(raw_key.lower())
        if canonical_key:
            if not raw_value.isdigit():
                warnings.append(f"{raw_key} 값이 숫자가 아니어서 사용하지 않았습니다.")
                continue
            if canonical_key in found and found[canonical_key].value != raw_value:
                warnings.append(
                    f"경로와 쿼리의 {canonical_key} 값이 서로 달라 경로 값을 유지했습니다."
                )
                continue
            found.setdefault(
                canonical_key,
                Identifier(
                    canonical_key,
                    raw_value,
                    f"URL 쿼리 {raw_key}",
                    OFFICIAL_MEANINGS[canonical_key],
                    canonical_key in {"productId", "vendorItemId"},
                ),
            )
        elif OBSERVED_ID_KEY_RE.search(raw_key) and raw_value:
            observed.append(
                Identifier(
                    f"관찰됨:{raw_key}",
                    raw_value,
                    f"URL 쿼리 {raw_key}",
                    "URL에 실제 존재하지만 공식 의미와 WING 용도는 확인되지 않음",
                    False,
                )
            )

    identifiers = tuple(found[k] for k in ("productId", "vendorItemId", "itemId") if k in found)
    identifiers += tuple(observed)

    if host == "coupa.ng" and not identifiers:
        warnings.append("단축 URL은 이동 후 표시되는 최종 coupang.com URL을 다시 넣어야 합니다.")
    if not identifiers:
        warnings.append("URL에서 확인 가능한 productId, vendorItemId 또는 itemId가 없습니다.")
    if observed:
        warnings.append("미확인 ID는 검색에 자동 사용하지 말고 WING에서 의미를 확인하세요.")

    return ParseResult(
        original,
        _safe_url(parts),
        host,
        True,
        identifiers,
        tuple(warnings),
    )
