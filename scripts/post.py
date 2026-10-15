"""
1日1問 あたまの体操 ― Instagram 自動投稿（GitHub Actions で動く）

使い方（.github/workflows/post.yml から呼ばれる）
  python scripts/post.py check   … つながるかの確認だけ（投稿しない）
  python scripts/post.py post    … 今日（日本時間）の queue/<日付>/ を投稿する

- 投稿する画像は queue/<YYYY-MM-DD>/1.jpg … と caption.txt
- 投稿したら posted/<日付>/ に移し、posted/<日付>/result.json に投稿IDを残す
- トークンは secret/token.enc（暗号化）。鍵は GitHub Secrets の TOKEN_KEY
- トークンは7日ごとに延長し、新しくなっていれば暗号化し直して保存する
"""
import hashlib, json, os, subprocess, sys, time, shutil, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

API = "https://graph.instagram.com/v23.0"
REPO = os.environ.get("GITHUB_REPOSITORY", "kotsu-de-toku/atama-quiz")
BRANCH = "main"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(ROOT, "secret", "token.enc")
STATE_FILE = os.path.join(ROOT, "secret", "state.json")
JST = timezone(timedelta(hours=9))


def crypt(data: str, decrypt: bool) -> str:
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-a", "-A", "-pass", "env:TOKEN_KEY"]
    if decrypt:
        args.append("-d")
    key = os.environ.get("TOKEN_KEY", "").strip()   # 貼り付けで付いた改行・空白を取り除く
    env = dict(os.environ, TOKEN_KEY=key)
    r = subprocess.run(args, input=data.strip().encode(), capture_output=True, env=env)
    if r.returncode != 0:
        fp = hashlib.sha256(key.encode()).hexdigest()[:8]   # 鍵そのものは出さず、照合用の指紋だけ
        raise SystemExit(f"トークンの鍵が開けられません（Secrets の TOKEN_KEY を確認。指紋 {fp}、長さ {len(key)}）: " + r.stderr.decode(errors="replace")[:120])
    return r.stdout.decode().strip()


def call(method, path, **params):
    url = f"{API}/{path}"
    body = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(url + "?" + body.decode())
    else:
        req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")
        # トークンがログに出ないように伏せる
        raise SystemExit(f"Instagram API エラー ({path}): {e.code} {msg.replace(params.get('access_token', '@@'), '***')}")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def get_token():
    with open(TOKEN_FILE, encoding="utf-8") as f:
        token = crypt(f.read(), decrypt=True)
    print(f"::add-mask::{token}")
    return token


def maybe_refresh(token):
    """7日に1回、トークンの期限を延ばす（60日で切れるのを防ぐ）"""
    st = load_state()
    last = st.get("refreshed_at")
    now = datetime.now(JST)
    if last and now - datetime.fromisoformat(last) < timedelta(days=7):
        return token
    r = call("GET", "refresh_access_token", grant_type="ig_refresh_token", access_token=token)
    new = r["access_token"]
    print(f"::add-mask::{new}")
    if new != token:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(crypt(new, decrypt=False) + "\n")
    st["refreshed_at"] = now.isoformat(timespec="seconds")
    st["expires_in_days"] = round(r.get("expires_in", 0) / 86400, 1)
    save_state(st)
    print(f"トークンを延長しました（残り約{st['expires_in_days']}日）")
    return new


def wait_ready(cid, token):
    for _ in range(30):
        s = call("GET", cid, fields="status_code", access_token=token).get("status_code")
        if s == "FINISHED":
            return
        if s in ("ERROR", "EXPIRED"):
            raise SystemExit(f"画像の準備に失敗しました: {cid} {s}")
        time.sleep(5)
    raise SystemExit(f"画像の準備が終わりませんでした: {cid}")


def check():
    token = get_token()
    me = call("GET", "me", fields="user_id,username,account_type", access_token=token)
    lim = call("GET", "me/content_publishing_limit", fields="quota_usage,config", access_token=token)
    print(f"つながりました：@{me.get('username')}（{me.get('account_type')}）")
    print(f"今日の投稿回数：{json.dumps(lim.get('data', lim), ensure_ascii=False)}")
    maybe_refresh(token)


def post():
    token = get_token()
    token = maybe_refresh(token)
    today = datetime.now(JST).strftime("%Y-%m-%d")
    src = os.path.join(ROOT, "queue", today)
    dst = os.path.join(ROOT, "posted", today)
    if os.path.exists(dst):
        print(f"{today} はもう投稿済みです")
        return
    start = load_state().get("start_date", "9999-12-31")
    if today < start and not os.path.isdir(src):
        print(f"開始日（{start}）より前なので、今日は投稿しません")
        return
    if not os.path.isdir(src):
        raise SystemExit(f"{today} の投稿が queue にありません（問題の貯金が切れています）")

    imgs = sorted([f for f in os.listdir(src) if f.lower().endswith(".jpg")], key=lambda x: int(x.split(".")[0]))
    with open(os.path.join(src, "caption.txt"), encoding="utf-8") as f:
        caption = f.read().strip()
    me = call("GET", "me", fields="user_id", access_token=token)
    uid = me["user_id"]

    base = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/queue/{today}"
    children = []
    for name in imgs:
        r = call("POST", f"{uid}/media", image_url=f"{base}/{name}", is_carousel_item="true", access_token=token)
        children.append(r["id"])
        print(f"画像を登録：{name}")
    for c in children:
        wait_ready(c, token)

    if len(children) == 1:
        raise SystemExit("画像が1枚しかありません（4枚の想定）")
    car = call("POST", f"{uid}/media", media_type="CAROUSEL", children=",".join(children), caption=caption, access_token=token)
    wait_ready(car["id"], token)
    pub = call("POST", f"{uid}/media_publish", creation_id=car["id"], access_token=token)
    print(f"投稿しました：{today}（id {pub['id']}）")

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    with open(os.path.join(dst, "result.json"), "w", encoding="utf-8") as f:
        json.dump({"date": today, "media_id": pub["id"], "posted_at": datetime.now(JST).isoformat(timespec="seconds")}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    try:
        {"check": check, "post": post}[mode]()
    except SystemExit as e:
        if e.code not in (None, 0):
            # GitHub の画面（と失敗メール）に理由が出るようにする
            print(f"::error::{e.code}")
        raise
    except Exception as e:
        print(f"::error::{type(e).__name__}: {e}")
        raise
