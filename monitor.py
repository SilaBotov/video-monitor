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
try:
    from googlenewsdecoder import gnewsdecoder
except Exception:
    gnewsdecoder = None
from openai import OpenAI

STATE_FILE = Path(__file__).with_name("state.json")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

SYSTEM_PROMPT = """Ты — аналитик рынка веб-генераторов видео на основе ИИ.
Задача: по предоставленным данным (результаты поиска и тексты страниц) составить отчёт о НОВЫХ платформах и ЗАМЕТНЫХ изменениях условий. Пиши на русском языке.

КРИТЕРИИ ОТБОРА (обязательные):
1. Работает напрямую в браузере на сайте. Инструменты только через Discord или Telegram-ботов исключаются.
2. Есть бесплатный тариф или триал: стартовые кредиты при регистрации, ежедневно возобновляемые квоты или безлимитные демо-режимы. Включай сервис при ЛЮБОМ бесплатном доступе, даже ограниченном. Привязку банковской карты не считай препятствием: если источник говорит, требуется ли она, укажи это в notes.
3. Это реальный запуск или изменение лимитов/условий, а не обзор, рейтинг или реклама.

ЧТО ФИКСИРОВАТЬ (только если это прямо указано в источниках):
- Объём бесплатных ресурсов: секунды, генерации или токены.
- Тип квоты: daily (обновление каждые 24 часа), trial (разовый приветственный пакет), rolling (скользящие циклы, например 5-часовые в экосистеме Google).
- Форматы: вертикальный 9:16 (Reels, Shorts, TikTok) и горизонтальный 16:9 (приоритет платформам с 9:16).
- Бесплатное разрешение: 480p, 720p, HD, Full HD.
- Водяные знаки на бесплатном тарифе: есть или нет.
- Режимы: Image-to-Video, Start & End Frame, ИИ-липсинк, нативный звук/речь, Consistent Characters.
- Флагманские модели внутри платформы: Kling, Sora, Veo, Seedance, Wan, MiniMax и аналогичные новые.
- Доступность в России (напрямую, через VPN, заблокирован) — только если источник это говорит.

СПЕЦИАЛЬНЫЙ ТРИГГЕР — Google NotebookLM:
Сообщай о NotebookLM ТОЛЬКО если появился полноценный художественный видеогенератор по свободному текстовому промпту с нуля, независимый от загруженных документов (в Studio Panel или в коде сервиса). Видеообзоры (Video Overviews) по заметкам и документам, регистрация товарных знаков, переименования и прочие обновления — НЕ триггер.

ПРАВИЛА ДОСТОВЕРНОСТИ:
- Используй только факты, явно указанные в источниках. Если параметр не указан, пиши «не указано» и не домысливай. Не делай выводов о намерениях компаний и о будущей доступности.
- Не указывай страну происхождения сервиса или компании, если источник прямо её не называет.
- В summary не утверждай про бесплатные тарифы, если для сервиса в items лимиты не указаны.
- Не пиши «без ограничений» и «безлимит», если источник прямо не говорит об отсутствии лимитов на количество генераций.
- Если источник — платный пресс-релиз или рекламный текст (например, businessinsider.com/news/stocks, prnewswire, globenewswire), добавь в notes: «заявление самого сервиса, не проверено».
- Не повторяй сервисы из списка уже отправленных, если нет СУЩЕСТВЕННЫХ изменений (лимиты, триал, условия, режимы, модели).
- Если ничего подходящего нет, верни {"has_news": false}.
- В "details" — по абзацу (3-5 предложений) на каждый сервис: что это, условия, что изменилось. Стиль аналитического отчёта.

Ответ верни СТРОГО одним JSON-объектом без пояснений и без markdown-ограждений:
{
  "has_news": true,
  "headline": "Свежие изменения в условиях и лимитах веб-платформ",
  "summary": "2-3 предложения о том, что изменилось на рынке",
  "items": [
    {
      "name": "название сервиса",
      "url": "официальная ссылка или ссылка на источник",
      "official_url": "адрес официального сайта сервиса, только если он явно указан в источниках, иначе пустая строка",
      "free_limits": "объём бесплатных ресурсов (секунды/генерации/токены)",
      "quota_type": "daily|trial|rolling|не указано",
      "resolution": "480p|720p|HD|Full HD|не указано",
      "formats": "9:16, 16:9 или не указано",
      "watermark": "есть|нет|не указано",
      "modes": ["Image-to-Video", "Start & End Frame", "липсинк", "нативный звук", "Consistent Characters"],
      "models": ["Veo", "Kling"],
      "notes": "особенности, доступность в РФ, пометки о достоверности (1-2 предложения)",
      "change": "new|updated",
      "access_type": "free|trial|trial+free"
    }
  ],
  "details": [
    "**Запуск НазваниеСервиса:** развёрнутый абзац..."
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


# Слова-мусор: такие результаты отбрасываем до отправки в модель
JUNK_RE = re.compile(
    r"\b(porn\w*|порно\w*|nsfw|hentai|хент\w*|sex|секс\w*|xxx|samsung|oneplus|oppo|смартфон\w*|trademarks?|товарн\w+\s+знак\w*)\b",
    re.IGNORECASE,
)


def is_junk(title: str, snippet: str = "") -> bool:
    return bool(JUNK_RE.search(f"{title} {snippet}"))


VIDEO_RE = re.compile(
    r"video|видео|ролик|clip|анимац|animat|генерац|generat|sora|veo|kling|seedance|runway|firefly|omni",
    re.IGNORECASE,
)


def is_relevant(title: str, snippet: str = "") -> bool:
    """Оставляем только материалы, где вообще речь о видео или генерации."""
    return bool(VIDEO_RE.search(f"{title} {snippet}"))


def clean_html(text: str) -> str:
    return BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)


def resolve_google_link(link: str) -> str:
    """Google News отдаёт ссылки-редиректы. Пробуем получить настоящий адрес статьи."""
    if "news.google.com" not in link:
        return link
    if gnewsdecoder is None:
        print("   googlenewsdecoder не установлен: ссылки Google News не раскрываются")
        return link
    try:
        res = gnewsdecoder(link, interval=1)
        if res.get("status") and res.get("decoded_url"):
            return res["decoded_url"]
        print(f"   Ссылка Google News не раскрыта: {str(res)[:150]}")
    except Exception as e:
        print(f"   Не удалось раскрыть ссылку Google News: {e}")
    return link


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
        print(f"   Не удалось прочитать {url[:70]}: {e}")
        return ""


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
        "new AI video platform launch free credits",
        "free AI video generator launch",
        "AI video generator rebrand",
        "AI video generator launches free credits sign up",
        "AI video studio launch free trial",
    ]
    for q in news_queries:
        try:
            url = f"https://news.google.com/rss/search?q={requests.utils.quote(q + ' when:7d')}&hl=ru&gl=RU&ceid=RU:ru"
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:  # Берем топ-5 по каждому запросу
                link = entry.get("link", "")
                title = entry.get("title", "")
                if not link or link in seen_urls or is_junk(title) or not is_relevant(title, clean_html(entry.get("summary", ""))):
                    continue
                seen_urls.add(link)
                real_link = resolve_google_link(link)
                if real_link in seen_urls and real_link != link:
                    continue
                seen_urls.add(real_link)
                results.append({
                    "title": title,
                    "snippet": clean_html(entry.get("summary", ""))[:300],
                    "url": real_link,
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
        f"AI video generator rebrand launch free credits {year}",
    ]
    for q in ddg_news_queries:
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.news(q, max_results=5, timelimit="w"))
            for h in hits:
                url = h.get("url", "")
                if not url or url in seen_urls or is_junk(h.get("title", ""), h.get("body", "")) \
                        or not is_relevant(h.get("title", ""), h.get("body", "")):
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
                hits = list(ddgs.text(q, max_results=5, timelimit="m"))
            for h in hits:
                url = h.get("href", "")
                if not url or url in seen_urls or is_junk(h.get("title", ""), h.get("body", "")) \
                        or not is_relevant(h.get("title", ""), h.get("body", "")):
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

    # Тексты статей не читаем: условия проверяет второй этап по официальным сайтам сервисов.
    for r in results:
        r["page_text"] = ""

    return results


def call_groq(system: str, user: str, max_tokens: int = 4000) -> dict:
    """Один запрос к Groq, ответ — JSON. При превышении лимита ждёт и повторяет один раз."""
    client = OpenAI(base_url=GROQ_BASE_URL, api_key=os.environ["GROQ_API_KEY"])
    response = None
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            break
        except Exception as e:
            if attempt == 0 and "429" in str(e):
                print("   Лимит Groq, жду 30 секунд и повторяю...")
                time.sleep(30)
                continue
            raise
    text = response.choices[0].message.content
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Нет JSON в ответе Groq: {text[:500]}")
    return json.loads(match.group(0))


def ask_groq(search_results: list[dict], seen: list[dict]) -> dict:
    today = datetime.now().strftime("%d.%m.%Y")
    seen_text = "\n".join(f"- {s['name']} ({s['domain']}), {s['date']}" for s in seen[-80:]) or "(пусто)"

    context_parts = []
    for i, r in enumerate(search_results, 1):
        part = f"{i}. {r['title']}\nURL: {r['url']}\nСниппет: {str(r['snippet'])[:300]}"
        if r.get("page_text"):
            part += f"\nТекст со страницы:\n{r['page_text'][:900]}"
        context_parts.append(part)
    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""Сегодня {today}.

Уже отправленные сервисы (не предлагай их, если нет серьёзных изменений):
{seen_text}

Данные для анализа:
{context[:9000]}

Проанализируй данные и верни СТРОГО JSON согласно инструкции в системном промпте."""

    return call_groq(SYSTEM_PROMPT, prompt, 8000)


# ---------- Этап 2: проверка условий на официальном сайте сервиса ----------

NOT_OFFICIAL = (
    "news.google.com", "google.com", "youtube.com", "youtu.be", "reddit.com", "habr.com", "vc.ru",
    "businessinsider.com", "techcrunch.com", "theverge.com", "medium.com", "msn.com", "wikipedia.org",
    "twitter.com", "x.com", "facebook.com", "linkedin.com", "t.me", "tiktok.com", "github.com",
    "producthunt.com", "aiservicesindex.com", "tenchat.ru", "dzen.ru", "yandex.ru", "bing.com",
)

VERIFY_PROMPT = """Ты проверяешь условия бесплатного доступа AI-видеогенератора ТОЛЬКО по текстам с его собственного сайта.
Используй исключительно факты из предоставленных текстов. Если параметр не указан, пиши «не указано». Ничего не домысливай и не указывай страну происхождения.
Верни СТРОГО один JSON-объект:
{
  "free_access": "да|нет|не указано",
  "free_limits": "объём бесплатных ресурсов: кредиты, секунды, генерации (кратко)",
  "quota_type": "daily|trial|rolling|не указано",
  "resolution": "480p|720p|HD|Full HD|не указано",
  "formats": "9:16, 16:9 или не указано",
  "watermark": "есть|нет|не указано",
  "modes": ["Image-to-Video", "Start & End Frame", "липсинк", "нативный звук", "Consistent Characters"],
  "models": ["названия видеомоделей, доступных в сервисе"],
  "card_required": "да|нет|не указано",
  "russia": "напрямую|через VPN|заблокирован|не указано"
}"""


def is_official_candidate(host: str) -> bool:
    return not any(host == d or host.endswith("." + d) for d in NOT_OFFICIAL)


def find_official_site(item: dict) -> str:
    hint = item.get("official_url") or ""
    if hint.startswith("http") and is_official_candidate(domain(hint)):
        p = urlparse(hint)
        return f"{p.scheme}://{p.netloc}"
    name = item.get("name", "")
    words = re.findall(r"[A-Za-z0-9]+", name)
    token = words[0].lower() if words else ""
    if len(token) < 3:
        return ""
    for q in (f"{name} official site", f"{name} pricing free credits", name):
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.text(q, max_results=8))
        except Exception as e:
            print(f"   Поиск сайта '{q}' не удался: {e}")
            time.sleep(2)
            continue
        for h in hits:
            u = h.get("href", "")
            host = domain(u)
            if u and is_official_candidate(host) and token in host.replace("-", ""):
                p = urlparse(u)
                return f"{p.scheme}://{p.netloc}"
        time.sleep(1)
    # запасной вариант: пробуем типичные адреса и проверяем, что на странице упомянуто название
    for tld in (".com", ".ai", ".io", ".video", ".app"):
        site = f"https://{token}{tld}"
        text = extract_page_text(site, max_chars=1500)
        if len(text) > 150 and token in text.lower():
            return site
    return ""


def fetch_site_texts(site: str) -> str:
    parts = []
    for path in ["", "/pricing", "/plans", "/price"]:
        u = site.rstrip("/") + path
        t = extract_page_text(u, max_chars=2500)
        if len(t) > 150:
            parts.append(f"[{u}]\n{t}")
        time.sleep(0.5)
        if len(parts) >= 3:
            break
    return "\n\n".join(parts)


def verify_items(items: list[dict]) -> None:
    """Для каждого сервиса пытаемся подтвердить условия по его официальному сайту."""
    for it in items:
        name = it.get("name", "")
        notes = [it.get("notes", "")] if it.get("notes") else []
        verified = False
        try:
            site = find_official_site(it)
            if not site:
                print(f"   {name}: официальный сайт не найден")
            else:
                text = fetch_site_texts(site)
                if len(text) < 300:
                    print(f"   {name}: на {site} мало текста (возможно, страница грузится скриптами)")
                    it["url"] = site
                else:
                    data = call_groq(VERIFY_PROMPT, f"Сервис: {name}\nСайт: {site}\n\n{text[:7000]}", 1500)
                    time.sleep(3)
                    it["url"] = site
                    for key in ("free_limits", "quota_type", "resolution", "formats", "watermark"):
                        v = data.get(key)
                        if v and v != "не указано":
                            it[key] = v
                    for key in ("modes", "models"):
                        if data.get(key):
                            it[key] = data[key]
                    if data.get("card_required") == "нет":
                        notes.append("Привязка карты не требуется.")
                    elif data.get("card_required") == "да":
                        notes.append("Требуется привязка карты.")
                    if data.get("russia") and data["russia"] != "не указано":
                        notes.append(f"Доступность в РФ: {data['russia']}.")
                    if data.get("free_access") == "нет":
                        notes.append("На сайте бесплатный доступ не обнаружен.")
                    verified = True
        except Exception as e:
            print(f"   Проверка '{name}' не удалась: {e}")
        notes.append("Условия проверены на официальном сайте." if verified else "Условия не проверены на сайте сервиса.")
        it["notes"] = " ".join(n.strip() for n in notes if n)


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
        quota = {"daily": "ежедневное обновление", "trial": "разовый триал", "rolling": "скользящие циклы"}
        specs = []
        if it.get("quota_type") and it["quota_type"] != "не указано":
            specs.append(f"квота: {quota.get(it['quota_type'], it['quota_type'])}")
        if it.get("resolution") and it["resolution"] != "не указано":
            specs.append(f"разрешение: {it['resolution']}")
        if it.get("formats") and it["formats"] != "не указано":
            specs.append(f"форматы: {it['formats']}")
        if it.get("watermark") and it["watermark"] != "не указано":
            specs.append(f"водяной знак: {it['watermark']}")
        if specs:
            lines.append("⚙️ " + esc("; ".join(specs)))
        if it.get("modes"):
            lines.append("🎬 " + esc(", ".join(map(str, it["modes"]))))
        if it.get("models"):
            lines.append("🤖 " + esc(", ".join(map(str, it["models"]))))
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

    seen_names = {s.get("name", "").strip().lower() for s in state["seen"]}
    data["items"] = [
        it for it in data["items"]
        if it.get("change") == "updated" or it.get("name", "").strip().lower() not in seen_names
    ][:6]
    if not data["items"]:
        print("Все найденные сервисы уже отправлялись ранее.")
        return
    print("2.5 Проверка условий на сайтах сервисов...")
    verify_items(data["items"])

    seen_domains = {s["domain"] for s in state["seen"] if is_official_candidate(s["domain"])}
    data["items"] = [
        it for it in data["items"]
        if it.get("change") == "updated" or (
            domain(it.get("url", "")) not in seen_domains
            and it.get("name", "").strip().lower() not in seen_names
        )
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
