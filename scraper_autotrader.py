import re
import json
import sqlite3
from bs4 import BeautifulSoup
import truck_hub

BATCH_LIMIT = 20

AUTOTRADER_SEARCH_URLS = {
    "SoCal Local": "[https://www.autotrader.com/cars-for-sale/all-cars/ford/f250/san-diego-ca?zip=92101&distance=150](https://www.autotrader.com/cars-for-sale/all-cars/ford/f250/san-diego-ca?zip=92101&distance=150)",
    "Desert Southwest": "[https://www.autotrader.com/cars-for-sale/all-cars/ford/f250/phoenix-az?zip=85001&distance=250](https://www.autotrader.com/cars-for-sale/all-cars/ford/f250/phoenix-az?zip=85001&distance=250)",
    "Texas Hub": "[https://www.autotrader.com/cars-for-sale/all-cars/ford/f250/dallas-tx?zip=75001&distance=250](https://www.autotrader.com/cars-for-sale/all-cars/ford/f250/dallas-tx?zip=75001&distance=250)"
}

def sweep_autotrader():
    print("=== Autotrader Search Sweep ===")
    for region, url in AUTOTRADER_SEARCH_URLS.items():
        print(f"Scanning Autotrader {region}...")
        html = truck_hub.fetch_with_zenrows(url)
        if not html:
            continue
        soup = BeautifulSoup(html, 'html.parser')
        links = soup.find_all('a', href=re.compile(r'/cars-for-sale/vehicle/'))
        found = 0
        for a in links:
            href = a.get('href')
            if href:
                clean_url = "[https://www.autotrader.com](https://www.autotrader.com)" + href.split('?')[0] if href.startswith('/') else href.split('?')[0]
                truck_hub.save_raw_listing(clean_url, region)
                found += 1
        print(f"  Found {found} vehicle links in {region}")

def process_autotrader_batch():
    conn = sqlite3.connect('hd_truck_market.db')
    cursor = conn.cursor()
    cursor.execute("SELECT vin, url, region_found FROM hd_truck_market WHERE ai_processed = 0 AND url LIKE '%autotrader.com%' LIMIT ?", (BATCH_LIMIT,))
    queue = cursor.fetchall()
    conn.close()

    if not queue:
        print("No pending Autotrader listings to process.")
        return

    print(f"\n=== Autotrader AI Processing Batch (Max {len(queue)}) ===")
    for old_vin, url, region in queue:
        print(f"Processing Autotrader page: {url}")
        html = truck_hub.fetch_with_zenrows(url)
        if not html:
            continue

        soup = BeautifulSoup(html, 'html.parser')

        # Deep JSON Extraction
        json_ld_data = {}
        lat, lon = None, None
        extracted_notes = []

        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string or '{}')
                if isinstance(data, dict):
                    if data.get('@type') in ['Car', 'Vehicle', 'Product']:
                        json_ld_data = data
                    elif data.get('@type') in ['AutoDealer', 'LocalBusiness']:
                        geo = data.get('geo', {})
                        lat, lon = geo.get('latitude'), geo.get('longitude')
            except Exception:
                pass

        title_el = soup.find('h1')
        title = title_el.text.strip() if title_el else json_ld_data.get('name', 'Unknown Truck')

        if not truck_hub.is_valid_hd_truck(title):
            print(f"  Purging non-HD record: {title}")
            truck_hub.remove_listing(old_vin, url)
            continue

        vin_match = re.search(r'([A-HJ-NPR-Z0-9]{17})', html)
        actual_vin = vin_match.group(1) if vin_match else json_ld_data.get('vehicleIdentificationNumber', old_vin)

        # Price
        price_val = None
        price_el = soup.find(class_=re.compile(r'first-price|price'))
        if price_el:
            price_val = truck_hub.safe_int(price_el.text)
        elif json_ld_data.get('offers', {}).get('price'):
            price_val = truck_hub.safe_int(json_ld_data['offers']['price'])

        # Mileage
        mileage_val = None
        mileage_el = soup.find(string=re.compile(r'([\d,]+)\s*miles', re.IGNORECASE))
        if mileage_el:
            mileage_val = truck_hub.safe_int(mileage_el)
        elif json_ld_data.get('mileageFromOdometer'):
            m_data = json_ld_data['mileageFromOdometer']
            mileage_val = truck_hub.safe_int(m_data.get('value') if isinstance(m_data, dict) else m_data)

        # Engine
        engine_str = json_ld_data.get('vehicleEngine', {}).get('engineType', '')
        if not engine_str:
            engine_el = soup.find(string=re.compile(r'engine', re.IGNORECASE))
            if engine_el and engine_el.parent:
                engine_str = engine_el.parent.text.strip()

        # Description / Specs
        if json_ld_data.get('description'):
            extracted_notes.append(json_ld_data['description'])
        else:
            extracted_notes.append(soup.get_text()[:4000])

        full_dealer_context = "\n".join(extracted_notes)
        distance_miles = truck_hub.calc_distance_from_sd(lat, lon)

        ai_data = truck_hub.analyze_truck_with_claude(
            title=title,
            engine_raw=engine_str,
            dealer_text=full_dealer_context,
            price=price_val,
            mileage=mileage_val
        )
        if not ai_data:
            ai_data = {
                "engine_type": "Unknown",
                "axle_ratio": None,
                "is_offroad_trim": 0,
                "payload_capacity_lbs": None,
                "has_towing_package": 0,
                "ai_towing_summary": "AI processing unverified."
            }

        score = truck_hub.calculate_readiness_score(
            title=title,
            engine_str=engine_str or ai_data.get('engine_type', ''),
            is_offroad_trim=ai_data.get('is_offroad_trim', 0),
            price=price_val,
            region_found=region,
            has_towing_pkg=ai_data.get('has_towing_package', 0),
            axle_ratio=ai_data.get('axle_ratio'),
            payload_lbs=ai_data.get('payload_capacity_lbs'),
            distance_miles=distance_miles
        )

        truck_hub.save_processed_truck(
            actual_vin=actual_vin,
            title=title,
            url=url,
            price_val=price_val,
            mileage_val=mileage_val,
            engine_str=engine_str,
            ai_data=ai_data,
            score=score,
            region_found=region,
            old_vin=old_vin,
            distance_miles=distance_miles
        )
        dist_str = f"{distance_miles} mi to SD" if distance_miles else region
        print(f"  Processed Autotrader VIN: {actual_vin} | Score: {score}/100 | {dist_str} | {title}")
