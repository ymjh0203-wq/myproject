# ============================================================
# diag_seed.py  —  씨앗 이미지 추출만 따로 진단
# ------------------------------------------------------------
# 앱 전체가 아니라 "URL → 대표이미지" 단계만 실제 브라우저(headful)로
# 돌려서, 되는지/안 되면 왜인지 콘솔에 그대로 보여줍니다.
# 실행: 씨앗진단.bat  (URL 붙여넣기)
# ============================================================

import sys
import traceback

import seed


def main() -> None:
    url = sys.argv[1].strip() if len(sys.argv) > 1 else input("상품 URL 붙여넣고 Enter: ").strip()
    if not url:
        print("URL이 없습니다.")
        return
    print("=" * 60)
    print("씨앗 이미지 추출 진단")
    print("URL:", url)
    print("=" * 60)
    print("실제 브라우저 창이 뜹니다. 몇 초 기다려주세요...")
    print()
    try:
        r = seed.image_from_url(url)
        print("✅ 성공!")
        print("  대표이미지 주소:", (r.get("seed_image_url") or "")[:120])
        print("  로컬 저장 경로 :", r.get("seed_image_path"))
        print()
        print("→ URL만으로 이미지 자동추출이 됩니다. 앱에서도 됩니다.")
    except Exception as e:
        print("❌ 실패:", type(e).__name__, "-", e)
        print()
        print("--- 자세한 내용 ---")
        traceback.print_exc()
    print()
    print("진단 스크린샷: seeds/debug_seed.png, seeds/debug_seed.txt")


if __name__ == "__main__":
    main()
