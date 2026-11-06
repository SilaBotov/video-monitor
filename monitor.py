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
from urllib.parse import urlparse, urljoin

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
# False: сервисы, найденные только обычным веб-поиском (лендинги, каталоги, рейтинги), в пост не попадают.
# Новостью считаются только материалы из Google News и новостного поиска DuckDuckGo. Поставь True, чтобы вернуть прежнее поведение.
WEB_RESULTS_CAN_BE_NEWS = False

SYSTEM_PROMPT = """Ты — аналитик рынка веб-генераторов видео на основе ИИ.
Задача: по предоставленным данным (результаты поиска и тексты страниц) составить отчёт о НОВЫХ платформах и ЗАМЕТНЫХ изменениях условий. Пиши на русском языке.

КРИТЕРИИ ОТБОРА (обязательные):
1. Работает напрямую в браузере на сайте. Инструменты только через Discord или Telegram-ботов исключаются.
2. Есть бесплатный тариф или триал: стартовые кредиты при регистрации, ежедневно возобновляемые квоты или безлимитные демо-режимы. Включай сервис при ЛЮБОМ бесплатном доступе, даже ограниченном. Привязку банковской карты не считай препятствием: если источник говорит, требуется ли она, укажи это в notes.
3. Это реальный запуск или изменение лимитов/условий, а не обзор, рейтинг или реклама.

ЧТО ФИКСИРОВАТЬ (только если это прямо указано в источниках):
- Объём бесплатных ресурсов: секунды, генерации или токены.
- Тип квоты: daily (обновление каждые 24 часа), monthly (ежемесячное обновление), trial (разовый приветственный пакет), rolling (скользящие циклы, например 5-часовые в экосистеме Google).
- Форматы: вертикальный 9:16 (Reels, Shorts, TikTok) и горизонтальный 16:9 (приоритет платформам с 9:16).
- Бесплатное разрешение: 480p, 720p, HD, Full HD.
- Водяные знаки на бесплатном тарифе: есть или нет.
- Режимы: Image-to-Video, Start & End Frame, ИИ-липсинк, нативный звук/речь, Consistent Characters.
- Флагманские модели внутри платформы: Kling, Sora, Veo, Seedance, Wan, MiniMax и аналогичные новые.
- Доступность в России (напрямую, через VPN, заблокирован) — только если источник это говорит.

СПЕЦИАЛЬНЫЙ ТРИГГЕР — Google NotebookLM:
Сообщай о NotebookLM ТОЛЬКО если появился полноценный художественный видеогенератор по свободному текстовому промпту с нуля, независимый от загруженных документов (в Studio Panel или в коде сервиса). Видеообзоры (Video Overviews) по заметкам и документам, регистрация товарных знаков, переименования и прочие обновления — НЕ триггер.

ПРАВИЛА ДОСТОВЕРНОСТИ:
- Используй только факты, явно указанные в источниках. Если параметр не указан, ставь «не указано» в полях карточки и не домысливай; в текстах (summary, notes, details) недостающую информацию просто не упоминай.
- В текстах никогда не ссылайся на «статью», «источник», «публикацию» или «материал»: не пиши «в статье отмечается», «по данным источника», «в источнике нет информации». Излагай факты напрямую, от своего лица. Не делай выводов о намерениях компаний и о будущей доступности.
- Не указывай страну происхождения сервиса или компании, если источник прямо её не называет.
- В summary не утверждай про бесплатные тарифы, если для сервиса в items лимиты не указаны.
- Обзоры, рейтинги и подборки («лучшие», «топ-N», «протестировали», сравнения) НЕ являются новостью о запуске. Не включай сервис только потому, что он упомянут в такой подборке. Включай сервис, только если материал сообщает именно о его запуске, ребрендинге, новой версии или изменении условий. Значение change=new ставь только для реального запуска сервиса.
- Источники с пометкой «СПРАВОЧНАЯ СТРАНИЦА» (лендинги сервисов, каталоги, рейтинги) не подтверждают, что сервис новый: по ним нельзя создавать карточки и ставить change=new.
- Если в материале указана только платная цена (например, оплата за секунду видео или подписка) и бесплатный доступ не упомянут, не включай сервис. Выпуск новой модели без бесплатного доступа новостью для этого канала не является.
- Все текстовые поля (free_limits, notes, details) пиши по-русски, даже если источник на английском.
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
      "quota_type": "daily|monthly|trial|rolling|не указано",
      "resolution": "480p|720p|HD|Full HD|не указано",
      "formats": "9:16, 16:9 или не указано",
      "watermark": "есть|нет|не указано",
      "modes": ["функции строго из списка: Image-to-Video, Start & End Frame, липсинк, нативный звук, Consistent Characters, Text-to-Video. Другие функции пиши кратко по-русски, без английских названий"],
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


def as_list(v) -> list:
    """Приводит значение к списку строк и убирает «не указано»."""
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    out = []
    for x in v:
        x = str(x).strip()
        if x and x.lower() not in ("не указано", "не указаны", "none", "-"):
            out.append(x)
    return out


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
        if (res.get("status") or res.get("success")) and res.get("decoded_url"):
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


def ddg_call(fn, q: str, timelimit: str) -> list:
    """Поиск DuckDuckGo: сначала с фильтром по дате, при неудаче — без него."""
    try:
        return list(fn(q, max_results=5, timelimit=timelimit))
    except Exception as e:
        print(f"   DuckDuckGo: '{q[:45]}' с фильтром по дате не удался ({e}), пробую без него")
        time.sleep(2)
        return list(fn(q, max_results=5))


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
                hits = ddg_call(ddgs.news, q, "w")
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
                hits = ddg_call(ddgs.text, q, "m")
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
        kind = "НОВОСТЬ" if r.get("source") in ("google_news", "ddg_news") else "СПРАВОЧНАЯ СТРАНИЦА (не новость)"
        part = f"{i}. [{kind}] {r['title']}\nURL: {r['url']}\nСниппет: {str(r['snippet'])[:300]}"
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
    "producthunt.com", "aiservicesindex.com", "aitoolnet.com", "toolify.ai", "futurepedia.io", "theresanaiforthat.com", "aitools.fyi", "toolradar.com", "feedough.com", "felloai.com", "pexo.ai", "tenchat.ru", "dzen.ru", "yandex.ru", "bing.com",
)

VERIFY_PROMPT = """Ты проверяешь условия бесплатного доступа AI-видеогенератора ТОЛЬКО по текстам с его собственного сайта.
Используй исключительно факты из предоставленных текстов. Если параметр не указан, пиши «не указано». Ничего не домысливай и не указывай страну происхождения.
Верни СТРОГО один JSON-объект:
{
  "official_site": "да, если сайт принадлежит самому сервису или компании-разработчику; нет, если это сторонний сайт, фан-сайт, перепродавец чужих моделей, каталог или в тексте есть оговорка вроде «не аффилирован», «unofficial», «independent»",
  "free_access": "да|нет|не указано",
  "free_limits": "бесплатные лимиты именно на ГЕНЕРАЦИЮ ВИДЕО: кредиты, секунды, генерации (кратко, по-русски). Если на сайте указаны лимиты только для изображений или текста, напиши «для видео не указано (для изображений: ...)»",
  "quota_type": "daily|monthly|trial|rolling|не указано",
  "resolution": "480p|720p|HD|Full HD|не указано",
  "formats": "9:16, 16:9 или не указано",
  "watermark": "есть|нет|не указано",
  "modes": ["функции строго из списка: Image-to-Video, Start & End Frame, липсинк, нативный звук, Consistent Characters, Text-to-Video. Другие функции пиши кратко по-русски, без английских названий"],
  "models": ["названия видеомоделей, доступных в сервисе"],
  "card_required": "да|нет|не указано",
  "russia": "напрямую|через VPN|заблокирован|не указано"
}"""


def is_official_candidate(host: str) -> bool:
    return not any(host == d or host.endswith("." + d) for d in NOT_OFFICIAL)


KNOWN_SITES = [
    (r"\bgemini\b|\bveo\b|\bomni\b", "https://gemini.google.com"),
    (r"notebook\s?lm", "https://notebooklm.google.com"),
    (r"google vids|\bvids\b", "https://workspace.google.com/products/vids"),
    (r"\bsora\b", "https://sora.com"),
    (r"firefly", "https://firefly.adobe.com"),
    (r"\bkling\b", "https://klingai.com"),
    (r"\brunway\b", "https://runwayml.com"),
    (r"minimax|hailuo", "https://www.minimax.io"),
    (r"dreamina|capcut", "https://dreamina.capcut.com"),
    (r"\bpika\b", "https://pika.art"),
    (r"\bluma\b", "https://lumalabs.ai"),
]


def find_official_site(item: dict) -> str:
    low = item.get("name", "").lower()
    for pattern, url in KNOWN_SITES:
        if re.search(pattern, low):
            return url
    return find_site_by_search(item)


def find_site_by_search(item: dict) -> str:
    name = item.get("name", "")
    words = re.findall(r"[A-Za-z0-9]+", name)
    token = words[0].lower() if words else ""
    hint = item.get("official_url") or ""
    if hint.startswith("http") and is_official_candidate(domain(hint)) and (not token or token in domain(hint).replace("-", "")):
        p = urlparse(hint)
        return f"{p.scheme}://{p.netloc}"
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


PRICE_RE = re.compile(r"pric|plan|subscri|credit|тариф|цен", re.IGNORECASE)


def find_price_links(site: str, limit: int = 2) -> list[str]:
    """Ищет на главной странице ссылки на тарифы, кредиты и планы."""
    try:
        r = requests.get(site, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []
    host = domain(site)
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        label = a.get_text(" ", strip=True)
        if not (PRICE_RE.search(href) or PRICE_RE.search(label)):
            continue
        full = urljoin(site.rstrip("/") + "/", href).split("#")[0]
        if domain(full) != host or full.rstrip("/") == site.rstrip("/") or full in found:
            continue
        found.append(full)
        if len(found) >= limit:
            break
    return found


def fetch_site_texts(site: str) -> str:
    base = site.rstrip("/")
    urls = [base] + find_price_links(site) + [base + "/pricing", base + "/plans"]
    parts, tried = [], set()
    for u in urls:
        if u in tried:
            continue
        tried.add(u)
        t = extract_page_text(u, max_chars=2500)
        if len(t) > 150:
            parts.append(f"[{u}]\n{t}")
        time.sleep(0.5)
        if len(parts) >= 3:
            break
    return "\n\n".join(parts)


def merge_site_data(it: dict, data: dict, notes: list) -> None:
    found_limits = False
    for key in ("free_limits", "quota_type", "resolution", "formats", "watermark"):
        v = data.get(key)
        if v and v != "не указано":
            old = str(it.get(key, "")).strip().lower()
            if key == "free_limits" and v.lower().startswith("для видео не указано") and old not in ("", "не указано"):
                continue
            it[key] = v
            if key == "free_limits":
                found_limits = True
    for key in ("modes", "models"):
        vals = as_list(data.get(key))
        if vals:
            it[key] = vals
    it["free_access"] = data.get("free_access", "не указано")
    if data.get("card_required") == "нет":
        notes.append("Привязка карты не требуется.")
    elif data.get("card_required") == "да":
        notes.append("Требуется привязка карты.")
    if data.get("russia") and data["russia"] != "не указано":
        notes.append(f"Доступность в РФ: {data['russia']}.")
    if data.get("free_access") == "нет" and not found_limits:
        notes.append("На сайте бесплатный доступ не обнаружен.")


def verify_items(items: list[dict]) -> None:
    """Для каждого сервиса пытаемся подтвердить условия по его официальному сайту."""
    for it in items:
        name = it.get("name", "")
        old_notes = it.get("notes", "")
        notes = []
        verified = False
        try:
            site = find_official_site(it)
            if not site:
                print(f"   {name}: официальный сайт не найден")
            else:
                text = fetch_site_texts(site)
                print(f"   {name}: сайт {site}, прочитано {len(text)} символов")
                if len(text) < 300:
                    print(f"   {name}: мало текста (возможно, страница грузится скриптами)")
                    it["url"] = site
                else:
                    data = call_groq(VERIFY_PROMPT, f"Сервис: {name}\nСайт: {site}\n\n{text[:7000]}", 1500)
                    time.sleep(3)
                    if str(data.get("official_site", "да")).strip().lower().startswith("нет"):
                        print(f"   {name}: {site} похож на неофициальный сайт, данные с него не используются")
                    else:
                        it["url"] = site
                        merge_site_data(it, data, notes)
                        verified = True
        except Exception as e:
            print(f"   Проверка '{name}' не удалась: {e}")
        if not verified and old_notes:
            notes = [old_notes] + notes
        # если сайт проверен, заметка первого этапа могла противоречить данным сайта, поэтому берём только данные сайта
        it["notes"] = " ".join(n.strip() for n in notes if n)


ALLOWED_MODES = {"image-to-video", "start & end frame", "липсинк", "нативный звук", "consistent characters", "text-to-video"}


def has_data(it: dict) -> bool:
    """Есть ли подтверждение бесплатного доступа: лимиты, тип квоты или явное «да» с сайта."""
    for k in ("free_limits", "quota_type"):
        if str(it.get(k, "")).strip().lower() not in ("", "не указано", "для видео не указано"):
            return True
    return str(it.get("free_access", "")).strip().lower() == "да"


def mostly_latin(text: str) -> bool:
    lat = len(re.findall(r"[A-Za-z]", text or ""))
    cyr = len(re.findall(r"[А-Яа-яЁё]", text or ""))
    return lat > 12 and lat > cyr


TRANSLATE_PROMPT = """Переведи на русский язык значения полей, сохранив смысл и числа. Названия сервисов, моделей и технические термины (Image-to-Video, 720p, Full HD и т.п.) оставь как есть. Ничего не добавляй.
Верни СТРОГО один JSON-объект с теми же ключами, что во входных данных."""


def translate_items(items: list[dict]) -> None:
    """Если в карточке остался английский текст (лимиты, заметки), переводим его на русский."""
    todo = {}
    for i, it in enumerate(items):
        for key in ("free_limits", "notes"):
            if mostly_latin(str(it.get(key, ""))):
                todo[f"{i}:{key}"] = it[key]
        extra = [m for m in as_list(it.get("modes")) if m.lower() not in ALLOWED_MODES and re.search(r"[A-Za-z]", m) and not re.search(r"[А-Яа-яЁё]", m)]
        if extra:
            todo[f"{i}:modes"] = "; ".join(extra)
    if not todo:
        return
    try:
        res = call_groq(TRANSLATE_PROMPT, json.dumps(todo, ensure_ascii=False), 1500)
        for k, v in todo.items():
            i, key = k.split(":")
            if not (isinstance(res.get(k), str) and res[k].strip()):
                continue
            if key == "modes":
                keep = [m for m in as_list(items[int(i)].get("modes")) if m.lower() in ALLOWED_MODES or re.search(r"[А-Яа-яЁё]", m)]
                items[int(i)]["modes"] = keep + [x.strip() for x in res[k].split(";") if x.strip()]
            else:
                items[int(i)][key] = res[k].strip()
    except Exception as e:
        print(f"   Не удалось перевести английский текст: {e}")


REPORT_PROMPT = """Составь текст отчёта ТОЛЬКО по данным из предоставленного JSON со списком сервисов.
Не добавляй фактов, которых там нет. Не указывай страну происхождения сервисов и компаний. Параметры со значением «не указано» не упоминай вообще. Никогда не ссылайся на «статью», «источник» или «публикацию»: не пиши «в статье отмечается», «по данным источника». Излагай факты напрямую.
Утверждения о бесплатных кредитах, лимитах и отсутствии карты относи только к тем сервисам, у которых это есть в данных. Если данных о сервисе нет, не называй его и не пиши о нём в summary.
Не упоминай проверку условий и слова «на официальном сайте». Не пиши фраз вроде «не указано», «информация отсутствует», «детали не раскрыты»: если данных нет, просто ничего об этом не говори. Если у сервиса мало данных, опиши его одним коротким предложением. Не начинай абзац со слов «Название — новый сервис генерации видео»: сразу переходи к условиям и возможностям.
Пиши по-русски. Верни СТРОГО один JSON-объект:
{
  "summary": "2-3 предложения о том, что изменилось на рынке, без утверждений, которых нет в данных",
  "details": ["**Название:** абзац 3-5 предложений по каждому сервису: что это, бесплатные условия, форматы, режимы, модели"]
}"""


def rewrite_report(data: dict) -> None:
    """Переписываем summary и details по проверенным данным, чтобы текст не противоречил карточкам."""
    try:
        slim = []
        for it in data.get("items", []):
            d = {k: v for k, v in it.items() if k not in ("notes", "official_url", "url", "access_type")}
            d = {k: v for k, v in d.items() if v not in ("не указано", "", [], None)}
            notes = it.get("notes", "").strip()
            if notes:
                d["notes"] = notes
            slim.append(d)
        payload = json.dumps(slim, ensure_ascii=False)
        res = call_groq(REPORT_PROMPT, payload[:7000], 3000)
        if res.get("summary"):
            data["summary"] = res["summary"]
        if res.get("details"):
            data["details"] = res["details"]
    except Exception as e:
        print(f"   Не удалось переписать отчёт по проверенным данным: {e}")


def esc(s: str) -> str:
    return html.escape(str(s or ""), quote=False)


ACCESS_TAGS = {
    "trial": {"new": "🆕 с пробным периодом", "updated": "🔄 с пробным периодом"},
    "trial+free": {"new": "🆕 бесплатно + триал", "updated": "🔄 бесплатно + триал"},
    "free": {"new": "🆕", "updated": "🔄"},
}


SRC_VERBS = r"(?:отмечается|отмечено|сообщается|указано|указывается|говорится|написано|подчёркивается|упоминается|утверждается)"
SRC_NOUNS = r"(?:статье|источнику|материалу|публикации|источнике|тексте)"
SRC_START_RE = re.compile(
    r"(?P<pre>^|[.!?]\s+|\*\*\s*)(?:В|Согласно|По данным)\s+" + SRC_NOUNS + r"\s+" + SRC_VERBS + r"(?:,?\s*что)?[,:]?\s*(?P<c>\S)",
    re.IGNORECASE,
)
SRC_MID_RE = re.compile(
    r",?\s*(?:в|согласно|по данным)\s+" + SRC_NOUNS + r"\s+" + SRC_VERBS + r"(?:,?\s*что)?[,:]?\s*",
    re.IGNORECASE,
)


def clean_src(text: str) -> str:
    """Убирает обороты вроде «В статье отмечается, что»."""
    text = SRC_START_RE.sub(lambda m: m.group("pre") + m.group("c").upper(), str(text or ""))
    return SRC_MID_RE.sub(lambda m: ", " if m.group(0).lstrip().startswith(",") else " ", text)


UNKNOWN_RE = re.compile(r"не указан|не раскрыт|не уточнен|отсутству|нет (?:данных|информации)|неизвестн", re.IGNORECASE)


def drop_unknown(text: str) -> str:
    """Убирает фразы вроде «информация о лимитах не указана»."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", str(text or "")):
        clauses = [c for c in re.split(r";\s*", sent) if c.strip() and not UNKNOWN_RE.search(c)]
        if clauses:
            out.append("; ".join(c.strip().rstrip(".!?") for c in clauses) + ".")
    return " ".join(out)


def format_message(data: dict) -> str:
    lines = [f"<b>{esc(data.get('headline', 'Свежие изменения в условиях и лимитах веб-платформ'))}</b>", ""]
    if data.get("summary"):
        lines += [esc(drop_unknown(clean_src(data["summary"]))), ""]

    for it in data.get("items", []):
        change = it.get("change", "new")
        access = it.get("access_type", "free")
        tag = ACCESS_TAGS.get(access, ACCESS_TAGS["free"]).get(change, "🆕")

        name = esc(it.get("name"))
        url = it.get("url", "")
        title = f'<a href="{html.escape(url, quote=True)}">{name}</a>' if url else name

        lines.append(f"{tag} — <b>{title}</b>")
        if str(it.get("free_limits", "")).strip().lower() not in ("", "не указано", "для видео не указано"):
            lines.append(f"🎁 {esc(it.get('free_limits'))}")
        quota = {"daily": "ежедневное обновление", "trial": "разовый триал", "rolling": "скользящие циклы", "monthly": "ежемесячное обновление"}
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
        if as_list(it.get("modes")):
            lines.append("🎬 " + esc(", ".join(as_list(it["modes"]))))
        if as_list(it.get("models")):
            lines.append("🤖 " + esc(", ".join(as_list(it["models"]))))
        if it.get("notes"):
            lines.append(f"ℹ️ {esc(clean_src(it['notes']))}")
        lines.append("")

    if data.get("details"):
        lines.append("<b>Подробности последних обновлений в деталях</b>")
        lines.append("")
        for d in data["details"]:
            d_clean = drop_unknown(clean_src(d))
            if len(re.sub(r"\*\*.*?\*\*", "", d_clean).strip()) < 15:
                continue
            d_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(d_clean))
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
    if not WEB_RESULTS_CAN_BE_NEWS and data.get("items"):
        web_urls = {r["url"] for r in search_results if r.get("source") == "ddg_web"}
        kept = [it for it in data["items"] if it.get("url") not in web_urls]
        if len(kept) < len(data["items"]):
            dropped = [it.get("name", "?") for it in data["items"] if it.get("url") in web_urls]
            print(f"   Отсеяны сервисы, найденные только справочными страницами: {', '.join(dropped)}")
        data["items"] = kept
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

    translate_items(data["items"])
    empty = [it.get("name", "?") for it in data["items"] if not has_data(it)]
    if empty:
        print(f"   Пропущены сервисы без данных об условиях: {', '.join(empty)}")
        data["items"] = [it for it in data["items"] if has_data(it)]
    if not data["items"]:
        print("Нет сервисов с подтверждёнными данными для публикации.")
        return
    print("2.7 Обновление текста отчёта по проверенным данным...")
    rewrite_report(data)

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
