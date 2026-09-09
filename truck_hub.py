import os
import re
import json
import math
import sqlite3
import requests
from datetime import datetime
import anthropic

# ==========================================
# 1. CONFIGURATION & KEYS
# ==========================================
ANTHROPIC_KEY = os.getenv('ANTHROPIC_API_KEY')
ZENROWS_KEY = os.getenv('ZENROWS_API_KEY')

def safe_int(val, max_val=2147483647):
    if not val:
        return None
    digits_only = re.sub(r'[^\d]', '', str(val))
    if not digits_only:
        return None
    try:
        num = int(digits_only)
        return num if num <= max_val else None
    except (ValueError, OverflowError):
        return None

def safe_db_int(val, default=None, max_val=2147483647):
    if val is None:
        return default
    try:
        digits_only = re.sub(r'[^\d-]', '', str(val))
        if not digits_only or digits_only == '-':
            return default
        num = int(digits_only)
        return num if abs(num) <= max_val else default
    except (ValueError, OverflowError):
        return default

def calc_distance_from_sd(lat, lon):
    if not lat or not lon:
        return None
    try:
        lat1, lon1 = 32.7157, -117.1611  # San Diego 92101 center
        lat2, lon2 = float(lat), float(lon)
        R = 3958.8  # Earth radius in miles
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(R * c)
    except Exception:
        return None

def is_valid_hd_truck(title):
    t = (title or "").lower()
    has_target = any(k in t for k in ['2500', 'f-250', 'f250', 'f 250'])
    has_excluded = any(x in t for x in [
        '1500', '3500', 'equinox', 'bronco', 'mustang', 'colorado', 'ranger',
        'maverick', 'transit', 'tahoe', 'yukon', 'suburban', 'expedition',
        'explorer', 'f-150', 'f150', 'canyon', 'blazer', 'traverse', 'escape', 'edge'
    ])
    return has_target and not has_excluded

# ==========================================
# 2. ZENROWS FETCH HELPER
# ==========================================
def fetch_with_zenrows(target_url, wait_time='3000'):
    api_url = "https://api.zenrows.com/v1/"
    params = {
        'apikey': ZENROWS_KEY,
        'url': target_url,
        'js_render': 'true',
        'premium_proxy': 'true',
        'antibot': 'true',
        'wait': wait_time
    }
    try:
        response = requests.get(api_url, params=params, timeout=60)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        print(f"Fetch error: {e}")
    return None

# ==========================================
# 3. DATABASE INITIALIZATION & PURGE
# ==========================================
def init_db():
    conn = sqlite3.connect('hd_truck_market.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hd_truck_market (
            vin TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            current_price INTEGER,
            original_price INTEGER,
            mileage INTEGER,
            engine TEXT,
            axle_ratio TEXT,
            is_offroad_trim INTEGER DEFAULT 0,
            payload_capacity_lbs INTEGER,
            has_towing_package INTEGER DEFAULT 0,
            ai_towing_summary TEXT,
            ai_processed INTEGER DEFAULT 0,
            region_found TEXT,
            first_seen TEXT,
            last_seen TEXT,
            airstream_readiness_score INTEGER DEFAULT 0,
            distance_miles INTEGER
        )
    ''')
    for col in ["airstream_readiness_score INTEGER DEFAULT 0", "distance_miles INTEGER"]:
        try:
            cursor.execute(f"ALTER TABLE hd_truck_market ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

def purge_non_hd_records():
    conn = sqlite3.connect('hd_truck_market.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE hd_truck_market 
        SET url = REPLACE(REPLACE(url, '[https://www.autotrader.com](', ''), ')', '') 
        WHERE url LIKE '%[%'
    """)
    
    # Wipe generic boilerplate AI summaries so they get re-analyzed with sharp specs
    cursor.execute("""
        UPDATE hd_truck_market 
        SET ai_processed = 0 
        WHERE ai_towing_summary LIKE '%provides ample power%' 
           OR ai_towing_summary LIKE '%buyers should verify%'
           OR ai_towing_summary LIKE '%generally well-suited%'
    """)

    cursor.execute('''
        DELETE FROM hd_truck_market 
        WHERE title IS NOT NULL 
          AND title != 'Unknown Truck'
          AND (
              title NOT LIKE '%2500%' 
              AND title NOT LIKE '%F-250%' 
              AND title NOT LIKE '%F250%'
          )
    ''')
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted_count > 0:
        print(f"Purged {deleted_count} non-HD records from database.")

# ==========================================
# 4. AIRSTREAM READINESS SCORE ALGORITHM
# ==========================================
def calculate_readiness_score(title, engine_str, is_offroad_trim, price, region_found, has_towing_pkg, axle_ratio, payload_lbs, distance_miles=None):
    if not is_valid_hd_truck(title):
        return 0

    score = 50
    engine_upper = (engine_str or "").upper()

    if any(g in engine_upper for g in ["7.3L", "6.4L", "6.6L", "GAS"]):
        score += 20
    elif any(d in engine_upper for d in ["6.7L", "CUMMINS", "DURAMAX", "POWERSTROKE", "DIESEL"]):
        score += 5

    if is_offroad_trim:
        score -= 25

    if price:
        if price < 35000:
            score += 15
        elif price < 45000:
            score += 10
        elif price <= 50000:
            score += 5

    if distance_miles is not None:
        if distance_miles <= 150:
            score += 15
        elif distance_miles <= 350:
            score += 8
    else:
        if region_found == "SoCal Local":
            score += 15
        elif region_found == "Desert Southwest":
            score += 10
        elif region_found == "Texas Hub":
            score += 2

    if has_towing_pkg:
        score += 10

    axle_str = str(axle_ratio or "")
    if "4.10" in axle_str or "4.30" in axle_str:
        score += 10
    elif "3.73" in axle_str:
        score += 5

    if payload_lbs:
        if payload_lbs >= 2500:
            score += 10
        elif payload_lbs >= 1800:
            score += 5
        elif payload_lbs < 1500:
            score -= 15

    return max(0, min(100, score))

# ==========================================
# 5. CLAUDE AI ANALYSIS ENGINE
# ==========================================
def analyze_truck_with_claude(title, engine_raw, dealer_text, price=None, mileage=None, price_history=None):
    if not ANTHROPIC_KEY:
        return None

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = f"""
    You are a sharp, factual heavy-duty truck analyst evaluating a listing for a buyer towing a 2007 Airstream Safari 25' (7,000 lbs GVWR, ~1,100 lbs tongue weight).

    Extracted Listing Data:
    - Vehicle: {title}
    - Listed Price: ${price or 'Unlisted'}
    - Mileage: {mileage or 'Unlisted'}
    - Engine: {engine_raw or 'Unlisted'}
    - Price History: {price_history or 'None'}
    - Dealer Description & Extracted Specs:
    {dealer_text[:6000]}

    CRITICAL INSTRUCTIONS FOR `ai_towing_summary`:
    1. STRICTLY FORBIDDEN PHRASES: Do NOT output "provides ample power", "buyers should verify", "generally well-suited", or generic 2500 series facts.
    2. Focus ONLY on concrete hardware, specs, and listing value: e.g. state exact engine (6.4L V8 / 6.7L Cummins), transmission (8-speed auto), drivetrain (4WD), factory tow equipment (integrated brake controller, 5th wheel prep, side steps, chrome wheels), mileage callout, or price value.
    3. Keep `ai_towing_summary` under 25 words. Make it read like a sharp spec cheat-sheet (e.g., "6.4L V8 Hemi, 8-spd auto, 4WD Big Horn with 69.6k mi. Includes cab steps, Uconnect, and backup camera. Solid $33.9k price value.").

    Extract and return strictly a valid JSON object:
    {{
        "engine_type": "Gas" or "Diesel" or "Unknown",
        "axle_ratio": "Extract numerical rear ratio (e.g., 3.73, 4.10, 4.30) or null",
        "is_offroad_trim": 1 if (Power Wagon, Tremor, ZR2, AT4X) else 0,
        "payload_capacity_lbs": Integer or null,
        "has_towing_package": 1 if (heavy duty tow package, integrated brake controller, or max trailer tow explicitly mentioned) else 0,
        "ai_towing_summary": "1-2 short, spec-heavy, non-generic sentences detailing hardware and value."
    }}
    """
    try:
        response = ai_client.messages.create(
            model="claude-sonnet-5",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )

        text_content = ""
        for block in response.content:
            if getattr(block, "type", "") == "text":
                text_content += block.text
        text_content = text_content.strip()

        if text_content.startswith("```"):
            text_content = text_content.split("\n", 1)[1].rsplit("\n", 1)[0]
            if text_content.startswith("json"):
                text_content = text_content.split("\n", 1)[1]

        return json.loads(text_content)
    except Exception as e:
        print(f"  AI Processing Error: {e}")
        return None

# ==========================================
# 6. DATABASE HELPERS FOR SPOKES
# ==========================================
def save_raw_listing(clean_url, region_name):
    conn = sqlite3.connect('hd_truck_market.db')
    cursor = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT vin FROM hd_truck_market WHERE url = ?", (clean_url,))
    row = cursor.fetchone()

    if not row:
        temp_vin = f"TEMP_{hash(clean_url)}"
        cursor.execute('''
            INSERT OR IGNORE INTO hd_truck_market (vin, url, region_found, first_seen, last_seen, ai_processed)
            VALUES (?, ?, ?, ?, ?, 0)
        ''', (temp_vin, clean_url, region_name, today_str, today_str))
        conn.commit()
    conn.close()

def save_processed_truck(actual_vin, title, url, price_val, mileage_val, engine_str, ai_data, score, region_found, old_vin, orig_price_val=None, distance_miles=None):
    conn = sqlite3.connect('hd_truck_market.db')
    cursor = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")

    clean_price = safe_db_int(price_val, default=None)
    clean_orig_price = safe_db_int(orig_price_val, default=clean_price)
    clean_mileage = safe_db_int(mileage_val, default=None)
    clean_offroad = safe_db_int(ai_data.get('is_offroad_trim'), default=0)
    clean_payload = safe_db_int(ai_data.get('payload_capacity_lbs'), default=None)
    clean_towing = safe_db_int(ai_data.get('has_towing_package'), default=0)
    clean_score = safe_db_int(score, default=0)
    clean_distance = safe_db_int(distance_miles, default=None)

    cursor.execute('''
        INSERT INTO hd_truck_market (
            vin, title, url, current_price, original_price, mileage, engine,
            axle_ratio, is_offroad_trim, payload_capacity_lbs, has_towing_package,
            ai_towing_summary, ai_processed, region_found, first_seen, last_seen, airstream_readiness_score, distance_miles
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
        ON CONFLICT(vin) DO UPDATE SET
            current_price = excluded.current_price,
            original_price = COALESCE(hd_truck_market.original_price, excluded.original_price),
            mileage = COALESCE(excluded.mileage, hd_truck_market.mileage),
            engine = excluded.engine,
            axle_ratio = excluded.axle_ratio,
            is_offroad_trim = excluded.is_offroad_trim,
            payload_capacity_lbs = excluded.payload_capacity_lbs,
            has_towing_package = excluded.has_towing_package,
            ai_towing_summary = excluded.ai_towing_summary,
            ai_processed = 1,
            last_seen = excluded.last_seen,
            airstream_readiness_score = excluded.airstream_readiness_score,
            distance_miles = COALESCE(excluded.distance_miles, hd_truck_market.distance_miles)
    ''', (
        actual_vin, title, url, clean_price, clean_orig_price, clean_mileage, engine_str,
        ai_data.get('axle_ratio'), clean_offroad,
        clean_payload, clean_towing,
        ai_data.get('ai_towing_summary'), region_found, today_str, today_str, clean_score, clean_distance
    ))

    if old_vin != actual_vin and old_vin.startswith('TEMP_'):
        cursor.execute("DELETE FROM hd_truck_market WHERE vin = ?", (old_vin,))

    conn.commit()
    conn.close()

def remove_listing(vin, url):
    conn = sqlite3.connect('hd_truck_market.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM hd_truck_market WHERE vin = ? OR url = ?", (vin, url))
    conn.commit()
    conn.close()
