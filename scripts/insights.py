"""
インスタの数字（インサイト）を取ってきて、暗号化して secret/insights.enc にためる。2026-11-11〜
GitHub Actions（.github/workflows/insights.yml）で毎晩動く。読むだけで、投稿・削除はしない。
- 表は公開されないように、合鍵と同じ鍵（Secrets の TOKEN_KEY）で暗号化して保存する
- 合鍵（トークン）の期限延長も、ここで7日に1回行う（自動投稿を止めていても切れないように）
- パソコン側は tools/insights.py が暗号を解いて logs/insights.csv にする
取れない数字：スキップ率（APIにない）、年齢の割合（フォロワー100人未満は出ない）
"""
import os, sys, csv, io, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from post import ROOT, JST, crypt, call, get_token, maybe_refresh

OUT = os.path.join(ROOT, "secret", "insights.enc")
METRICS = {"REELS": "views,reach,likes,comments,saved,shares,total_interactions,ig_reels_avg_watch_time,ig_reels_video_view_total_time",
           "FEED": "views,reach,likes,comments,saved,shares,total_interactions"}
COLS = ["取得日", "投稿日時", "種類", "views", "reach", "likes", "comments", "saved", "shares", "total_interactions",
        "ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "フォロワー数", "最初の行", "リンク", "id"]


def try_call(path, **params):
    try:
        return call("GET", path, **params)
    except SystemExit as e:
        print("取れませんでした:", str(e)[:200])
        return None


def main():
    token = maybe_refresh(get_token())
    me = call("GET", "me", fields="user_id,username,followers_count,media_count", access_token=token)
    items = call("GET", f"{me['user_id']}/media", fields="id,media_product_type,timestamp,caption,permalink",
                 limit=50, access_token=token)["data"]
    today = datetime.now(JST).date().isoformat()
    rows = []
    if os.path.exists(OUT):
        old = crypt(open(OUT, encoding="utf-8").read(), decrypt=True)
        rows = [r for r in csv.DictReader(io.StringIO(old)) if r["取得日"] != today]
    ok = 0
    for m in items:
        kind = "REELS" if m.get("media_product_type") == "REELS" else "FEED"
        r = try_call(f"{m['id']}/insights", metric=METRICS[kind], access_token=token)
        if not r:
            continue
        v = {d["name"]: (d.get("values") or [{}])[0].get("value", (d.get("total_value") or {}).get("value")) for d in r["data"]}
        row = {"取得日": today, "投稿日時": m["timestamp"][:16].replace("T", " "), "種類": "リール" if kind == "REELS" else "フィード",
               "フォロワー数": me.get("followers_count"), "最初の行": (m.get("caption") or "").split("\n")[0][:40],
               "リンク": m.get("permalink", ""), "id": m["id"]}
        row.update({k: v[k] for k in COLS if k in v})
        rows.append(row)
        ok += 1
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLS)
    w.writeheader()
    w.writerows(rows)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(crypt(buf.getvalue(), decrypt=False) + "\n")
    print(f"@{me.get('username')} フォロワー {me.get('followers_count')}：{len(items)} 件中 {ok} 件の数字を記録しました")
    if items and ok == 0:
        raise SystemExit("数字が1件も取れませんでした（合鍵に「インサイトを読む」許可がない可能性）")


if __name__ == "__main__":
    main()
