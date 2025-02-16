from gevent import monkey
monkey.patch_all()

from flask import Flask, request, jsonify, Blueprint
import re
import logging
import sqlite3
import os
import requests
import ssl
import certifi
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
import yt_dlp
from moviepy.editor import VideoFileClip
from PIL import Image
from transformers import BlipProcessor, BlipForConditionalGeneration
from openai import OpenAI
import whisper

# SSL Fix
ssl._create_default_https_context = ssl._create_unverified_context

app = Flask(__name__)

# Logging konfigurieren
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# OpenAI Client
client = OpenAI(api_key="DEIN_API_KEY")

# Datenbank initialisieren
def init_db():
    conn = sqlite3.connect("somean.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS analyses (id INTEGER PRIMARY KEY, url TEXT UNIQUE, platform TEXT, type TEXT, result TEXT)''')
    conn.commit()
    conn.close()

init_db()

# Erkennung der Plattform
PLATFORM_PATTERNS = {
    "tiktok": r"(https?:\\/\\/)?(www\\.)?tiktok\\.com\\/",
    "instagram": r"(https?:\\/\\/)?(www\\.)?instagram\\.com\\/",
    "youtube": r"(https?:\\/\\/)?(www\\.)?youtube\\.com\\/|youtu\\.be\\/",
    "facebook": r"(https?:\\/\\/)?(www\\.)?facebook\\.com\\/",
    "linkedin": r"(https?:\\/\\/)?(www\\.)?linkedin\\.com\\/"
}

def detect_platform(url):
    for platform, pattern in PLATFORM_PATTERNS.items():
        if re.search(pattern, url):
            logging.info(f"Plattform erkannt: {platform}")
            return platform
    logging.info("Unbekannte Plattform")
    return "unknown"

def detect_content_type(url):
    if "reel" in url or "video" in url:
        return "video"
    elif "p/" in url or "photo" in url:
        return "image"
    elif "shorts" in url:
        return "short-video"
    elif "posts" in url or "status" in url:
        return "text"
    elif "linkedin" in url:
        return "linkedin-post"
    return "unknown"

# LinkedIn-Post-Verarbeitung
def process_linkedin_post(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        return "Fehler: LinkedIn-Post nicht abrufbar. Entweder ist der Beitrag privat oder LinkedIn blockiert Scraping."
    
    soup = BeautifulSoup(response.text, "html.parser")
    post_text = soup.find("div", class_="feed-shared-update-v2__description")
    
    return post_text.text.strip() if post_text else "Kein Text gefunden oder nicht öffentlich sichtbar."

# LinkedIn-Video-Verarbeitung
def process_linkedin_video(url):
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    driver = webdriver.Chrome(options=options)
    driver.get(url)
    
    try:
        video_elements = driver.find_elements(By.TAG_NAME, "video")
        video_links = [video.get_attribute("src") for video in video_elements if video.get_attribute("src")]
        driver.quit()
        
        if video_links:
            ydl_opts = {"outtmpl": "downloads/linkedin_video.mp4"}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_links[0]])
            return "LinkedIn-Video erfolgreich heruntergeladen und analysiert."
        return "Fehler: Kein Video gefunden oder nicht öffentlich sichtbar."
    except Exception as e:
        return f"Fehler: LinkedIn-Video konnte nicht extrahiert werden. Ursache: {str(e)}"

# Schritt-für-Schritt-Anleitungen
def generate_step_by_step(text):
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "Erstelle eine schrittweise Anleitung aus folgendem Text."},
            {"role": "user", "content": text}
        ]
    )
    return response.choices[0].message.content

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    url = data.get("url")
    
    if not url:
        return jsonify({"error": "Keine URL angegeben"}), 400
    
    platform = detect_platform(url)
    content_type = detect_content_type(url)
    
    conn = sqlite3.connect("somean.db")
    c = conn.cursor()
    c.execute("SELECT result FROM analyses WHERE url = ?", (url,))
    existing = c.fetchone()
    if existing:
        return jsonify({"platform": platform, "type": content_type, "cached_result": existing[0]})
    
    if content_type == "linkedin-post":
        result = process_linkedin_post(url)
    elif content_type == "video" and platform == "linkedin":
        result = process_linkedin_video(url)
    else:
        return jsonify({"error": "Unbekannter Inhaltstyp oder Plattform wird nicht unterstützt."}), 400
    
    c.execute("INSERT INTO analyses (url, platform, type, result) VALUES (?, ?, ?, ?)", (url, platform, content_type, str(result)))
    conn.commit()
    conn.close()
    
    return jsonify({"platform": platform, "type": content_type, "result": result})

if __name__ == "__main__":
    from gevent.pywsgi import WSGIServer
    port = int(os.getenv("PORT", 5000))
    http_server = WSGIServer(("0.0.0.0", port), app)
    http_server.serve_forever()