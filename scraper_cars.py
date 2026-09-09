import re
import json
import sqlite3
from bs4 import BeautifulSoup
import truck_hub

BATCH_LIMIT = 20

CARS_SEARCH_URLS = {
    "SoCal Local": "[https://www.cars.com/shopping/results/?stock_type=all&makes](https://www.cars.com/shopping/results/?stock_type=all&makes)[]=ford&makes[]=ram&models[]=ford-f_250&models[]=ram-2500&zip=92101&maximum_distance=150",
    "Desert Southwest": "[https://www.cars.com/shopping/results/?stock_type=all&makes](https://www.cars.com/shopping/results/?stock_type=all&makes)[]=ford&makes[]=ram&models[]=ford-f_250&models[]=ram-2500&zip=85001&maximum_distance=250",
    "Texas Hub": "[https://www.cars.com/shopping/results/?stock_type=all&makes](https://www.cars.com/shopping/results/?stock_type=all&makes)[]=ford&makes[]=ram&models[]=ford-f_250&models[]=ram-2500&zip=75001&maximum_distance=250"
}

def sweep_cars():
    print("=== Cars.com Search Sweep ===")
    for region, url in CARS_SEARCH_URLS.items():
        print(f"Scanning Cars.com {region}...")
        html = truck_hub.fetch_with_zenrows(url)
        if not html:
            continue
        soup = BeautifulSoup(html, 'html.parser')
        links = soup.find_all('a', href=re.compile(r'/vehicledetail/'))
        found = 0
        for a in links:
            href = a.get('href')
            if href:
                clean_url = "[https://www.cars.com](https://www.cars.com)" + href.split('?')[0] if href.startswith('/') else href.split('?')[0]
                truck_hub.save_raw_listing(clean_url, region)
                found += 1
        print(f"  Found {found} vehicle links in {region}")

def process_cars_batch():
    conn = sqlite3.connect('hd_truck_market.db')
    cursor = conn.cursor()
    cursor.execute("SELECT vin, url, region_found FROM hd_truck_market WHERE ai_processed = 0 AND url LIKE '%cars.com%' LIMIT ?", (BATCH_LIMIT,))
    queue = cursor.fetchall()
    conn.close()

    if not queue:
        print("No pending Cars.com listings to process.")
        return

    print(f"\n=== Cars.com AI Processing Batch (Max {len(queue)}) ===")
    for old_vin, url, region in queue:
        print(f"Processing Cars.com page: {url}")
        html = truck_hub.fetch_with_zenrows(url)
        if not html:
            continue

        soup = BeautifulSoup(html, 'html.parser')

        extracted_notes = []
        json_ld_data = {}
        lat, lon = None, None
        price_history_str = ""

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

        next_data_script = soup.find('script', id='__NEXT_DATA__')
        if next_data_script and next_data_script.string:
            try:
                next_json = json.loads(next_data_script.string)
                props = next_json.get('props', {}).get('pageProps', {})
                vdp = props.get('vehicle', {}) or props.get('vdp', {})
                
                if not lat and vdp.get('seller', {}).get('location'):
                    loc = vdp['seller']['location']
                    lat, lon = loc.get('latitude'), loc.get('longitude')

                if vdp.get('priceHistory'):
                    ph_list = [f"${p.get('price')} on {p.get('date')}" for p in vdp['priceHistory'] if p.get('price')]
                    price_history_str = " -> ".join(ph_list)

                if vdp.get('features'):
                    extracted_notes.append("FEATURES: " + ", ".join(vdp['features']))
                if vdp.get('sellerNotes'):
                    extracted_notes.append("SELLER NOTES: " + vdp['sellerNotes'])
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

        price_val = None
        price_el = soup.find(class_=re.compile(r'primary-price|price'))
        if price_el:
            price_val = truck_hub.safe_int(price_el.text)
        elif json_ld_data.get('offers', {}).get('price'):
            price_val = truck_hub.safe_int(json_ld_data['offers']['price'])

        mileage_val = None
        mileage_el = soup.find(string=re.compile(r'([\d,]+)\s*(mi\.|miles)', re.IGNORECASE))
        if mileage_el:
            mileage_val = truck_hub.safe_int(mileage_el)
        elif json_ld_data.get('mileageFromOdometer'):
            m_data = json_ld_data['mileageFromOdometer']
            mileage_val = truck_hub.safe_int(m_data.get('value') if isinstance(m_data, dict) else m_data)

        engine_str = json_ld_data.get('vehicleEngine', {}).get('engineType', '')
        if not engine_str:
            engine_el = soup.find(string=re.compile(r'engine', re.IGNORECASE))
            if engine_el and engine_el.parent:
                engine_str = engine_el.parent.text.strip()

        for sel in ['.sellers-notes', '.pdp-description', '.fancy-description', '[data-qa="seller-notes"]', '.features-and-specs']:
            found_el = soup.select_one(sel)
            if found_el:
                extracted_notes.append(found_el.get_text(separator=" ").strip())

        if not extracted_notes:
            extracted_notes.append(soup.get_text()[:4000])

        full_dealer_context = "\n".join(extracted_notes)

        # Fallback text regex for mileage
        if not mileage_val and full_dealer_context:
            m_match = re.search(r'([\d,]{2,7})\s*(?:miles|mile|mi\b)', full_dealer_context, re.IGNORECASE)
            if m_match:
                mileage_val = truck_hub.safe_int(m_match.group(1))

        distance_miles = truck_hub.calc_distance_from_sd(lat, lon)

        ai_data = truck_hub.analyze_truck_with_claude(
            title=title,
            engine_raw=engine_str,
            dealer_text=full_dealer_context,
            price=price_val,
            mileage=mileage_val,
            price_history=price_history_str
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
        print(f"  Processed Cars.com VIN: {actual_vin} | Score: {score}/100 | {dist_str} | Odo: {mileage_val or 'N/A'} | {title}")
