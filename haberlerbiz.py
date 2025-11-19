# bot.py
# -*- coding: utf-8 -*-
import os
import re
import json
import time
import html
import hashlib
import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# WordPress XML-RPC
from wordpress_xmlrpc import Client
from wordpress_xmlrpc.methods import posts, media, taxonomies
from wordpress_xmlrpc.compat import xmlrpc_client
from wordpress_xmlrpc import WordPressPost

# =======================
# ZORUNLU GÖMÜLÜ AYARLAR
# =======================
OPENAI_API_KEY = "xxxxxx"
WP_BASE_URL    = "https://haberler.biz".rstrip("/")

# REST (Application Password) – opsiyonel fallback
WP_USERNAME    = "xxxxxx"
WP_APPPASS     = "xxxxxx"

# XML-RPC (wp-admin şifresi) – ZORUNLU (XML-RPC kullanıyoruz)
WP_USERPASS    = "xxxxxx"

WP_STATUS      = "publish"
WP_DEFAULT_CAT = 0  # İstersen kategori ID yaz

# Kaynak
LIST_URL       = "https://www.haberler.com/son-dakika/"

# =======================
# LOG AYARI
# =======================
logging.basicConfig(
    level=logging.INFO,
    format="[*] %(message)s"
)
log = logging.getLogger("WPHaber")

# =======================
# YARDIMCI
# =======================
SEEN_PATH = "seen.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (WPHaberBot/1.0; +https://haberler.biz)"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def load_seen():
    if os.path.exists(SEEN_PATH):
        try:
            with open(SEEN_PATH, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    try:
        with open(SEEN_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def full_url(href):
    if href.startswith("http"):
        return href
    return urljoin("https://www.haberler.com", href.lstrip("/"))

def looks_like_placeholder(img_url: str) -> bool:
    if not img_url:
        return True
    # Haberler'in beyaz placeholder'ı:
    if "mstatic/assets/img/white.jpg" in img_url:
        return True
    # boş/generic
    low = img_url.lower()
    return any(x in low for x in ["placeholder", "blank", "noimage"])

def get_soup(url, timeout=20):
    r = SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")

def extract_list_items(list_url):
    soup = get_soup(list_url)
    cards = soup.select("div.new3sondk-news-card a.new3sondk-news")
    items = []
    for a in cards:
        href = a.get("href", "").strip()
        title = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        if not href or not title:
            continue

        # Görsel öncelik: mobil > normal
        img = a.select_one(".images-mobile img") or a.select_one(".images img")
        img_src = (img.get("src").strip() if img else "")

        # PAS kuralı: listede görsel yoksa veya placeholder ise, bu item'i atla
        if not img_src or looks_like_placeholder(img_src):
            continue

        items.append({
            "title": title,
            "url": full_url(href),
            "thumb": img_src
        })
    return items

def extract_article_main_image(soup):
    # Önce OG/Twitter image
    og = soup.find("meta", attrs={"property":"og:image"})
    if og and og.get("content"):
        return og["content"].strip()

    tw = soup.find("meta", attrs={"name":"twitter:image"})
    if tw and tw.get("content"):
        return tw["content"].strip()

    # İçerikten büyük görsel
    # Haber gövdesi sınıfları siteye göre değişebilir; birkaç olası seçici deneyelim:
    candidates = []
    for sel in [
        ".news-detail img",
        ".haber-metni img",
        ".article-content img",
        ".content-body img",
        "article img"
    ]:
        for im in soup.select(sel):
            src = im.get("src") or im.get("data-src") or ""
            src = src.strip()
            if not src or looks_like_placeholder(src):
                continue
            # Büyük görsel varsayımı: geniş kırpım/amp değil ise daha iyi olabilir,
            # yine de ilk düzgün olanı döndür.
            candidates.append(src)

    return candidates[0] if candidates else None

def extract_article_text(soup):
    # Temel gövde: paragrafları çek, biraz temizlik
    body_selectors = [
        ".news-detail",
        ".haber-metni",
        "article",
        ".content-body",
        ".article-content"
    ]
    parts = []
    for sel in body_selectors:
        node = soup.select_one(sel)
        if node:
            # paragraf bazlı
            for p in node.find_all(["p","h2","li"]):
                txt = p.get_text(" ", strip=True)
                if txt and len(txt) > 30 and "Haberler.com" not in txt:
                    parts.append(txt)
            break
    # yedek: sayfadaki uzun paragraflar
    if not parts:
        for p in soup.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if len(txt) > 40 and "Haberler.com" not in txt:
                parts.append(txt)

    # Metni tek string olarak döndür
    return "\n\n".join(parts[:12])  # çok uzun olmasın

# =======================
# OPENAI – içerik ve etiket üretimi
# =======================
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"

def openai_chat(messages, temperature=0.4, max_tokens=800):
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type":"application/json"
    }
    payload = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    r = requests.post(OPENAI_CHAT_URL, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

def build_article_with_tags(title, source_text):
    prompt = f"""Aşağıdaki başlık ve kaynak metne dayanarak Türkçe bir haber yaz.
- Tarafsız ve akıcı olsun
- 4-7 paragraf
- Gereksiz tekrar ve abartı olmasın
- En alta "ETİKETLER:" satırında 6-12 adet kısa etiket (virgülle)

BAŞLIK: {title}

KAYNAK METİN:
{source_text if source_text else "(Kaynak metin kısıtlı)"}"""

    content = openai_chat([
        {"role":"system","content":"Kısa, sade ve tarafsız Türkçe haber yazarı gibi davran."},
        {"role":"user","content": prompt}
    ])

    # "ETİKETLER:" satırını ayrıştır
    tags = []
    body = content
    m = re.search(r"ETİKETLER\s*:\s*(.+)", content, re.IGNORECASE)
    if m:
        tagline = m.group(1)
        tags = [t.strip() for t in re.split(r"[#,;,\|]", tagline) if t.strip()]
        # etiketsiz gövde:
        body = content[:m.start()].strip()

    # Paragrafları <p> içine al
    html_body = ""
    for para in [x.strip() for x in body.split("\n") if x.strip()]:
        html_body += f"<p>{html.escape(para)}</p>\n"

    return html_body, tags[:12]

# =======================
# WordPress – medya & yazı
# =======================
def wp_xmlrpc_client():
    endpoint = f"{WP_BASE_URL}/xmlrpc.php"
    return Client(endpoint, WP_USERNAME, WP_USERPASS)

def upload_media_xmlrpc(image_url):
    try:
        img_resp = SESSION.get(image_url, timeout=30)
        img_resp.raise_for_status()
        data = img_resp.content
    except Exception:
        return None

    name = os.path.basename(urlparse(image_url).path) or f"image_{int(time.time())}.jpg"
    if not re.search(r"\.(jpg|jpeg|png|gif|webp)$", name, re.I):
        name += ".jpg"

    data_struct = {
        'name': name,
        'type': 'image/jpeg',
        'bits': xmlrpc_client.Binary(data),
        'overwrite': False
    }
    try:
        cl = wp_xmlrpc_client()
        response = cl.call(media.UploadFile(data_struct))
        return response  # {'id':..., 'url':..., ...}
    except Exception as e:
        log.info(f"Medya yükleme (XML-RPC) hata: {e}")
        return None

def create_post_xmlrpc(title, content_html, tags=None, featured_media_id=None, status=WP_STATUS):
    post = WordPressPost()
    post.title = title
    post.content = content_html
    post.post_status = status
    if WP_DEFAULT_CAT:
        post.terms_names = {'post_tag': tags or [] , 'category': []}
        post.terms = []
    else:
        post.terms_names = {'post_tag': tags or []}

    if featured_media_id:
        post.thumbnail = featured_media_id

    cl = wp_xmlrpc_client()
    post_id = cl.call(posts.NewPost(post))
    return post_id

# ---- REST fallback
def rest_auth():
    from requests.auth import HTTPBasicAuth
    return HTTPBasicAuth(WP_USERNAME, WP_APPPASS.replace(" ", ""))

def upload_media_rest(image_url):
    try:
        img_resp = SESSION.get(image_url, timeout=30)
        img_resp.raise_for_status()
        data = img_resp.content
    except Exception:
        return None

    name = os.path.basename(urlparse(image_url).path) or f"image_{int(time.time())}.jpg"
    if not re.search(r"\.(jpg|jpeg|png|gif|webp)$", name, re.I):
        name += ".jpg"

    endpoint = f"{WP_BASE_URL}/wp-json/wp/v2/media"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    try:
        r = requests.post(endpoint, headers=headers, data=data, auth=rest_auth(), timeout=40)
        r.raise_for_status()
        return r.json()  # {id, guid:{rendered}, source_url...}
    except Exception as e:
        log.info(f"Medya yükleme (REST) hata: {e}")
        return None

def create_post_rest(title, content_html, tags=None, featured_media_id=None, status=WP_STATUS):
    endpoint = f"{WP_BASE_URL}/wp-json/wp/v2/posts"
    payload = {
        "title": title,
        "content": content_html,
        "status": status,
    }
    if featured_media_id:
        payload["featured_media"] = featured_media_id
    if tags:
        # isimden etiket oluştur/bağla (basit yol: 'tags' => CSV isim, WP eklentiniz destekliyorsa)
        payload["tags_input"] = tags

    try:
        r = requests.post(endpoint, json=payload, auth=rest_auth(), timeout=40)
        r.raise_for_status()
        return r.json().get("id")
    except Exception as e:
        log.info(f"Yazı oluşturma (REST) hata: {e}")
        return None

# =======================
# ANA AKIŞ
# =======================
def process_one(item, seen):
    title = item["title"].strip()
    url   = item["url"].strip()
    # tekillik – url hash
    uid = hashlib.md5(url.encode("utf-8")).hexdigest()
    if uid in seen:
        return False, "Yeni haber yok."

    # İçeriği çek
    try:
        soup = get_soup(url)
    except Exception as e:
        return False, f"İç sayfa hata: {e}"

    # Ana görsel zorunlu
    main_img = extract_article_main_image(soup)
    if not main_img or looks_like_placeholder(main_img):
        return False, "İç sayfada ana görsel yok/PAS."

    # Metin
    src_text = extract_article_text(soup)

    # OpenAI ile içerik + etiketler
    try:
        content_html, tags = build_article_with_tags(title, src_text)
    except Exception as e:
        return False, f"OpenAI hata: {e}"

    # Görseli yükle (XML-RPC öncelik)
    media_info = upload_media_xmlrpc(main_img)
    featured_id = None
    if media_info and isinstance(media_info, dict):
        featured_id = media_info.get("id")

    # XML-RPC post
    post_id = None
    try:
        post_id = create_post_xmlrpc(title, content_html, tags, featured_media_id=featured_id)
    except Exception as e:
        log.info(f"XML-RPC yazı hata: {e}")

    # REST fallback
    if not post_id:
        if not featured_id:
            rest_media = upload_media_rest(main_img)
            if rest_media and isinstance(rest_media, dict):
                featured_id = rest_media.get("id")
        post_id = create_post_rest(title, content_html, tags, featured_media_id=featured_id)

    if post_id:
        seen.add(uid)
        save_seen(seen)
        return True, f"Yayınlandı (ID:{post_id})"
    else:
        return False, "WordPress yayına alınamadı."

def main_loop():
    seen = load_seen()
    log.info("Sürekli takip (5 dakikada bir). Liste görseli YOKSA PAS, iç sayfada ana görsel YOKSA yine PAS.")
    while True:
        try:
            log.info(f"Liste çekiliyor: {LIST_URL}")
            items = extract_list_items(LIST_URL)
            log.info(f"Bulunan (küçük görselli) link sayısı: {len(items)}")

            new_count = 0
            for it in items:
                ok, msg = process_one(it, seen)
                if ok:
                    new_count += 1
                    log.info(f"✓ {it['title']} -> {msg}")
                else:
                    # Sessiz geç – sadece bilgi satırı
                    # log.debug de olabilirdi; örnek çıktıya uyduk.
                    pass

            if new_count == 0:
                log.info("Yeni haber yok.")
        except Exception as e:
            log.info(f"Hata: {e}")

        log.info("5 dakika bekleniyor...")
        time.sleep(300)

# =======================
# ÇALIŞTIR
# =======================
if __name__ == "__main__":
    main_loop()