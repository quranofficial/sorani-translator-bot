#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =====================================================================
#  بۆتی وەرگێڕی کوردی سۆرانی  |  Kurdish Sorani Translator Bot
# =====================================================================
#  تواناکان / Features:
#   - دەق (text) ............ وەرگێڕان ڕاستەوخۆ بۆ کوردی سۆرانی
#   - PDF / DOCX / PPTX / TXT / SRT ... وەرگێڕانی فایل، دەرچووەکە هەمان جۆر
#        + کۆپی PDF بۆ هەموو فایلەکان (DOCX/PPTX بە LibreOffice بۆ باوەڕپێکراوی شێواز)
#   - ڤیدیۆ / دەنگ (فایل یان لینکی YouTube / Facebook / TikTok / ...)
#        دەنگ -> نووسین (faster-whisper) -> وەرگێڕان -> SRT + Word + PDF بە سۆرانی
#        + ژێرنووسی کوردی ڕاستەوخۆ لەسەر ڤیدیۆکە دادەنرێت (burn-in بە ffmpeg/libass)
#   - بزوێنەری وەرگێڕان: Google Translate (بەخۆڕایی) + GLM/Z.ai (یەدەگ)
#   - لە GitHub Actions ٢٤/٧ کار دەکات: هەر خولێک ٤٥ خولەکە و لە کۆتایی خولێکی نوێ
#     خۆکارانە دەست پێدەکات (زنجیرەی بێکۆتایی) + cron ی پاشەکەوت هەر ١٠ خولەک
#
#  کاردانەوەی خێرا: python bot.py --check   (پشکنینی ژینگە)
# =====================================================================

import glob
import io
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import requests

# ------------------------------ Config -------------------------------

def env_int(name, default):
    v = (os.getenv(name) or "").strip()
    try:
        return int(float(v)) if v else default
    except ValueError:
        return default

BOT_TOKEN         = (os.getenv("BOT_TOKEN") or "").strip()
TARGET_LANG       = (os.getenv("TARGET_LANG") or "ckb").strip()      # ckb = کوردی سۆرانی
STATE_FILE        = (os.getenv("STATE_FILE") or "state.json").strip()
RUN_SECONDS       = env_int("RUN_SECONDS", 2700)     # ماوەی خولی polling (بنەڕەت ٤٥ خولەک)
IDLE_EXIT_SECONDS = env_int("IDLE_EXIT_SECONDS", 0)  # 0 = لەکارخراو — بۆت تا کۆتایی خول ئۆنلاین دەمێنێت
HEAVY_BUDGET      = env_int("HEAVY_BUDGET", 2400)    # زۆرترین کات بۆ یەک ڤیدیۆ/دەنگ (چرکە)
MAX_VIDEO_MINUTES = env_int("MAX_VIDEO_MINUTES", 0)  # 0 = بێ سنوور
BURN_SUBTITLES    = env_int("BURN_SUBTITLES", 1)     # 1 = ژێرنووسی کوردی لەسەر ڤیدیۆ دادەنرێت
BURN_MAX_MINUTES  = env_int("BURN_MAX_MINUTES", 30)  # زۆرترین درێژی ڤیدیۆ بۆ سووتاندنی ژێرنووس
SEND_PDF_COPY     = env_int("SEND_PDF_COPY", 1)      # 1 = کۆپی PDF بۆ هەموو فایلەکان دەنێردرێت
WHISPER_MODEL     = (os.getenv("WHISPER_MODEL") or "base").strip()
SOURCE_LANG       = (os.getenv("SOURCE_LANG") or "").strip() or None  # None = خۆکار
GLM_API_KEY       = (os.getenv("GLM_API_KEY") or os.getenv("ZAI_API_KEY") or "").strip()
GLM_API_URL       = (os.getenv("GLM_API_URL") or "https://api.z.ai/api/paas/v4").strip().rstrip("/")
GLM_MODEL         = (os.getenv("GLM_MODEL") or "glm-4-flash").strip()
YT_COOKIES        = (os.getenv("YT_COOKIES") or "").strip()

TG_MAX_FILE = 20 * 1024 * 1024
TG_API = "https://api.telegram.org/bot" + BOT_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sorani-bot")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) sorani-translator-bot/1.0"})


class BotError(Exception):
    """هەڵەی دۆستانە: نامەکەی وەکو خۆی بۆ بەکارهێنەر دەنێردرێت."""


# -------------------------- نووسینە کوردییەکان ------------------------

WELCOME = (
    "سڵاو! من بۆتی وەرگێڕانم بۆ کوردی سۆرانی.\n"
    "\n"
    "چی دەکەم:\n"
    "• هەر دەقێک بنێری (هەر زمانێک بێت) — ڕاستەوخۆ بە سۆرانی وەریدەگێڕمەوە.\n"
    "• فایلی PDF، Word، PowerPoint، TXT یان SRT بنێری — وەرگێڕدراوەکە بە هەمان جۆری فایل\n"
    "  دەنێرمەوە + کۆپی PDF یش لەگەڵ دەکەم.\n"
    "• ڤیدیۆ یان دەنگ بنێری (فایل)، یان لینکی YouTube / Facebook / TikTok — دەنگەکە دەخوێنمەوە،\n"
    "  وەریدەگێڕم و بە فایلی SRT و Word و PDF دەینێرم + ژێرنووسی کوردی لەسەر ڤیدیۆکە دادەنێم.\n"
    "\n"
    "تێبینی گرنگ:\n"
    "• فایلی ڕاستەوخۆ دەبێت کەمتر بێت لە ٢٠ مێگابایت (سنووری تەلەگرام).\n"
    "• بەردەوام ئۆنلاینم (٢٤ کاتژمێر / ٧ ڕۆژ)؛ تەنها لە کاتی گۆڕینی خولەکە (هەر نزیکەی کاتژمێرێک)\n"
    "  لەوانەیە وەڵامەکەم چەند خولەکێک دابخرێت."
)

PROG_DOWNLOAD   = "چاوەڕێ بکە... ڤیدیۆکە داگیر دەم. (بەپێی قەبارەکەی چەند خولەکێک دەخایەنێت)"
MSG_MEDIA_START = "چاوەڕێ بکە... فایلەکە دادەگرم و دەنگەکە دەخوێنمەوە."
MSG_DOC_START   = "چاوەڕێ بکە... فایلەکە وەرگێڕ دەم. (بۆ فایلە گەورەکان چەند خولەکێک دەخایەنێت)"
MSG_DOWNLOADED  = "داگیرا: {title}\nماوە: {dur} — ئێستا دەنگ دەخوێنمەوە..."
MSG_TRANSCRIBED = "دەنگ خرایە سەر نووسین: زمانی «{lang}» — ماوە {dur} — {n} بەش.\nئێستا وەرگێڕان بۆ سۆرانی دەست پێدەکات..."
MSG_VIDEO_DONE  = "وەرگێڕان تەواو بوو: فایلی SRT بۆ ژێرنووس + فایلی Word و PDF بۆ خوێندنەوە."
CAPTION_DOC     = "وەرگێڕانی کوردی سۆرانی ئامادەیە."
CAPTION_PDF     = "هەمان وەرگێڕان بە شێوەی PDF."
MSG_BURN_START   = "ئێستا ژێرنووسی کوردی لەسەر ڤیدیۆکە دادەنێم... (ئەم قۆناغە چەند خولەکێک دەخایەنێت)"
CAPTION_BURN     = "ڤیدیۆکە بە ژێرنووسی کوردی سۆرانی."
MSG_BURN_FAIL    = ("بەداخەوە دانانی ژێرنووس لەسەر ڤیدیۆ سەرنەچوو؛ "
                    "بەڵام فایلەکانی SRT و Word و PDF ئامادەن.")
MSG_BURN_SKIPPED = ("ڤیدیۆکە لە {max} خولەک درێژترە، بۆیە ژێرنووسەکە نەخرایە سەر ڤیدیۆ "
                    "(سنووری کاتی GitHub Actions).\n"
                    "فایلی SRT ئامادەیە — دەتوانیت بە بەرنامەی مۆنتاژ یان خزمەتگوزاری "
                    "کاپشن بیخەیتە سەر ڤیدیۆکە.")
MSG_UNSUPPORTED = ("ئەم جۆرە پەیام/فایلە پشتگیری نەکراوە.\n"
                   "پشتگیریکراو: PDF، Word (docx)، PowerPoint (pptx)، TXT، SRT، "
                   "ڤیدیۆ، دەنگ، لینکی ڤیدیۆ، یان دەقی ئاسایی.")
MSG_ALREADY     = "ئەم دەقە پێشتر سۆرانییە یان پێویستی بە وەرگێڕان نییە."
ERR_LINK        = ("نەتوانم ڤیدیۆکە لەم لینکە دابگرم. لەوانەیە ماڵپەڕەکە ڕێگری لە سێرڤەرەکان بکات "
                   "(بەتایبەتی YouTube لە ئایپییەکانی GitHub).\n"
                   "چارەسەر: دووبارە هەوڵ بدە، یان ڤیدیۆکە دابگرە و وەکو فایل بنێرە (تا ٢٠ مێگابایت).")
ERR_BIG         = ("فایلەکە لە ٢٠ مێگابایت گەورەترە (سنووری داگرتنەوەی تەلەگرام بۆ بۆتەکان).\n"
                   "چارەسەر: بەشێکی بچووکتر بنێرە، یان لینکی ڤیدیۆکە بنێرە.")
ERR_SCANNED     = "ئەم فایلی PDF ە دەقی نووسراوی تێدا نییە (وەکو وێنەیەکی سکانیکراوە) و بەبێ OCR ناتوانرێت بخوێنرێتەوە."
ERR_LONG        = "پرۆسێسەکە زۆر درێژ کێشا و ڕاوەستێنرا. تکایە ڤیدیۆ/فایلەکە بکە بە بەشی بچووکتر."
ERR_AUDIO       = "نەتوانم دەنگێک لەم فایلە بخوێنمەوە. لەوانەیە بێ دەنگ بێت یان فۆرماتەکە پشتگیری نەکرێت."
ERR_FFMPEG      = "ئامرازی ffmpeg لەم ژینگەیەدا بەردەست نییە (لە GitHub Actions خۆکارانە دامەزرێت)."
ERR_TRANS       = "وەرگێڕان سەرنەچوو — کێشە لە خزمەتگوزاری وەرگێڕان ڕوویدا. تکایە دووبارە هەوڵ بدە."
ERR_GEN         = "هەڵەیەکی نەخوازراو ڕوویدا. تکایە دووبارە هەوڵ بدە."

GLM_PROMPT = (
    "You are a professional translator specialized in Central Kurdish "
    "(Sorani, ISO 639-3: ckb, written in Arabic script). Translate the user's "
    "text into natural, fluent Sorani Kurdish. Preserve the original paragraph "
    "structure and line breaks. Output ONLY the translated text — no "
    "explanations, no quotes, no extra commentary."
)

DOC_EXTS  = {".pdf", ".docx", ".pptx", ".txt", ".srt"}
MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".3gp", ".ts",
              ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac",
              ".wma", ".amr"}


def has_letters(t):
    return any(c.isalpha() for c in (t or ""))


def sanitize(name):
    name = re.sub(r"[^\w.\- ]+", "_", (name or "file"))[:80].strip()
    return name or "file"


def fmt_dur(sec):
    try:
        sec = int(float(sec))
    except Exception:
        return "?"
    return "%d خولەک و %d چرکە" % (sec // 60, sec % 60)


def ts(sec):
    """چرکە -> 00:00:00,000  (فۆرماتی SRT)"""
    sec = max(0.0, float(sec))
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms3 = divmod(rem, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms3)


# ----------------------------- Telegram ------------------------------

def tg_call(method, payload=None, files=None, timeout=70):
    r = SESSION.post(TG_API + "/" + method, data=payload, files=files, timeout=timeout)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError("telegram %s http %s" % (method, r.status_code))
    if not j.get("ok"):
        desc = j.get("description") or ("http %s" % r.status_code)
        if "too big" in desc.lower():
            raise BotError(ERR_BIG)
        raise BotError("Telegram: " + desc)
    return j.get("result")


def tg_send(chat_id, text):
    text = (text or "").strip() or "-"
    while text:
        if len(text) <= 4000:
            part, rest = text, ""
        else:
            cut = text.rfind("\n", 0, 4000)
            if cut < 2000:
                cut = 4000
            part, rest = text[:cut], text[cut:].lstrip("\n")
        tg_call("sendMessage", {"chat_id": chat_id, "text": part})
        text = rest


def _safe_send(chat_id, text):
    try:
        tg_send(chat_id, text)
    except Exception:
        log.error("failed to send message to %s", chat_id)


def tg_action(chat_id, action):
    try:
        tg_call("sendChatAction", {"chat_id": chat_id, "action": action}, timeout=20)
    except Exception:
        pass


def tg_send_doc(chat_id, path, caption=None):
    payload = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption[:1000]
    with open(path, "rb") as fh:
        tg_call("sendDocument", payload,
                files={"document": (os.path.basename(path), fh)}, timeout=600)


def tg_download(file_id, workdir, name_hint="file"):
    f = tg_call("getFile", {"file_id": file_id}, timeout=60)
    if int(f.get("file_size") or 0) > TG_MAX_FILE:
        raise BotError(ERR_BIG)
    url = "https://api.telegram.org/file/bot%s/%s" % (BOT_TOKEN, f["file_path"])
    r = SESSION.get(url, timeout=600)
    r.raise_for_status()
    ext = os.path.splitext(f.get("file_path") or "")[1] or ""
    path = os.path.join(workdir, sanitize(name_hint) + ext)
    with open(path, "wb") as fh:
        fh.write(r.content)
    return path


def tg_get_updates(offset, timeout=40):
    try:
        return tg_call("getUpdates",
                       {"offset": offset, "timeout": int(timeout), "limit": 100,
                        "allowed_updates": ["message"]},
                       timeout=int(timeout) + 20) or []
    except requests.RequestException as e:
        log.warning("network error while polling: %s", e)
        return []
    except BotError as e:
        if "409" in str(e) or "conflict" in str(e).lower():
            log.warning("getUpdates conflict (409) — another poller is running; sleeping")
            time.sleep(20)
            return []
        raise


def skip_backlog(state):
    """لە یەکەم کارکردن: تەنها کۆتا نوێکردنەوە وەربگرە، پەیامە کۆنەکان تێپەڕە."""
    res = tg_call("getUpdates", {"offset": -1, "limit": 1, "timeout": 0}, timeout=30)
    if res:
        state["offset"] = res[-1]["update_id"] + 1
        save_state(state)
        log.info("backlog skipped, starting offset=%s", state["offset"])


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"offset": 0}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, STATE_FILE)


# -------------------------- وەرگێڕان ---------------------------------
#  Google Translate (خاڵی بەخۆڕایی translate.googleapis.com) + GLM یەدەگ

GTX_URL = "https://translate.googleapis.com/translate_a/single"
_MEMO = {}


def _gtx_once(text):
    """وەرگێڕانێکی تاکی بە گووگڵ. دەگەڕێتەوە: (وەرگێڕان, زمانی سەرچاوە)"""
    r = SESSION.post(
        GTX_URL,
        params={"client": "gtx", "sl": "auto", "tl": TARGET_LANG, "dt": "t", "dj": "1"},
        data={"q": text},
        timeout=40,
    )
    if r.status_code != 200:
        raise RuntimeError("gtx http %s" % r.status_code)
    data = r.json()
    src = data.get("src") or ""
    out = "".join(s.get("trans", "") for s in (data.get("sentences") or []))
    if not out and text.strip():
        raise RuntimeError("gtx empty result")
    return out, src


def _glm_once(text):
    if not GLM_API_KEY:
        raise RuntimeError("GLM not configured (set ZAI_API_KEY secret)")
    r = SESSION.post(
        GLM_API_URL + "/chat/completions",
        headers={"Authorization": "Bearer " + GLM_API_KEY},
        json={
            "model": GLM_MODEL,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": GLM_PROMPT},
                {"role": "user", "content": text},
            ],
        },
        timeout=180,
    )
    if r.status_code != 200:
        raise RuntimeError("glm http %s" % r.status_code)
    j = r.json()
    out = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not out.strip():
        raise RuntimeError("glm empty result")
    return out.strip()


def chunk_text(text, size=2800):
    """دەبەشکات بۆ پارچەی <=size، بەبێ لەدەستدانی هیچ پیتێک (بەپێی دێر)."""
    if len(text) <= size:
        return [text]
    lines = text.splitlines(keepends=True)
    chunks, cur = [], ""
    for ln in lines:
        while len(ln) > size:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(ln[:size])
            ln = ln[size:]
        if len(cur) + len(ln) > size:
            chunks.append(cur)
            cur = ln
        else:
            cur += ln
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


def _translate_with_fallback(chunk):
    last_err = None
    for attempt in range(3):
        try:
            out, src = _gtx_once(chunk)
            if src == TARGET_LANG:      # پێشتر سۆرانییە
                return chunk
            return out
        except Exception as e:
            last_err = e
            time.sleep(1.2 * (attempt + 1) + random.random())
    log.warning("google failed (%s), trying GLM fallback", last_err)
    try:
        return _glm_once(chunk)
    except Exception as e:
        log.error("GLM fallback failed: %s", e)
        raise BotError(ERR_TRANS)


def translate_text(text):
    text = text or ""
    if not has_letters(text):
        return text
    if len(text) <= 2800:
        return _translate_with_fallback(text)
    chunks = chunk_text(text)
    if len(chunks) <= 3:
        return "".join(_translate_with_fallback(c) for c in chunks)
    with ThreadPoolExecutor(min(4, len(chunks))) as ex:
        return "".join(ex.map(_translate_with_fallback, chunks))


def translate_item(text):
    """وەرگێڕانی پاراگراف/بەشێک بە یادەوەری (memo) — بۆ فایلەکان."""
    key = (text or "").strip()
    if not key:
        return text
    if key in _MEMO:
        return _MEMO[key]
    out = translate_text(text)
    _MEMO[key] = out
    return out


# -------------------------- دۆکیومێنتەکان -----------------------------

# --- TXT ---
def process_txt(data):
    text = data.decode("utf-8", errors="replace")
    return translate_text(text).encode("utf-8")


# --- SRT (ژێرنووس — کاتەکان دەمێننەوە، تەنها دەق وەرگێڕدرێت) ---
SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n"
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})[^\n]*\n"
    r"(.*?)(?=\n\s*\n|\Z)",
    re.S,
)


def parse_srt(data):
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for m in SRT_BLOCK_RE.finditer(text):
        stamp = m.group(2).strip()
        stamp = stamp.replace(".", ",") if "-->" in stamp else stamp
        body = m.group(3).strip()
        if body:
            cues.append((stamp, body))
    return cues


def translate_srt(data):
    """(srt_وەرگێڕدراو, cues, outs) دەگەڕێنێتەوە — بۆ SRT و PDF ی لگەڵ."""
    cues = parse_srt(data)
    if not cues:
        return "", [], []
    with ThreadPoolExecutor(6) as ex:
        outs = list(ex.map(translate_item, [c[1] for c in cues]))
    parts = []
    for i, ((stamp, _), out) in enumerate(zip(cues, outs), 1):
        parts.append("%d\n%s\n%s\n" % (i, stamp, (out or "").strip()))
    return "\n".join(parts), cues, outs


def process_srt(data):
    return translate_srt(data)[0]


# --- DOCX (Word) — گۆڕینی دەقی پاراگرافەکان لە شوێنی خۆیان (شێواز دەمێنێتەوە) ---
try:
    from docx import Document as DocxDocument
    from docx.oxml.ns import qn as dx_qn
    try:
        from docx.oxml import OxmlElement
    except Exception:
        from docx.oxml.shared import OxmlElement
except Exception as e:      # pragma: no cover
    raise SystemExit("python-docx is required: pip install python-docx (%s)" % e)


def _set_bidi_docx(p):
    try:
        pPr = p._p.get_or_add_pPr()
        if pPr.find(dx_qn("w:bidi")) is None:
            pPr.append(OxmlElement("w:bidi"))
    except Exception:
        pass


def _set_para_text(p, new_text):
    for hl in p._p.findall(dx_qn("w:hyperlink")):
        p._p.remove(hl)
    if p.runs:
        p.runs[0].text = new_text
        for r in p.runs[1:]:
            r.text = ""
    else:
        p.add_run(new_text)
    _set_bidi_docx(p)


def _docx_paragraphs(doc):
    for p in doc.paragraphs:
        yield p
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p
    for sec in doc.sections:
        for p in sec.header.paragraphs:
            yield p
        for p in sec.footer.paragraphs:
            yield p


def process_docx(data):
    doc = DocxDocument(io.BytesIO(data))
    paras = [p for p in _docx_paragraphs(doc) if has_letters(p.text)]
    if not paras:
        return data
    texts = [p.text for p in paras]
    with ThreadPoolExecutor(6) as ex:
        outs = list(ex.map(translate_item, texts))
    for p, out in zip(paras, outs):
        _set_para_text(p, out)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --- PPTX (PowerPoint) — گۆڕینی دەق لە شوێنی خۆی (لایەوت دەمێنێتەوە) ---
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


def _iter_shapes(shapes):
    for sh in shapes:
        try:
            if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
                for sub in _iter_shapes(sh.shapes):
                    yield sub
                continue
        except Exception:
            pass
        yield sh


def _ppt_paras(tf):
    try:
        return list(tf.paragraphs)
    except Exception:
        return []


def _set_ppt_para(para, new_text):
    try:
        runs = para.runs
        if runs:
            runs[0].text = new_text
            for r in runs[1:]:
                r.text = ""
        else:
            para.add_run().text = new_text
        pPr = para._p.get_or_add_pPr()
        pPr.set("rtl", "1")
    except Exception:
        pass


def process_pptx(data):
    prs = Presentation(io.BytesIO(data))
    items = []
    for slide in prs.slides:
        for sh in _iter_shapes(slide.shapes):
            try:
                if sh.has_text_frame:
                    for para in _ppt_paras(sh.text_frame):
                        if has_letters(para.text):
                            items.append((para, para.text))
            except Exception:
                pass
            try:
                if getattr(sh, "has_table", False) and sh.has_table:
                    for row in sh.table.rows:
                        for cell in row.cells:
                            for para in _ppt_paras(cell.text_frame):
                                if has_letters(para.text):
                                    items.append((para, para.text))
            except Exception:
                pass
        try:
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                for para in _ppt_paras(slide.notes_slide.notes_text_frame):
                    if has_letters(para.text):
                        items.append((para, para.text))
        except Exception:
            pass
    if not items:
        return data
    texts = [t for _, t in items]
    with ThreadPoolExecutor(6) as ex:
        outs = list(ex.map(translate_item, texts))
    for (para, _), out in zip(items, outs):
        _set_ppt_para(para, out)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# --- PDF: دەرهێنانی دەق (PyMuPDF) + بنیاتنانی PDF ی سۆرانی RTL (ReportLab) ---
try:
    import pymupdf as fitz_mod
except Exception:
    import fitz as fitz_mod

import arabic_reshaper
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

try:
    from bidi.algorithm import get_display
except Exception:
    from bidi import get_display

_reshaper = arabic_reshaper.ArabicReshaper()


def _shape(s):
    return _reshaper.reshape(s)


def _disp(s):
    return get_display(s)


# فۆنتەکان: سەرەتا ڕێڕەوی سیستەم (لە Actions بە apt دامەزرێت)، پاشان داگرتن
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/truetype/amiri/Amiri-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
]
FONT_URLS = [
    "https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",
    "https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/fonts/NotoNaskhArabic/hinted/ttf/NotoNaskhArabic-Regular.ttf",
    "https://cdn.jsdelivr.net/gh/notofonts/notofonts.github.io/fonts/NotoNaskhArabic/hinted/ttf/NotoNaskhArabic-Regular.ttf",
]


def ensure_arabic_font(workdir):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    fdir = os.path.join(workdir, "fonts")
    os.makedirs(fdir, exist_ok=True)
    fp = os.path.join(fdir, "arabic.ttf")
    if os.path.exists(fp) and os.path.getsize(fp) > 100_000:
        return fp            # فۆنتەکە پێشتر دابەزیوە — دووبارە داگری ناکەین
    for url in FONT_URLS:
        try:
            r = SESSION.get(url, timeout=90)
            r.raise_for_status()
            if len(r.content) > 100_000:
                with open(fp, "wb") as fh:
                    fh.write(r.content)
                log.info("downloaded Arabic font from %s", url)
                return fp
        except Exception as e:
            log.warning("font download failed %s: %s", url, e)
    return None


def extract_pdf_text(data):
    doc = fitz_mod.open(stream=data, filetype="pdf")
    pages = []
    for pg in doc:
        try:
            pages.append(pg.get_text("text") or "")
        except Exception:
            pages.append("")
    doc.close()
    if not has_letters("".join(pages)):
        raise BotError(ERR_SCANNED)
    return pages


def _wrap_rtl_lines(text, font_name, size, maxw):
    """دابەشکردنی دەق بۆ دێری هاوسەنگ، پاشان شێوەدان + bidi بۆ هەر دێرێک."""
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            lines.append("")
            continue
        words = raw.split()
        cur = ""
        for w in words:
            cand = (cur + " " + w) if cur else w
            try:
                width = pdfmetrics.stringWidth(_shape(cand), font_name, size)
            except Exception:
                width = len(cand) * size * 0.55
            if width <= maxw:
                cur = cand
            elif not cur:
                # وشەیەکی زۆر درێژ (لینک...) — بە زۆر دەبڕین
                chunk = w
                while chunk:
                    k = len(chunk)
                    while k > 1 and pdfmetrics.stringWidth(_shape(chunk[:k]), font_name, size) > maxw:
                        k -= 1
                    lines.append(_disp(_shape(chunk[:k])))
                    chunk = chunk[k:].lstrip()
                cur = ""
            else:
                lines.append(_disp(_shape(cur)))
                cur = w
        if cur:
            lines.append(_disp(_shape(cur)))
    return lines


def build_pdf(pages_texts, out_path, title="Sorani Translation"):
    workdir = os.path.dirname(os.path.abspath(out_path)) or "."
    font_path = ensure_arabic_font(workdir)
    if not font_path:
        raise RuntimeError("no Arabic font available (download failed)")
    pdfmetrics.registerFont(TTFont("KurdishFont", font_path))
    W, H = A4
    M = 46.0
    body, lead = 12.5, 21.0
    maxw = W - 2 * M
    c = rl_canvas.Canvas(out_path, pagesize=A4)
    c.setTitle((title or "Sorani Translation")[:120])
    c.setAuthor("Sorani Translator Bot")
    for i, ptxt in enumerate(pages_texts, 1):
        c.setFont("KurdishFont", 9)
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.drawRightString(W - M, H - M + 8, _disp(_shape("پەڕەی %d" % i)))
        c.setFillColorRGB(0, 0, 0)
        y = H - M - 26
        for ln in _wrap_rtl_lines(ptxt, "KurdishFont", body, maxw):
            if y < M + lead:
                c.showPage()
                c.setFont("KurdishFont", body)
                c.setFillColorRGB(0, 0, 0)
                y = H - M
            if ln:
                c.setFont("KurdishFont", body)
                c.drawRightString(W - M, y, ln)
            y -= lead
        c.showPage()
    c.save()
    return out_path


# ---------- PDF ی گشتی: بلۆکەکان + گۆڕین بە LibreOffice (کۆپی PDF) ----------

_PDF_STYLES = {
    "title":  (16.0, 24.0, 10.0),
    "h1":     (15.0, 22.0, 8.0),
    "h2":     (13.5, 20.0, 6.0),
    "body":   (12.5, 21.0, 4.0),
    "bullet": (12.5, 21.0, 3.0),
    "meta":   (10.5, 17.0, 3.0),
}


def build_pdf_blocks(blocks, out_path, title="Sorani Translation"):
    """PDF ی RTL لە لیستی بلۆک (جۆر، دەق). جۆرەکان: title/h1/h2/body/bullet/meta"""
    workdir = os.path.dirname(os.path.abspath(out_path)) or "."
    font_path = ensure_arabic_font(workdir)
    if not font_path:
        raise RuntimeError("no Arabic font available (download failed)")
    pdfmetrics.registerFont(TTFont("KurdishFont", font_path))
    W, H = A4
    M = 46.0
    maxw = W - 2 * M
    c = rl_canvas.Canvas(out_path, pagesize=A4)
    c.setTitle((title or "Sorani Translation")[:120])
    c.setAuthor("Sorani Translator Bot")

    pg = {"n": 1}

    def draw_footer():
        c.setFont("KurdishFont", 9)
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.drawRightString(W - M, H - M + 8, _disp(_shape("پەڕەی %d" % pg["n"])))
        c.setFillColorRGB(0, 0, 0)

    def start_page():
        c.showPage()
        pg["n"] += 1
        draw_footer()
        return H - M

    draw_footer()
    y = H - M - 10
    if title:
        for ln in _wrap_rtl_lines(title, "KurdishFont", 16.0, maxw):
            c.setFont("KurdishFont", 16.0)
            c.drawRightString(W - M, y, ln)
            y -= 24.0
        y -= 10.0

    for kind, text in blocks:
        text = (text or "").strip()
        if not text:
            continue
        size, lead, gap = _PDF_STYLES.get(kind, _PDF_STYLES["body"])
        if kind == "bullet":
            text = "• " + text
        for ln in _wrap_rtl_lines(text, "KurdishFont", size, maxw):
            if y < M + lead:
                y = start_page()
            if ln:
                c.setFont("KurdishFont", size)
                c.setFillColorRGB(0.1, 0.1, 0.1) if kind in ("title", "h1", "h2") \
                    else c.setFillColorRGB(0, 0, 0)
                c.drawRightString(W - M, y, ln)
            y -= lead
        y -= gap
    c.save()
    return out_path


def _soffice_bin():
    return shutil.which("soffice") or shutil.which("libreoffice")


def office_to_pdf(src_path, out_dir, timeout=420):
    """گۆڕینی DOCX/PPTX بۆ PDF بە LibreOffice — هەمان شێواز و وێنەکان دەمێننەوە.
    ئەگەر نەکرا None دەگەڕێنێتەوە (کۆدەکە بە ReportLab بەردەوام دەبێت)."""
    soff = _soffice_bin()
    if not soff:
        log.info("soffice not available — using ReportLab fallback")
        return None
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    prof = tempfile.mkdtemp(prefix="lo_profile_")
    cmd = [soff, "-env:UserInstallation=file://" + prof,
           "--headless", "--norestore", "--nolockcheck",
           "--convert-to", "pdf", "--outdir", out_dir, os.path.abspath(src_path)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        log.warning("soffice failed: %s", e)
        return None
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    stem = os.path.splitext(os.path.basename(src_path))[0]
    out = os.path.join(out_dir, stem + ".pdf")
    if res.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        log.warning("soffice convert failed: rc=%s %s",
                    res.returncode, (res.stderr or "")[-300:])
        return None
    log.info("LibreOffice converted %s -> %s", os.path.basename(src_path), os.path.basename(out))
    return out


def register_font_fontconfig(font_path):
    """فۆنتەکە دەخاتە ~/.fonts بۆ ئەوەی LibreOffice بیبینێت (بۆ نووسینی عەرەبی/کوردی)."""
    try:
        fdir = os.path.expanduser("~/.fonts")
        os.makedirs(fdir, exist_ok=True)
        dst = os.path.join(fdir, "bot-arabic.ttf")
        if os.path.abspath(font_path) != os.path.abspath(dst):
            shutil.copyfile(font_path, dst)
        subprocess.run(["fc-cache", "-f"], capture_output=True, timeout=90)
    except Exception as e:
        log.warning("fontconfig register failed: %s", e)


def ensure_font_and_register(workdir):
    fp = ensure_arabic_font(workdir)
    if fp:
        register_font_fontconfig(fp)
    return fp


def _docx_para_block(p):
    t = (p.text or "").strip()
    if not t:
        return []
    try:
        sname = ((p.style and p.style.name) or "").lower()
    except Exception:
        sname = ""
    if sname == "title" or "heading 1" in sname:
        return [("h1", t)]
    if "heading" in sname:
        return [("h2", t)]
    if "list" in sname or "bullet" in sname:
        return [("bullet", t)]
    return [("body", t)]


def blocks_from_docx(path):
    """بلۆکەکان لە فایلی Word (پاراگراف + خشتە بە هەمان ڕیزبەندی)."""
    doc = DocxDocument(path)
    blocks = []
    try:
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table as _Tbl
        from docx.text.paragraph import Paragraph as _Para
        for child in doc.element.body.iterchildren():
            if isinstance(child, CT_P):
                blocks.extend(_docx_para_block(_Para(child, doc)))
            elif isinstance(child, CT_Tbl):
                try:
                    for row in _Tbl(child, doc).rows:
                        cells = []
                        for cell in row.cells:
                            ct = " ".join(x.strip() for x in (cell.text or "").splitlines()
                                          if x.strip())
                            if ct:
                                cells.append(ct)
                        if cells:
                            blocks.append(("body", " | ".join(cells)))
                except Exception:
                    pass
    except Exception:
        for p in doc.paragraphs:
            blocks.extend(_docx_para_block(p))
    return [b for b in blocks if (b[1] or "").strip()]


def blocks_from_pptx(path):
    """بلۆکەکان لە فایلی PowerPoint (سلاید بە سلاید)."""
    prs = Presentation(path)
    blocks = []
    for i, slide in enumerate(prs.slides, 1):
        slide_blocks = []
        for sh in _iter_shapes(slide.shapes):
            try:
                if sh.has_text_frame:
                    for para in _ppt_paras(sh.text_frame):
                        t = (para.text or "").strip()
                        if t and has_letters(t):
                            slide_blocks.append(
                                ("bullet" if getattr(para, "level", 0) else "body", t))
            except Exception:
                pass
        if slide_blocks:
            blocks.append(("h2", "سلایدی %d" % i))
            blocks.extend(slide_blocks)
    return blocks


def blocks_from_plain(text):
    blocks = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if ln:
            blocks.append(("body", ln))
    return blocks


def blocks_from_transcript(cues, texts):
    """بلۆکی نووسینی دەنگ: cues دەکرێت (stamp_str, text) یان (st, en, text) بێت."""
    blocks = []
    for cue, t in zip(cues, texts):
        t = (t or "").strip()
        if not t:
            continue
        raw = cue[0] if len(cue) == 2 else ts(cue[0])
        stamp = str(raw).split("-->")[0].strip()
        blocks.append(("meta", "[%s]" % stamp))
        blocks.append(("body", t))
    return blocks


def docx_to_pdf(docx_path, workdir, title):
    """PDF بۆ Word: سەرەتا LibreOffice (شێوازی ڕەسەن)، ئەگەر نەبوو ReportLab."""
    out = None
    try:
        out = office_to_pdf(docx_path, workdir)
    except Exception as e:
        log.warning("office_to_pdf(docx) error: %s", e)
    if out:
        return out
    try:
        blocks = blocks_from_docx(docx_path)
        if not blocks:
            return None
        stem = sanitize(os.path.splitext(os.path.basename(docx_path))[0])
        return build_pdf_blocks(blocks, os.path.join(workdir, stem + ".pdf"), title)
    except Exception as e:
        log.warning("docx pdf fallback failed: %s", e)
        return None


def pptx_to_pdf(pptx_path, workdir, title):
    """PDF بۆ PowerPoint: سەرەتا LibreOffice، ئەگەر نەبوو ReportLab."""
    out = None
    try:
        out = office_to_pdf(pptx_path, workdir)
    except Exception as e:
        log.warning("office_to_pdf(pptx) error: %s", e)
    if out:
        return out
    try:
        blocks = blocks_from_pptx(pptx_path)
        if not blocks:
            return None
        stem = sanitize(os.path.splitext(os.path.basename(pptx_path))[0])
        return build_pdf_blocks(blocks, os.path.join(workdir, stem + ".pdf"), title)
    except Exception as e:
        log.warning("pptx pdf fallback failed: %s", e)
        return None


# ----------------------- ڤیدیۆ / دەنگ -----------------------------

def ffmpeg_extract(src, dst):
    if not shutil.which("ffmpeg"):
        raise BotError(ERR_FFMPEG)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
           "-vn", "-ac", "1", "-ar", "16000", dst]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        raise BotError(ERR_LONG)
    if res.returncode != 0 or not os.path.exists(dst):
        log.warning("ffmpeg failed: %s", (res.stderr or "")[-500:])
        raise BotError(ERR_AUDIO)


def ffprobe_duration(path):
    if not shutil.which("ffprobe"):
        return 0.0
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=60)
        return float(res.stdout.strip()) if res.returncode == 0 else 0.0
    except Exception:
        return 0.0


def ffprobe_has_video(path):
    """ئایا فایلەکە تراکێکی ڤیدیۆی تێدایە؟ (بۆ دەنگی خاڵیس False)"""
    if not shutil.which("ffprobe"):
        return False
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60)
        return "video" in (res.stdout or "")
    except Exception:
        return False


def _ass_font_family(font_path):
    stem = os.path.splitext(os.path.basename(font_path or ""))[0]
    stem = stem.split("-")[0]
    fam = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem)
    return fam.strip() or "Amiri"


def _ass_style(font_path):
    fam = _ass_font_family(font_path)
    return ("FontName=%s,Fontsize=22,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=1,"
            "Outline=2,Shadow=1,Alignment=2,MarginV=28,Spacing=0.3" % fam)


def burn_subtitles(src_video, srt_path, out_path, workdir, timeout=2400):
    """ژێرنووسی کوردی دەکاتەوە سەر خۆی ڤیدیۆکە (ffmpeg + libass).
    فۆنتی عەرەبی/کوردی لە fontsdir دا دەدۆزرێتەوە. ئەگەر سەرنەچوو None."""
    if not shutil.which("ffmpeg"):
        raise BotError(ERR_FFMPEG)
    font_path = ensure_arabic_font(workdir)
    cwd = os.path.abspath(workdir)
    srt_local = os.path.join(cwd, "burn_subs.srt")
    shutil.copyfile(srt_path, srt_local)
    ext = os.path.splitext(src_video)[1] or ".mp4"
    src_local = os.path.join(cwd, "burn_src" + ext)
    if os.path.abspath(src_video) != os.path.abspath(src_local):
        shutil.copyfile(src_video, src_local)
    fontsdir = os.path.dirname(font_path) if font_path else "/usr/share/fonts"
    vf = "subtitles=burn_subs.srt:fontsdir=%s:force_style='%s'" % (fontsdir, _ass_style(font_path))
    out_tmp = os.path.join(cwd, "burn_out.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src_local,
           "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", out_tmp]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise BotError(ERR_LONG)
    if res.returncode != 0 or not os.path.exists(out_tmp) or os.path.getsize(out_tmp) == 0:
        log.warning("burn failed: %s", (res.stderr or "")[-600:])
        return None
    # ئەگەر قەبارەکە گەورەترە لە سنووری تەلەگرام، بە bitrate ی ژمێریارکراو دووبارە بیکەوە
    if os.path.getsize(out_tmp) > 45 * 1024 * 1024:
        dur = ffprobe_duration(out_tmp) or ffprobe_duration(src_local)
        if dur > 0:
            kbps = max(300, int(40 * 1024 * 1024 * 8.0 / dur / 1000))
            out_tmp2 = os.path.join(cwd, "burn_out2.mp4")
            cmd2 = ["ffmpeg", "-y", "-loglevel", "error", "-i", out_tmp,
                    "-c:v", "libx264", "-preset", "veryfast",
                    "-b:v", "%dk" % kbps, "-maxrate", "%dk" % int(kbps * 1.4),
                    "-bufsize", "%dk" % (kbps * 3),
                    "-c:a", "copy", "-movflags", "+faststart", out_tmp2]
            try:
                subprocess.run(cmd2, capture_output=True, text=True,
                               timeout=timeout, cwd=cwd)
                if os.path.exists(out_tmp2) and os.path.getsize(out_tmp2) > 0:
                    os.replace(out_tmp2, out_tmp)
            except Exception:
                pass
    shutil.move(out_tmp, out_path)
    return out_path


def tg_send_video(chat, path, caption=None):
    payload = {"chat_id": chat, "supports_streaming": "true"}
    if caption:
        payload["caption"] = caption[:1000]
    try:
        with open(path, "rb") as fh:
            tg_call("sendVideo", payload,
                    files={"video": (os.path.basename(path), fh)}, timeout=900)
        return True
    except Exception as e:
        log.warning("sendVideo failed (%s) — fallback to sendDocument", e)
        try:
            tg_send_doc(chat, path, caption)
            return True
        except Exception as e2:
            log.error("video send fallback failed: %s", e2)
            return False


_whisper_model = None
_HARD_DEADLINE = None   # کۆتایی کاتی خول — لە main() دادەنرێت (بۆ ڕاوەستاندنی ئەرکی قورس)


def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        log.info("loading whisper model: %s", WHISPER_MODEL)
        _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper_model


def transcribe_file(wav_path, t0):
    model = get_whisper()
    seg_iter, info = model.transcribe(
        wav_path,
        language=SOURCE_LANG,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    segs = []
    for s in seg_iter:
        if time.time() - t0 > HEAVY_BUDGET:
            raise BotError(ERR_LONG)
        if _HARD_DEADLINE and time.time() > _HARD_DEADLINE:
            raise BotError(ERR_LONG)
        txt = (s.text or "").strip()
        if txt:
            segs.append((float(s.start), float(s.end), txt))
    return segs, info


def merge_cues(segs, max_dur=12.0, max_gap=1.2):
    """یەکخستنی بەشە بچووکەکان بۆ ژێرنووسی خوێندنئاسان."""
    out, cur = [], None
    for st, en, txt in segs:
        if cur is not None and (en - cur[1]) <= max_dur and (st - cur[1]) <= max_gap:
            cur = (cur[0], en, (cur[2] + " " + txt).strip())
        else:
            if cur is not None:
                out.append(cur)
            cur = (st, en, txt)
    if cur is not None:
        out.append(cur)
    return out


def render_srt(cues, texts):
    blocks = []
    for i, ((st, en, _), t) in enumerate(zip(cues, texts), 1):
        blocks.append("%d\n%s --> %s\n%s\n" % (i, ts(st), ts(en), (t or "").strip()))
    return "\n".join(blocks)


def build_transcript_docx(cues, texts, out_path, title=""):
    doc = DocxDocument()
    if title:
        p = doc.add_paragraph(title[:120])
        _set_bidi_docx(p)
    for (st, en, _), t in zip(cues, texts):
        p = doc.add_paragraph("[%s] %s" % (ts(st), (t or "").strip()))
        _set_bidi_docx(p)
    doc.save(out_path)


def ytdl_download(url, workdir):
    import yt_dlp
    import yt_dlp.utils

    opts = {
        "format": ("best[height<=720]+bestaudio/best[height<=720]/bestaudio/best"
                   if BURN_SUBTITLES else "bestaudio/best"),  # ڤیدیۆ پێویستە بۆ ژێرنووس
        "outtmpl": os.path.join(workdir, "src.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "restrictfilenames": True,
    }
    if MAX_VIDEO_MINUTES > 0:
        opts["match_filter"] = yt_dlp.utils.match_filter_func(
            "duration<=%d" % (MAX_VIDEO_MINUTES * 60))
    if YT_COOKIES:
        cf = os.path.join(workdir, "cookies.txt")
        with open(cf, "w", encoding="utf-8") as fh:
            fh.write(YT_COOKIES + "\n")
        opts["cookiefile"] = cf

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info.get("_type") == "playlist" and info.get("entries"):
            info = info["entries"][0]
        path = None
        for d in (info.get("requested_downloads") or []):
            if d.get("filepath"):
                path = d["filepath"]
                break
        if not path:
            path = ydl.prepare_filename(info)
        if not os.path.exists(path):
            g = sorted(glob.glob(os.path.join(workdir, "src.*")))
            if not g:
                raise RuntimeError("downloaded file not found")
            path = g[-1]
        title = info.get("title") or "video"
        duration = info.get("duration") or 0
    return path, title, duration


def run_speech_pipeline(src_path, chat, workdir):
    t0 = time.time()
    tg_action(chat, "typing")
    wav = os.path.join(workdir, "audio_16k.wav")
    ffmpeg_extract(src_path, wav)
    tg_send(chat, "دەنگەکە دەخوێنمەوە... (ئەم قۆناغە ماوەیەک دەخایەنێت)")
    segs, info = transcribe_file(wav, t0)
    if not segs:
        raise BotError(ERR_AUDIO)
    lang = getattr(info, "language", None) or "?"
    dur = getattr(info, "duration", 0) or (segs[-1][1] if segs else 0)
    cues = merge_cues(segs)
    tg_send(chat, MSG_TRANSCRIBED.format(lang=lang, dur=fmt_dur(dur), n=len(cues)))
    texts = [c[2] for c in cues]
    with ThreadPoolExecutor(6) as ex:
        outs = list(ex.map(translate_item, texts))
    tg_action(chat, "upload_document")
    base = sanitize(os.path.splitext(os.path.basename(src_path))[0]) or "video"
    srt_path = os.path.join(workdir, "sorani_%s.srt" % base)
    with open(srt_path, "w", encoding="utf-8") as fh:
        fh.write(render_srt(cues, outs))
    docx_path = os.path.join(workdir, "sorani_%s.docx" % base)
    build_transcript_docx(cues, outs, docx_path, title="وەرگێڕانی کوردی سۆرانی — %s" % base)
    pdf_path = None
    if SEND_PDF_COPY:
        try:
            blocks = blocks_from_transcript(cues, outs)
            pdf_path = build_pdf_blocks(
                blocks, os.path.join(workdir, "sorani_%s.pdf" % base),
                title="وەرگێڕانی کوردی سۆرانی — %s" % base)
        except Exception as e:
            log.warning("transcript PDF failed: %s", e)
    preview = "\n".join(x for x in outs if x.strip())[:700]
    tg_send(chat, preview + ("…" if len(preview) == 700 else ""))
    tg_send_doc(chat, srt_path, MSG_VIDEO_DONE)
    tg_send_doc(chat, docx_path, None)
    if pdf_path:
        tg_send_doc(chat, pdf_path, None)
    # ---- ژێرنووسی کوردی لەسەر خۆی ڤیدیۆ (burn-in) ----
    if BURN_SUBTITLES:
        v_dur = float(dur or 0) or ffprobe_duration(src_path)
        if not ffprobe_has_video(src_path):
            log.info("burn skipped: source has no video stream (audio only)")
        elif v_dur and v_dur > BURN_MAX_MINUTES * 60:
            _safe_send(chat, MSG_BURN_SKIPPED.format(max=BURN_MAX_MINUTES))
        else:
            tg_send(chat, MSG_BURN_START)
            tg_action(chat, "upload_video")
            vid_out = os.path.join(workdir, "sorani_%s_subtitled.mp4" % base)
            try:
                vid_path = burn_subtitles(src_path, srt_path, vid_out, workdir)
            except BotError as e:
                _safe_send(chat, "هەڵە: %s" % e)
                vid_path = None
            if vid_path and os.path.exists(vid_path) and os.path.getsize(vid_path) <= 49 * 1024 * 1024:
                tg_send_video(chat, vid_path, CAPTION_BURN)
            else:
                _safe_send(chat, MSG_BURN_FAIL)


# --------------------------- هاندەرەکان -------------------------------

def route_document(name, mime):
    ext = os.path.splitext(name or "")[1].lower()
    if ext in DOC_EXTS:
        return ext
    if mime == "application/pdf":
        return ".pdf"
    if mime == "application/x-subrip":
        return ".srt"
    if mime == "text/plain":
        return ".txt"
    if mime and "wordprocessingml" in mime:
        return ".docx"
    if mime and "presentationml" in mime:
        return ".pptx"
    return None


def route_media(name, mime):
    ext = os.path.splitext(name or "")[1].lower()
    if ext in MEDIA_EXTS:
        return ext
    if mime and (mime.startswith("video/") or mime.startswith("audio/")):
        return ext or ".bin"
    return None


def handle_document_file(tg_file, name, chat, workdir):
    kind = route_document(name, tg_file.get("mime_type") or "")
    if not kind:
        raise BotError(MSG_UNSUPPORTED)
    if int(tg_file.get("file_size") or 0) > TG_MAX_FILE:
        raise BotError(ERR_BIG)
    tg_send(chat, MSG_DOC_START)
    path = tg_download(tg_file["file_id"], workdir, name)
    with open(path, "rb") as fh:
        data = fh.read()
    stem = sanitize(os.path.splitext(os.path.basename(name))[0]) or "file"
    out_path = None
    pdf_path = None
    caption = CAPTION_DOC

    if kind == ".pdf":
        pages = extract_pdf_text(data)
        with ThreadPoolExecutor(4) as ex:
            tpages = list(ex.map(translate_text, pages))
        try:
            out_path = build_pdf(tpages,
                                 os.path.join(workdir, "sorani_%s.pdf" % stem),
                                 title=stem)
        except Exception as e:
            log.warning("PDF build failed, falling back to TXT: %s", e)
            out_path = os.path.join(workdir, "sorani_%s.txt" % stem)
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("\n\n".join(tpages))
            caption = CAPTION_DOC + " (تێبینی: دروستکردنی PDF سەرنەچوو، بە TXT نێردرا)"

    elif kind == ".docx":
        out_path = os.path.join(workdir, "sorani_%s.docx" % stem)
        with open(out_path, "wb") as fh:
            fh.write(process_docx(data))
        if SEND_PDF_COPY:
            try:
                ensure_font_and_register(workdir)
                pdf_path = docx_to_pdf(out_path, workdir, title=stem)
            except Exception as e:
                log.warning("docx→pdf failed: %s", e)

    elif kind == ".pptx":
        out_path = os.path.join(workdir, "sorani_%s.pptx" % stem)
        with open(out_path, "wb") as fh:
            fh.write(process_pptx(data))
        if SEND_PDF_COPY:
            try:
                ensure_font_and_register(workdir)
                pdf_path = pptx_to_pdf(out_path, workdir, title=stem)
            except Exception as e:
                log.warning("pptx→pdf failed: %s", e)

    elif kind == ".txt":
        out_path = os.path.join(workdir, "sorani_%s.txt" % stem)
        with open(out_path, "wb") as fh:
            fh.write(process_txt(data))
        if SEND_PDF_COPY:
            try:
                with open(out_path, "r", encoding="utf-8") as fh:
                    ttext = fh.read()
                blocks = blocks_from_plain(ttext)
                if blocks:
                    pdf_path = build_pdf_blocks(
                        blocks, os.path.join(workdir, "sorani_%s.pdf" % stem),
                        title=stem)
            except Exception as e:
                log.warning("txt→pdf failed: %s", e)

    elif kind == ".srt":
        srt_out, scues, souts = translate_srt(data)
        if not srt_out:
            raise BotError(ERR_GEN)
        out_path = os.path.join(workdir, "sorani_%s.srt" % stem)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(srt_out)
        if SEND_PDF_COPY and scues:
            try:
                blocks = blocks_from_transcript(scues, souts)
                pdf_path = build_pdf_blocks(
                    blocks, os.path.join(workdir, "sorani_%s.pdf" % stem),
                    title=stem)
            except Exception as e:
                log.warning("srt→pdf failed: %s", e)

    if out_path and os.path.exists(out_path):
        tg_send_doc(chat, out_path, caption)
    else:
        raise BotError(ERR_GEN)
    if pdf_path and os.path.exists(pdf_path):
        tg_send_doc(chat, pdf_path, CAPTION_PDF)


def handle_media_file(tg_file, chat, workdir, name_hint="media"):
    if int(tg_file.get("file_size") or 0) > TG_MAX_FILE:
        raise BotError(ERR_BIG)
    tg_send(chat, MSG_MEDIA_START)
    path = tg_download(tg_file["file_id"], workdir, name_hint)
    run_speech_pipeline(path, chat, workdir)


def handle_link(url, chat, workdir):
    tg_action(chat, "typing")
    tg_send(chat, PROG_DOWNLOAD)
    try:
        src_path, title, duration = ytdl_download(url, workdir)
    except BotError:
        raise
    except Exception as e:
        log.warning("yt-dlp failed for %s: %s", url, e)
        raise BotError(ERR_LINK)
    msg = "داگیرا: %s" % title[:120]
    if duration:
        msg += "\nماوە: %s" % fmt_dur(duration)
    tg_send(chat, msg)
    run_speech_pipeline(src_path, chat, workdir)


def handle_text_translate(text, chat):
    tg_action(chat, "typing")
    out = translate_text(text)
    if out.strip() == text.strip():
        tg_send(chat, MSG_ALREADY)
    else:
        tg_send(chat, out)


def handle_message(msg, chat, workdir):
    doc = msg.get("document")
    if doc:
        name = doc.get("file_name") or "file"
        if route_document(name, doc.get("mime_type") or ""):
            handle_document_file(doc, name, chat, workdir)
        elif route_media(name, doc.get("mime_type") or ""):
            handle_media_file(doc, chat, workdir, os.path.basename(name))
        else:
            raise BotError(MSG_UNSUPPORTED)
        return

    for key in ("video", "voice", "audio", "video_note"):
        if key in msg:
            handle_media_file(msg[key], chat, workdir, key)
            return

    text = (msg.get("text") or "").strip()
    if not text:
        text = (msg.get("caption") or "").strip()
    if not text:
        raise BotError(MSG_UNSUPPORTED)

    if text.startswith("/"):
        tg_send(chat, WELCOME)
        return

    m = re.search(r"https?://\S+", text)
    if m:
        handle_link(m.group(0).rstrip(".,;)]}>"), chat, workdir)
        return

    handle_text_translate(text, chat)


def process_update(u, workdir):
    msg = u.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    if not chat:
        return
    task_dir = tempfile.mkdtemp(prefix="task_", dir=workdir)
    try:
        handle_message(msg, chat, task_dir)
    except BotError as e:
        _safe_send(chat, "هەڵە: %s" % e)
    except Exception:
        log.error("update failed:\n%s", traceback.format_exc())
        _safe_send(chat, ERR_GEN)
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


# ------------------------------ خولی سەرەکی ---------------------------

def self_check():
    print("== sorani-translator-bot :: self check ==")
    print("BOT_TOKEN:", "set" if BOT_TOKEN else "MISSING (add GitHub secret)")
    print("GLM fallback:", ("enabled, model=" + GLM_MODEL) if GLM_API_KEY else "not configured (Google only)")
    print("whisper model:", WHISPER_MODEL, "| max video minutes:", MAX_VIDEO_MINUTES or "unlimited")
    print("ffmpeg:", shutil.which("ffmpeg") or "MISSING")
    print("libreoffice:", shutil.which("soffice") or "missing (ReportLab fallback will be used)")
    print("burn subtitles:", ("on, max %d min" % BURN_MAX_MINUTES) if BURN_SUBTITLES else "off")
    print("pdf copy:", "on" if SEND_PDF_COPY else "off")
    try:
        import yt_dlp
        print("yt-dlp:", yt_dlp.version.__version__)
    except Exception as e:
        print("yt-dlp: FAIL", e)
    try:
        import faster_whisper  # noqa
        print("faster-whisper: OK")
    except Exception as e:
        print("faster-whisper: FAIL", e)
    try:
        out, src = _gtx_once("Hello, how are you today?")
        print("Google translate: OK  (src=%s) sample: %s" % (src, out[:80]))
    except Exception as e:
        print("Google translate: FAIL", e)
    print("done.")


def main():
    global _HARD_DEADLINE
    if "--check" in sys.argv:
        self_check()
        return
    if not BOT_TOKEN:
        print("BOT_TOKEN is missing — add it as a GitHub secret first.")
        sys.exit(42)

    state = load_state()
    save_state(state)

    workdir = tempfile.mkdtemp(prefix="soranibot_")
    start = time.time()
    deadline = start + RUN_SECONDS
    _HARD_DEADLINE = deadline - 600   # ١٠ خولەک بۆ ناردنەوەی دەرئەنجامەکان حیجز بکە

    if state.get("offset", 0) <= 0:
        try:
            skip_backlog(state)
        except Exception as e:
            log.warning("skip_backlog failed: %s", e)

    log.info("bot cycle started (offset=%s, budget=%ss)", state.get("offset"), RUN_SECONDS)

    while time.time() < deadline:
        remaining = deadline - time.time()
        updates = tg_get_updates(state.get("offset", 0),
                                 timeout=int(min(45, max(5, remaining))))
        if (not updates and IDLE_EXIT_SECONDS > 0
                and (time.time() - start) > IDLE_EXIT_SECONDS):
            break
        for u in updates:
            try:
                process_update(u, workdir)
            except Exception:
                log.error("process_update crashed:\n%s", traceback.format_exc())
            state["offset"] = u["update_id"] + 1
            save_state(state)
            log.info("processed update %s", u["update_id"])

    save_state(state)
    shutil.rmtree(workdir, ignore_errors=True)
    log.info("bot cycle finished")


if __name__ == "__main__":
    main()
