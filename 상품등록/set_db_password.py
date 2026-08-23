# ============================================================
# set_db_password.py  —  Supabase DB 비밀번호를 .env 에 안전하게 넣는 도구
# ------------------------------------------------------------
# 실행: DB비밀번호_설정.bat 더블클릭
#  - 비밀번호를 물어봄(화면에 안 보임)
#  - .env 의 DATABASE_URL 을 완성해서 저장(특수문자도 자동 처리)
#  - 연결 테스트 + 표 6개 생성까지 한 번에
# ============================================================

import getpass
import glob
import os
import urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(BASE, ".env")

# 회원님 프로젝트 기준 풀러 주소 (비밀번호만 채우면 됨)
PROJECT_REF = "leypdhszkhahawkvtqgn"
REGION = "ap-southeast-2"
POOLER_HOST = f"aws-0-{REGION}.pooler.supabase.com"
PORT = "5432"


def build_url(password: str) -> str:
    enc = urllib.parse.quote(password, safe="")  # 특수문자 자동 인코딩
    return (f"postgresql://postgres.{PROJECT_REF}:{enc}"
            f"@{POOLER_HOST}:{PORT}/postgres")


def write_env(url: str) -> None:
    lines = []
    if os.path.exists(ENV):
        with open(ENV, encoding="utf-8") as f:
            lines = f.read().splitlines()
    out, found = [], False
    for ln in lines:
        if ln.strip().startswith("DATABASE_URL="):
            out.append(f"DATABASE_URL={url}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.insert(0, f"DATABASE_URL={url}")
    with open(ENV, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def main() -> None:
    print("=" * 56)
    print(" Supabase DB 비밀번호 설정")
    print("=" * 56)
    print(" Supabase에서 정한 DB 비밀번호를 입력하세요.")
    print(" (입력 글자는 화면에 보이지 않습니다. 다 치고 Enter)")
    print()
    pw = getpass.getpass(" 비밀번호: ").strip()
    if not pw:
        print(" 입력이 없어 취소했습니다.")
        return

    write_env(build_url(pw))
    print(" .env 저장 완료. 연결 테스트 중...")

    import db  # .env 저장 후 import
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
    except Exception as e:
        print()
        print(" [실패] 연결이 안 됩니다:", type(e).__name__)
        print("   ", str(e)[:200])
        print(" - 비밀번호가 맞는지, 특수문자면 Supabase에서 영문+숫자로 리셋했는지 확인하세요.")
        return

    # 표 생성 (migrations/*.sql)
    print(" 연결 성공! 표 만드는 중...")
    for f in sorted(glob.glob(os.path.join(BASE, "migrations", "*.sql"))):
        sql = open(f, encoding="utf-8").read()
        cur.execute(sql)
        conn.commit()
        print("   적용:", os.path.basename(f))
    conn.close()

    st = db.db_status()
    print()
    print(f" [완료] 백엔드={st['backend']} · 표 {st.get('tables_present')}/{st.get('tables_total')}개")
    print(" 이제 앱 홈의 노란 경고가 사라집니다. 앱을 새로고침하세요.")


if __name__ == "__main__":
    main()
