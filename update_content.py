from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sources.json"
DATA = ROOT / "data"
ARTICLES_PATH = DATA / "articles.json"
WEEKLY_PATH = DATA / "weekly.json"

KEYWORDS = {
    "Security": ["taiwan strait", "military", "defense", "defence", "blockade", "drill", "pla", "china taiwan"],
    "Semiconductor": ["semiconductor", "chip", "tsmc", "foundry", "hbm", "packaging", "wafer", "memory"],
    "Japan-Korea": ["japan korea", "korea japan", "japanese", "korean", "sk hynix", "samsung", "tokyo electron"],
    "Logistics": ["shipping", "logistics", "port", "freight", "transport", "resilience", "supply chain"],
}

JA_CAT = {"Security":"安全保障","Semiconductor":"半導体","Japan-Korea":"日韓連携","Logistics":"物流・レジリエンス","Other":"その他"}
KO_CAT = {"Security":"안보","Semiconductor":"반도체","Japan-Korea":"한일협력","Logistics":"물류·회복력","Other":"기타"}


def load_json(path: Path, default: Any):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text(value: str | None) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def stable_id(url: str, title: str) -> str:
    base = (url or title).strip().lower().encode("utf-8")
    return hashlib.sha256(base).hexdigest()[:16]


def parse_date(entry: Any) -> str:
    for field in ("published_parsed", "updated_parsed"):
        t = getattr(entry, field, None)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc).date().isoformat()
    return datetime.now(timezone.utc).date().isoformat()


def classify(title: str, summary: str, fallback: str) -> str:
    text = f"{title} {summary}".lower()
    scores = {cat: sum(1 for kw in kws if kw in text) for cat, kws in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else fallback


def source_name(entry: Any, feed_name: str) -> str:
    source = getattr(entry, "source", None)
    if isinstance(source, dict) and source.get("title"):
        return clean_text(source["title"])
    if getattr(entry, "author", None):
        return clean_text(entry.author)
    return feed_name.replace("Google News — ", "")


def is_http_url(url: str) -> bool:
    try:
        return urlparse(url).scheme in {"http", "https"}
    except Exception:
        return False


def ingest() -> list[dict[str, Any]]:
    cfg = load_json(CONFIG, {})
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=int(cfg.get("lookback_days", 10)))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    headers = {"User-Agent":"STRAIT-RESILIENCE/1.0 (+public-source-research)"}

    for feed in cfg.get("feeds", []):
        try:
            r = requests.get(feed["url"], headers=headers, timeout=25)
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
        except Exception as exc:
            print(f"WARN feed failed: {feed.get('name')}: {exc}", file=sys.stderr)
            continue

        for e in parsed.entries:
            title = clean_text(getattr(e, "title", ""))
            summary = clean_text(getattr(e, "summary", ""))
            url = getattr(e, "link", "") or ""
            if not title or not is_http_url(url):
                continue
            date = parse_date(e)
            try:
                if datetime.fromisoformat(date).date() < cutoff:
                    continue
            except ValueError:
                pass
            sid = stable_id(url, title)
            if sid in seen:
                continue
            seen.add(sid)
            category = classify(title, summary, feed.get("default_category", "Other"))
            rows.append({
                "id": sid,
                "date": date,
                "source": source_name(e, feed.get("name", "Public source")),
                "source_lang": "EN",
                "category": category,
                "url": url,
                "original_title": title,
                "original_summary": summary[:900],
                "tags": [],
            })

    rows.sort(key=lambda x: (x["date"], x["id"]), reverse=True)
    return rows[: int(cfg.get("max_articles", 18))]


def get_client():
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(api_key=key)


def ai_localize(client, article: dict[str, Any]) -> dict[str, Any]:
    if client is None:
        # Safe fallback keeps the pipeline operational without fabricating translations.
        title = article["original_title"]
        summary = article.get("original_summary") or "Source summary unavailable."
        return {
            **article,
            "ja_title": title,
            "ko_title": title,
            "ja_summary": summary[:360],
            "ko_summary": summary[:360],
            "tags": [article["category"]],
            "translation_status": "pending_api_key",
        }

    prompt = {
        "task":"Translate and summarize one public-source news item for a Taiwan Strait / semiconductor supply-chain research dashboard.",
        "rules":[
            "Do not invent facts beyond the supplied title/summary.",
            "Japanese and Korean summaries: 2 concise sentences each, about 90-180 characters.",
            "Titles should read like neutral professional news headlines.",
            "Return 2-4 short tags in English or widely recognized company names.",
            "Output JSON only."
        ],
        "article": {
            "title":article["original_title"],
            "summary":article.get("original_summary", ""),
            "source":article["source"],
            "date":article["date"],
            "category":article["category"],
        },
        "schema":{"ja_title":"","ko_title":"","ja_summary":"","ko_summary":"","tags":[]}
    }
    resp = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        input=json.dumps(prompt, ensure_ascii=False),
    )
    text = resp.output_text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    data = json.loads(text)
    return {**article, **data, "translation_status":"complete"}


def merge_and_translate(fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old_rows = load_json(ARTICLES_PATH, [])
    old = {x.get("id"): x for x in old_rows if x.get("id")}
    client = get_client()
    out = []
    for a in fresh:
        existing = old.get(a["id"])
        if existing and existing.get("translation_status") in {"complete", "demo_complete", "seed"}:
            merged = {**existing, **{k:a[k] for k in ["date","source","category","url","original_title","original_summary"] if k in a}}
            out.append(merged)
            continue
        try:
            out.append(ai_localize(client, a))
        except Exception as exc:
            print(f"WARN translation failed for {a['id']}: {exc}", file=sys.stderr)
            out.append(ai_localize(None, a))
    return out


def build_weekly(client, articles: list[dict[str, Any]]) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=6)
    period = f"{start.isoformat()} — {today.isoformat()}"
    recent = [a for a in articles if a.get("date", "") >= start.isoformat()]
    if not recent:
        recent = articles[:8]

    if client is None:
        # Demo-safe fallback: preserve the curated bilingual brief instead of
        # overwriting it with an API-key warning during a scheduled run.
        existing = load_json(WEEKLY_PATH, {})
        if existing.get("ja") and existing.get("ko"):
            return {**existing, "period": period, "generated_at": datetime.now(timezone.utc).isoformat(), "status": "demo"}
        return {
            "period": period,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status":"demo",
            "ja": {"headline":"供給網の変化を4つのSignalで整理","summary":"公開情報をもとにしたデモ用Weekly Briefです。API接続後は同じ形式で自動生成されます。","points":[]},
            "ko": {"headline":"공급망 변화를 4개의 Signal로 정리","summary":"공개정보 기반 데모용 Weekly Brief · API 연결 후 동일 형식 자동 생성","points":[]}
        }

    compact = [{k:a.get(k) for k in ["date","source","category","original_title","ja_summary","ko_summary"]} for a in recent[:14]]
    prompt = {
        "task":"Create a bilingual weekly intelligence brief from supplied news items.",
        "rules":["Use only supplied items.","Neutral analytical tone.","Explain implications for Taiwan Strait risk and Japan-Korea semiconductor resilience.","Exactly four points in each language.","Output JSON only."],
        "period":period,
        "articles":compact,
        "schema":{"ja":{"headline":"","summary":"","points":[["label","text"]]},"ko":{"headline":"","summary":"","points":[["label","text"]]}}
    }
    resp = client.responses.create(model=os.getenv("OPENAI_MODEL", "gpt-5-mini"), input=json.dumps(prompt, ensure_ascii=False))
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.output_text.strip(), flags=re.S)
    d = json.loads(text)
    return {"period":period,"generated_at":datetime.now(timezone.utc).isoformat(),"status":"complete",**d}


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    fresh = ingest()
    if not fresh:
        print("WARN no fresh feed items; preserving existing articles", file=sys.stderr)
        articles = load_json(ARTICLES_PATH, [])
    else:
        articles = merge_and_translate(fresh)
        ARTICLES_PATH.write_text(json.dumps(articles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    weekly = build_weekly(get_client(), articles)
    WEEKLY_PATH.write_text(json.dumps(weekly, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"articles={len(articles)} weekly_status={weekly.get('status')}")

if __name__ == "__main__":
    main()
