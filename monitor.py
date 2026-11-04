#!/usr/bin/env python3
"""
Мониторинг бесплатных веб-генераторов видео -> Telegram.
Версия с Groq API (бесплатно, без карты, работает в РФ).
Поиск: Google News RSS + DuckDuckGo News/Web + парсинг страниц -> анализ через Groq.
"""
import json
import os
import re
import html
import time
import feedparser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from ddgs import DDGS
import sys
from bs4 import BeautifulSoup
from openai import OpenAI

STATE_FILE = Path(__file__).with_name("state.json")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

SYSTEM_PROMPT = """Ты — аналитик рынка веб-генераторов видео на основе ИИ.
Твоя задача: проанализировать предоставленные данные (результаты поиска и тексты со страниц сервисов) и составить отчёт о НОВЫХ платформах и ЗАМЕТНЫХ изменениях условий.

КРИТЕРИИ ОТБОРА (все обязательны):
- работает напрямую в браузере, без Discord;
- есть бесплатный старт: кредиты, пробные генерации, бесплатный тариф или пробный период (free trial);
- не требуется привязка банковской карты для бесплатного старта;
- это реальный запуск или изменение лимитов, а не просто статья-обзор.

ОТДЕЛЬНЫЕ ТРИГГЕРЫ (следи за ними особо внимательно):
1. Google NotebookLM — любые обновления, особенно добавление генерации видео, аудио-обзоров, новых функций. Даже если это не "новый сервис", а апдейт существующего — сообщи.
2. Доступность в России — для каждого сервиса отмечай, работает ли он в РФ напрямую, через VPN, или заблокирован. Это важная информация для пользователя.

ПРАВИЛА:
- Не повторяй сервисы из списка уже отправленных, если по ним нет СУЩЕСТВЕННЫХ изменений (например, изменились лимиты, добавился триал, изменились условия, появилась информация о доступности в РФ).
- Если ничего нового нет, верни has_news=false.
- Пиши на русском языке.
- В поле "details" пиши развёрнутые абзацы (3-5 предложений) по каждому сервису: что это, как работает, какие условия, что изменилось, доступна ли платформа в России. Стиль — как в аналитическом отчёте.

Ответ верни СТРОГО одним JSON-объектом без пояснений и без markdown-ограждений:
{
  "has_news": true,
  "headline": "Свежие изменения в условиях и лимитах веб-платформ",
  "summary": "2-3 предложения о том, что изменилось на рынке",
  "items": [
    {
      "name": "название сервиса",
      "url": "официальная ссылка",
      "free_limits": "бесплатные лимиты, например: 1000 кредитов при регистрации",
      "notes": "особенности, сроки квот и доступность в России (1-2 предложения)",
      "change": "new|updated",
      "access_type": "free|trial|trial+free"
    }
  ],
  "details": [
    "**Запуск НазваниеСервиса:** развёрнутый абзац описания с упоминанием доступности в РФ...",
    "**Обновление NotebookLM:** развёрнутый абзац о новых функциях..."
  ]
}"""


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"seen": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def extract_page_text(url: str, max_chars: int = 3000) -> str:
    """Парсит страницу и извлекает основной текст."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
            tag.decompose()
        texts = []
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
            t = tag.get_text(strip=True)
            if t and len(t) > 20:
                texts.append(t)
        result = "\n".join(texts)
        return result[:max_chars]
    except Exception as e:
        return f"[Ошибка парсинга: {e}]"


def search_and_parse() -> list[dict]:
    """Ищет в Google News RSS, DuckDuckGo News и Web, парсит страницы результатов."""
    year = datetime.now().year
    results = []
    seen_urls = set()

    # 1. Google News RSS (Самые свежие пресс-релизы и новости, обновляется в реальном времени)
    news_queries = [
        "AI video generator free browser",
        "Google NotebookLM update video",
        "AI video generator Russia available",
        "Adobe Firefly Video free",
        "EaseMate AI video",
    ]
    for q in news_queries:
        try:
            url = f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl=ru&gl=RU&ceid=RU:ru"
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:  # Берем топ-5 по каждому запросу
                link = entry.get("link", "")
                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)
                results.append({
                    "title": entry.get("title", ""),
                    "snippet": entry.get("summary", ""),
                    "url": link,
                    "source": "google_news",
                })
            time.sleep(0.5)
        except Exception as e:
            print(f"Ошибка Google News RSS '{q}': {e}")

    # 2. DuckDuckGo News (Новостные статьи из СМИ и блогов)
    ddg_news_queries = [
        f"new free AI video generator browser no credit card {year}",
        f"Google NotebookLM video generation update {year}",
        f"AI video generator available Russia VPN {year}",
    ]
    for q in ddg_news_queries:
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.news(q, max_results=5))
            for h in hits:
                url = h.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append({
                    "title": h.get("title", ""),
                    "snippet": h.get("body", ""),
                    "url": url,
                    "source": "ddg_news",
                })
            time.sleep(1)
        except Exception as e:
            print(f"Ошибка DuckDuckGo News '{q}': {e}")

    # 3. DuckDuckGo Web (Обычный поиск для полноты картины: форумы, обзоры)
    ddg_web_queries = [
        f'free AI video generator browser "no credit card" {year}',
        f'бесплатный ИИ генератор видео браузер "без карты" {year}',
        f'NotebookLM new features video {year}',
    ]
    for q in ddg_web_queries:
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.text(q, max_results=5))
            for h in hits:
                url = h.get("href", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append({
                    "title": h.get("title", ""),
                    "snippet": h.get("body", ""),
                    "url": url,
                    "source": "ddg_web",
                })
            time.sleep(1)
        except Exception as e:
            print(f"Ошибка DuckDuckGo Web '{q}': {e}")

    print(f"   Всего найдено уникальных ссылок: {len(results)}")

    # 4. Парсим страницы (только первые 15 уникальных доменов, чтобы уложиться в лимиты времени GitHub)
    parsed_domains = set()
    parsed_count = 0
    for r in results:
        if parsed_count >= 15:
            r["page_text"] = ""
            continue
            
        d = domain(r["url"])
        if d in parsed_domains:
            r["page_text"] = ""
            continue
            
        parsed_domains.add(d)
        parsed_count += 1
        r["page_text"] = extract_page_text(r["url"])
        time.sleep(0.5)

    return results


def ask_groq(search_results: list[dict], seen: list[dict]) -> dict:
    today = datetime.now().strftime("%d.%m.%Y")
    seen_text = "\n".join(f"- {s['name']} ({s['domain']}), {s['date']}" for s in seen[-80:]) or "(пусто)"

    context_parts = []
    for i, r in enumerate(search_results, 1):
        part = f"{i}. {r['title']}\nURL: {r['url']}\nСниппет: {r['snippet']}"
        if r.get("page_text"):
            part += f"\nТекст со страницы:\n{r['page_text']}"
        context_parts.append(part)
    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""Сегодня {today}.

Уже отправленные сервисы (не предлагай их, если нет серьёзных изменений):
{seen_text}

Данные для анализа:
{context[:8000]}

Проанализируй данные и верни СТРОГО JSON согласно инструкции в системном промпте."""

    client = OpenAI(base_url=GROQ_BASE_URL, api_key=os.environ["GROQ_API_KEY"])
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=8000,
        response_format={"type": "json_object"},
    )

    text = response.choices[0].message.content
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Нет JSON в ответе Groq: {text[:500]}")
    return json.loads(match.group(0))


def esc(s: str) -> str:
    return html.escape(str(s or ""), quote=False)


ACCESS_TAGS = {
    "trial": {"new": "🆕 с пробным периодом", "updated": "🔄 с пробным периодом"},
    "trial+free": {"new": "🆕 бесплатно + триал", "updated": "🔄 бесплатно + триал"},
    "free": {"new": "🆕", "updated": "🔄"},
}


def format_message(data: dict) -> str:
    lines = [f"<b>{esc(data.get('headline', 'Свежие изменения в условиях и лимитах веб-платформ'))}</b>", ""]
    if data.get("summary"):
        lines += [esc(data["summary"]), ""]

    for it in data.get("items", []):
        change = it.get("change", "new")
        access = it.get("access_type", "free")
        tag = ACCESS_TAGS.get(access, ACCESS_TAGS["free"]).get(change, "🆕")

        name = esc(it.get("name"))
        url = it.get("url", "")
        title = f'<a href="{html.escape(url, quote=True)}">{name}</a>' if url else name

        lines.append(f"{tag} — <b>{title}</b>")
        lines.append(f"🎁 {esc(it.get('free_limits'))}")
        if it.get("notes"):
            lines.append(f"ℹ️ {esc(it['notes'])}")
        lines.append("")

    if data.get("details"):
        lines.append("<b>Подробности последних обновлений в деталях</b>")
        lines.append("")
        for d in data["details"]:
            d_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(d))
            lines.append(f"• {d_html}")
            lines.append("")

    return "\n".join(lines).strip()


def send_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > 3800:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        chunks.append(cur)

    for chunk in chunks:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        r.raise_for_status()


def main() -> None:
    print("Запуск мониторинга...")
    state = load_state()

    print("1. Поиск и парсинг страниц...")
    search_results = search_and_parse()
    print(f"   Найдено {len(search_results)} результатов.")
    for r in search_results:
        print("  -", r.get("source"), "|", r.get("title", "")[:90], "|", r.get("url", "")[:80])
    print("2. Анализ через Groq...")
    try:
        data = ask_groq(search_results, state["seen"])
    except Exception as e:
        print(f"Ошибка при запросе к Groq: {e}")
        sys.exit(1)
    print(json.dumps(data, ensure_ascii=False)[:1500])
    if not data.get("has_news") or not data.get("items"):
        print("Новостей, соответствующих критериям, не найдено.")
        return

    seen_domains = {s["domain"] for s in state["seen"]}
    data["items"] = [
        it for it in data["items"]
        if it.get("change") == "updated" or domain(it.get("url", "")) not in seen_domains
    ]

    if not data["items"]:
        print("Все найденные сервисы уже отправлялись ранее.")
        return

    print("3. Отправка в Telegram...")
    send_telegram(format_message(data))

    today = datetime.now().strftime("%Y-%m-%d")
    for it in data["items"]:
        state["seen"].append({
            "name": it.get("name", ""),
            "domain": domain(it.get("url", "")),
            "date": today,
        })
    save_state(state)
    print(f"Успешно отправлено позиций: {len(data['items'])}")


if __name__ == "__main__":
    main()
