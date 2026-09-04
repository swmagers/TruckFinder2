import re
import time
import random
import sqlite3
from bs4 import BeautifulSoup
import truck_hub

MAX_AI_BATCH_SIZE = 50

AUTOTRADER_REGIONS = [
    {
        "name": "SoCal Local",
        "url": (
            "[https://www.autotrader.com/cars-for-sale/all-cars/used-cars/sandiego-ca](https://www.autotrader.com/cars-for-sale/all-cars/used-cars/sandiego-ca)?"
            "zip=92101&distance=200&maxPrice=50000&maxMileage=150000&startYear=2019&"
            "vehicleStyleCodes=TRUCKS&modelCodeList=F250,RAM2500,CHEV2500,GMC2500"
        )
    },
    {
        "name": "Desert Southwest",
        "url": (
            "[https://www.autotrader.com/cars-for-sale/all-cars/used-cars/phoenix-az](https://www.autotrader.com/cars-for-sale/all-cars/used-cars/phoenix-az)?"
            "zip=85001&distance=250&maxPrice=50000&maxMileage=150000&startYear=2019&"
            "vehicleStyleCodes=TRUCKS&modelCodeList=F250,RAM2500,CHEV2500,GMC2500"
        )
    },
    {
        "name": "Texas Hub",
        "url": (
            "https://www.autotrader.com/cars-for-sale/all-cars/used-cars/temple-tx?"
            "zip=76501&distance=225&maxPrice=50000&maxMileage=150000&startYear=2019&"
            "vehicleStyleCodes=TRUCKS&modelCodeList=F250,RAM2500,CHEV2500,GMC2500"
        )
    }
]

def sweep_autotrader():
    print("=== Autotrader Search Sweep ===")
    for region in AUTOTRADER_REGIONS:
        print(f"Scanning Autotrader {region['name']}...")
        html = truck_hub.fetch_with_zenrows(region['url'], wait_time='4000')
        if not html:
            continue

        soup = BeautifulSoup(html, 'html.parser')
        detail_links = soup.select('a[href*="/cars-for-sale/vehicledetail"], a[href*="/cars-for-sale/vehicle/"]')
        seen_urls = set()

        for a in detail_links:
            href = a['href']
            clean_url = f"[https://www.autotrader.com](https://www.autotrader.com){href}" if href.startswith('/') else href
            clean_url = clean_url.split('?')[0]

            if clean_url not in seen_urls:
                seen_urls.add(clean_url)
                truck_hub.save_raw_listing(clean_url, region['name'])

        print(f"  Found {len(seen_urls)} vehicle links in {region['name']}")

def process_autotrader_batch():
    print(f"\n=== Autotrader AI Processing Batch (Max {MAX_AI_BATCH_SIZE}) ===")
    conn = sqlite3.connect('hd_truck_market.db')
    cursor = conn.cursor()
    cursor.execute("SELECT vin, url, region_found FROM hd_truck_market WHERE ai_processed = 0 AND url LIKE '%autotrader.com%' LIMIT ?", (MAX_AI_BATCH_SIZE,))
    pending = cursor.fetchall()
    conn.close()

    if not pending:
        print("No pending Autotrader listings.")
        return

    for old_vin, url, region_found in pending:
        print(f"Processing Autotrader page: {url}")
        html = truck_hub.fetch_with_zenrows(url, wait_time='4000')
        if not html:
            continue

        soup = BeautifulSoup(html, 'html.parser')
        h1 = soup.find('h1')
        title = h1.text.strip() if h1 else "Unknown Truck"

        if not truck_hub.is_valid_hd_truck(title):
            print(f"  Purging non-HD record: {title}")
            truck_hub.remove_listing(old_vin, url)
            continue

        vin_match = re.search(r'\b[A-HJ-NPR-Z0-9]{17}\b', html)
        actual_vin = vin_match.group(0) if vin_match else old_vin

        price_match = re.search(r'\$\d{2,3},\d{3}', html)
        price_val = truck_hub.safe_int(price_match.group(0)) if price_match else None

        mileage_match = re.search(r'(\d[\d,]*)\s*(?:mi|miles)', html, re.IGNORECASE)
        mileage_val = truck_hub.safe_int(mileage_match.group(1)) if mileage_match else None

        engine_match = re.search(r'\b(6\.7L|6\.4L|7\.3L|6\.6L|6\.2L|6\.0L)\b[^\n<,]*', html, re.IGNORECASE)
        engine_str = engine_match.group(0).strip() if engine_match else "Unknown"

        notes_elem = soup.select_one('div[class*="description"], div[class*="seller-notes"], #seller-notes')
        seller_notes = notes_elem.text.strip() if notes_elem else html[:3000]

        ai_data = truck_hub.analyze_truck_with_claude(title, engine_str, seller_notes)

        if ai_data:
            score = truck_hub.calculate_readiness_score(
                title=title,
                engine_str=engine_str,
                is_offroad_trim=ai_data.get('is_offroad_trim', 0),
                price=price_val,
                region_found=region_found,
                has_towing_pkg=ai_data.get('has_towing_package', 0),
                axle_ratio=ai_data.get('axle_ratio'),
                payload_lbs=ai_data.get('payload_capacity_lbs')
            )

            truck_hub.save_processed_truck(
                actual_vin, title, url, price_val, mileage_val, engine_str,
                ai_data, score, region_found, old_vin
            )
            print(f"  Processed Autotrader VIN: {actual_vin} | Score: {score}/100 | {title}")

        time.sleep(random.uniform(3.5, 6.0))

if __name__ == "__main__":
    truck_hub.init_db()
    truck_hub.purge_non_hd_records()
    sweep_autotrader()
    process_autotrader_batch()
