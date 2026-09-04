import os
import json
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

DB_NAME = 'hd_truck_market.db'
DASHBOARD_URL = "https://swmagers.github.io/TruckFinder2/"

def fetch_analytics():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. Macro KPIs
    cursor.execute('''
        SELECT 
            COUNT(*),
            ROUND(AVG(current_price), 0),
            ROUND(AVG(mileage), 0),
            SUM(CASE WHEN airstream_readiness_score >= 75 THEN 1 ELSE 0 END),
            ROUND(AVG(payload_capacity_lbs), 0)
        FROM hd_truck_market 
        WHERE ai_processed = 1 AND title IS NOT NULL AND airstream_readiness_score > 0
    ''')
    total_trucks, avg_price, avg_miles, high_score_cnt, avg_payload = cursor.fetchone()

    # 2. Make/Model Breakdown
    cursor.execute('''
        SELECT 
            CASE 
                WHEN LOWER(title) LIKE '%ford%' OR LOWER(title) LIKE '%f-250%' OR LOWER(title) LIKE '%f250%' THEN 'Ford F-250'
                WHEN LOWER(title) LIKE '%ram%' THEN 'RAM 2500'
                WHEN LOWER(title) LIKE '%chevrolet%' OR LOWER(title) LIKE '%chevy%' OR LOWER(title) LIKE '%silverado%' THEN 'Chevy 2500'
                WHEN LOWER(title) LIKE '%gmc%' OR LOWER(title) LIKE '%sierra%' THEN 'GMC 2500'
                ELSE 'Other HD'
            END as make_model,
            COUNT(*) as count,
            ROUND(AVG(airstream_readiness_score), 1) as avg_score,
            ROUND(AVG(current_price), 0) as avg_price,
            ROUND(AVG(mileage), 0) as avg_miles
        FROM hd_truck_market
        WHERE ai_processed = 1 AND title IS NOT NULL AND airstream_readiness_score > 0
        GROUP BY make_model
        ORDER BY count DESC
    ''')
    model_stats = cursor.fetchall()

    # 3. Regional Analysis
    cursor.execute('''
        SELECT 
            COALESCE(region_found, 'Unknown') as region,
            COUNT(*) as count,
            ROUND(AVG(airstream_readiness_score), 1) as avg_score,
            ROUND(AVG(current_price), 0) as avg_price
        FROM hd_truck_market
        WHERE ai_processed = 1 AND title IS NOT NULL AND airstream_readiness_score > 0
        GROUP BY region
        ORDER BY count DESC
    ''')
    region_stats = cursor.fetchall()

    # 4. Engine Type Comparison (Gas vs Diesel)
    cursor.execute('''
        SELECT 
            CASE 
                WHEN LOWER(engine) LIKE '%diesel%' OR LOWER(engine) LIKE '%cummins%' OR LOWER(engine) LIKE '%powerstroke%' OR LOWER(engine) LIKE '%duramax%' OR LOWER(engine) LIKE '%6.7l%' THEN 'Diesel'
                WHEN LOWER(engine) LIKE '%gas%' OR LOWER(engine) LIKE '%7.3l%' OR LOWER(engine) LIKE '%6.4l%' OR LOWER(engine) LIKE '%6.6l%' THEN 'Gas'
                ELSE 'Unspecified'
            END as engine_type,
            COUNT(*) as count,
            ROUND(AVG(airstream_readiness_score), 1) as avg_score,
            ROUND(AVG(current_price), 0) as avg_price,
            ROUND(AVG(payload_capacity_lbs), 0) as avg_payload
        FROM hd_truck_market
        WHERE ai_processed = 1 AND title IS NOT NULL AND airstream_readiness_score > 0
        GROUP BY engine_type
    ''')
    engine_stats = cursor.fetchall()

    # 5. Price Drop / Deal Tracking
    cursor.execute('''
        SELECT title, original_price, current_price, (original_price - current_price) as price_drop, url, region_found
        FROM hd_truck_market
        WHERE original_price > current_price AND current_price > 0
        ORDER BY price_drop DESC
        LIMIT 5
    ''')
    price_drops = cursor.fetchall()

    conn.close()
    
    return {
        "total_trucks": total_trucks or 0,
        "avg_price": int(avg_price) if avg_price else 0,
        "avg_miles": int(avg_miles) if avg_miles else 0,
        "high_score_cnt": high_score_cnt or 0,
        "avg_payload": int(avg_payload) if avg_payload else 0,
        "model_stats": model_stats,
        "region_stats": region_stats,
        "engine_stats": engine_stats,
        "price_drops": price_drops
    }

def generate_dashboard():
    data = fetch_analytics()

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT title, current_price, mileage, engine, payload_capacity_lbs, 
               airstream_readiness_score, ai_towing_summary, url, region_found, last_seen
        FROM hd_truck_market
        WHERE ai_processed = 1 AND title IS NOT NULL AND airstream_readiness_score > 0
        ORDER BY airstream_readiness_score DESC, current_price ASC
    ''')
    trucks = cursor.fetchall()
    conn.close()

    # Model Metric Cards
    model_cards_html = ""
    for stat in data["model_stats"]:
        make_model, count, avg_score, avg_price, avg_miles = stat
        price_fmt = f"${int(avg_price):,}" if avg_price else "N/A"
        model_cards_html += f"""
        <div class="card">
            <div class="card-title">{make_model}</div>
            <div class="card-metric" style="color:#1a73e8;">{avg_score} <span class="sub">/100 avg</span></div>
            <div class="card-sub">Count: <strong>{count}</strong> | Avg: <strong>{price_fmt}</strong></div>
        </div>
        """

    # Engine Metric Cards
    engine_cards_html = ""
    for stat in data["engine_stats"]:
        eng_type, count, avg_score, avg_price, avg_payload = stat
        price_fmt = f"${int(avg_price):,}" if avg_price else "N/A"
        payload_fmt = f"{int(avg_payload):,} lbs" if avg_payload else "N/A"
        engine_cards_html += f"""
        <div class="card">
            <div class="card-title">{eng_type} Engine Option</div>
            <div class="card-metric" style="color:#2e7d32;">{avg_score} <span class="sub">/100 score</span></div>
            <div class="card-sub">Count: <strong>{count}</strong> | Avg Price: <strong>{price_fmt}</strong></div>
            <div class="card-sub">Avg Payload: <strong>{payload_fmt}</strong></div>
        </div>
        """

    # Price Drops Section
    price_drops_html = ""
    if data["price_drops"]:
        rows = ""
        for pd in data["price_drops"]:
            title, orig, curr, drop, url, reg = pd
            rows += f"""
            <tr>
                <td><a href="{url}" target="_blank" style="color:#1a73e8; font-weight:bold;">{title}</a></td>
                <td><span style="text-decoration:line-through; color:#888;">${orig:,}</span></td>
                <td style="color:#2e7d32; font-weight:bold;">${curr:,}</td>
                <td><span style="background:#e6f4ea; color:#137333; padding:2px 8px; border-radius:12px; font-weight:bold;">-${drop:,}</span></td>
                <td>{reg}</td>
            </tr>
            """
        price_drops_html = f"""
        <div class="section-container">
            <h2>🔥 Active Price Drops & Deals</h2>
            <table>
                <thead>
                    <tr><th>Vehicle</th><th>Was</th><th>Now</th><th>Price Reduction</th><th>Region</th></tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
        """

    # Table rows
    rows_html = ""
    for t in trucks:
        title, price, miles, engine, payload, score, summary, url, region, last_seen = t
        price_str = f"${price:,}" if price else "N/A"
        miles_str = f"{miles:,} mi" if miles else "N/A"
        payload_str = f"{payload:,} lbs" if payload else "N/A"
        score_color = "#2e7d32" if score >= 75 else ("#f57c00" if score >= 60 else "#c62828")

        rows_html += f"""
        <tr>
            <td style="font-weight:bold; color:{score_color}; font-size:1.1em;">{score}/100</td>
            <td><a href="{url}" target="_blank" style="text-decoration:none; color:#1a73e8; font-weight:bold;">{title}</a></td>
            <td>{price_str}</td>
            <td>{miles_str}</td>
            <td>{engine or 'Unknown'}</td>
            <td>{payload_str}</td>
            <td><span style="font-size:0.85em; background:#e8f0fe; padding:2px 6px; border-radius:4px;">{region}</span></td>
            <td style="font-size:0.9em; color:#555;">{summary or ''}</td>
        </tr>
        """

    # Chart JSON Data
    model_labels = [m[0] for m in data["model_stats"]]
    model_scores = [m[2] for m in data["model_stats"]]
    model_prices = [m[3] for m in data["model_stats"]]

    region_labels = [r[0] for r in data["region_stats"]]
    region_counts = [r[1] for r in data["region_stats"]]
    region_prices = [r[3] for r in data["region_stats"]]

    engine_labels = [e[0] for e in data["engine_stats"]]
    engine_counts = [e[1] for e in data["engine_stats"]]

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Airstream Safari HD Truck Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background: #f8f9fa; color: #333; }}
        h1 {{ margin-bottom: 2px; }}
        h2 {{ font-size: 1.2em; color: #444; margin-top: 0; }}
        p {{ color: #666; margin-top: 0; margin-bottom: 20px; }}
        .grid {{ display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px; }}
        .card {{ background: white; padding: 15px 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); min-width: 170px; flex: 1; }}
        .card-title {{ font-size: 0.8em; color: #666; font-weight: bold; text-transform: uppercase; }}
        .card-metric {{ font-size: 1.6em; font-weight: bold; margin: 4px 0; }}
        .card-sub {{ font-size: 0.82em; color: #555; margin-top: 2px; }}
        .sub {{ font-size: 0.5em; color: #888; font-weight: normal; }}
        .kpi-card {{ background: #1a73e8; color: white; min-width: 150px; }}
        .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; margin-bottom: 25px; }}
        .chart-container {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .section-container {{ background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 20px; margin-bottom: 25px; overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; }}
        th, td {{ padding: 10px 12px; border-bottom: 1px solid #eee; }}
        th {{ background: #f1f3f4; font-weight: 600; color: #444; }}
        tr:hover {{ background: #f8f9fa; }}
    </style>
</head>
<body>
    <h1>🚚 HD Truck Market Intelligence</h1>
    <p>Target: 2007 Airstream Safari 25' (7,000 lbs GVWR) | Live Market Analytics</p>
    
    <!-- KPI Row -->
    <div class="grid">
        <div class="card kpi-card">
            <div class="card-title" style="color:rgba(255,255,255,0.8);">Total Listings</div>
            <div class="card-metric">{data["total_trucks"]}</div>
            <div class="card-sub" style="color:rgba(255,255,255,0.9);">Tracked Vehicles</div>
        </div>
        <div class="card kpi-card" style="background:#2e7d32;">
            <div class="card-title" style="color:rgba(255,255,255,0.8);">High Match Count</div>
            <div class="card-metric">{data["high_score_cnt"]}</div>
            <div class="card-sub" style="color:rgba(255,255,255,0.9);">Score $\ge$ 75/100</div>
        </div>
        <div class="card">
            <div class="card-title">Average HD Price</div>
            <div class="card-metric">${data["avg_price"]:,}</div>
            <div class="card-sub">Market Average</div>
        </div>
        <div class="card">
            <div class="card-title">Average Mileage</div>
            <div class="card-metric">{data["avg_miles"]:,} <span class="sub">mi</span></div>
            <div class="card-sub">Odometer Average</div>
        </div>
        <div class="card">
            <div class="card-title">Average Payload</div>
            <div class="card-metric">{data["avg_payload"]:,} <span class="sub">lbs</span></div>
            <div class="card-sub">Target: >1,500 lbs</div>
        </div>
    </div>

    <!-- Models & Engines Grid -->
    <div class="grid">
        {model_cards_html}
    </div>
    <div class="grid">
        {engine_cards_html}
    </div>

    {price_drops_html}

    <!-- Interactive Charts Row -->
    <div class="charts-grid">
        <div class="chart-container">
            <h2>Readiness Score by Make/Model</h2>
            <canvas id="modelScoreChart"></canvas>
        </div>
        <div class="chart-container">
            <h2>Regional Inventory Volume</h2>
            <canvas id="regionChart"></canvas>
        </div>
        <div class="chart-container">
            <h2>Engine Mix (Gas vs Diesel)</h2>
            <canvas id="engineMixChart"></canvas>
        </div>
    </div>

    <!-- Full Database Table -->
    <div class="section-container">
        <h2>All Active HD Truck Listings</h2>
        <table>
            <thead>
                <tr>
                    <th>Score</th>
                    <th>Vehicle Description</th>
                    <th>Price</th>
                    <th>Mileage</th>
                    <th>Engine</th>
                    <th>Payload</th>
                    <th>Region</th>
                    <th>Claude AI Towing Assessment</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>

    <script>
        // Chart 1: Model Scores
        new Chart(document.getElementById('modelScoreChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(model_labels)},
                datasets: [{{
                    label: 'Avg Airstream Readiness Score',
                    data: {json.dumps(model_scores)},
                    backgroundColor: '#1a73e8'
                }}]
            }},
            options: {{ responsive: true, scales: {{ y: {{ min: 0, max: 100 }} }} }}
        }});

        // Chart 2: Regional Counts
        new Chart(document.getElementById('regionChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(region_labels)},
                datasets: [{{
                    label: 'Vehicle Count',
                    data: {json.dumps(region_counts)},
                    backgroundColor: '#f57c00'
                }}]
            }},
            options: {{ responsive: true }}
        }});

        // Chart 3: Engine Mix
        new Chart(document.getElementById('engineMixChart'), {{
            type: 'doughnut',
            data: {{
                labels: {json.dumps(engine_labels)},
                datasets: [{{
                    data: {json.dumps(engine_counts)},
                    backgroundColor: ['#2e7d32', '#1a73e8', '#9e9e9e']
                }}]
            }},
            options: {{ responsive: true }}
        }});
    </script>
</body>
</html>
"""
    with open("index.html", "w") as f:
        f.write(html_content)
    print("Dashboard index.html generated with interactive charts and analytics.")

def send_email_digest():
    user = os.getenv('EMAIL_USER')
    password = os.getenv('EMAIL_PASS')
    if not user or not password:
        print("Skipping email digest: EMAIL_USER or EMAIL_PASS environment variables not set.")
        return

    data = fetch_analytics()

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT title, current_price, mileage, engine, airstream_readiness_score, ai_towing_summary, url, region_found
        FROM hd_truck_market
        WHERE ai_processed = 1 AND title IS NOT NULL AND airstream_readiness_score >= 65
        ORDER BY airstream_readiness_score DESC, current_price ASC
        LIMIT 5
    ''')
    top_picks = cursor.fetchall()
    conn.close()

    if not top_picks:
        print("No high-scoring trucks to report in email digest.")
        return

    # Model Summary Bullets
    stats_summary_html = ""
    for stat in data["model_stats"]:
        make_model, count, avg_score, avg_price, avg_miles = stat
        price_fmt = f"${int(avg_price):,}" if avg_price else "N/A"
        stats_summary_html += f"<li><strong>{make_model}:</strong> {count} trucks | Avg Score: {avg_score}/100 | Avg Price: {price_fmt}</li>"

    # Regional Summary Bullets
    region_summary_html = ""
    for r in data["region_stats"]:
        reg, count, avg_score, avg_price = r
        price_fmt = f"${int(avg_price):,}" if avg_price else "N/A"
        region_summary_html += f"<li><strong>{reg}:</strong> {count} listings | Avg Price: {price_fmt}</li>"

    # Top Picks
    items_html = ""
    for t in top_picks:
        title, price, miles, engine, score, summary, url, region = t
        price_str = f"${price:,}" if price else "N/A"
        miles_str = f"{miles:,} mi" if miles else "N/A"
        
        items_html += f"""
        <div style="border-left: 4px solid #1a73e8; padding-left: 12px; margin-bottom: 20px;">
            <h3 style="margin:0 0 5px 0;"><a href="{url}" style="color:#1a73e8; text-decoration:none;">{title}</a></h3>
            <p style="margin:0 0 5px 0; font-weight:bold; color:#333;">
                Score: <span style="color:#2e7d32;">{score}/100</span> | Price: {price_str} | Odometer: {miles_str} | Region: {region}
            </p>
            <p style="margin:0; font-size:0.9em; color:#555;"><em>"{summary}"</em></p>
        </div>
        """

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🚚 Market Digest: {data['total_trucks']} HD Trucks Tracked | Top Picks Inside"
    msg['From'] = user
    msg['To'] = user

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5;">
        <h2>HD Truck Market Executive Update</h2>
        
        <p><strong>Market Overview ({data['total_trucks']} total trucks tracked):</strong></p>
        <ul>
            {stats_summary_html}
        </ul>

        <p><strong>Regional Breakdown:</strong></p>
        <ul>
            {region_summary_html}
        </ul>

        <h3>Top Airstream-Ready Picks</h3>
        {items_html}

        <p style="margin-top:25px;">
            <a href="{DASHBOARD_URL}" style="background-color:#1a73e8; color:white; padding:10px 18px; text-decoration:none; border-radius:4px; font-weight:bold;">View Interactive Web Charts & Dashboard</a>
        </p>
    </body>
    </html>
    """
    msg.attach(MIMEText(html_body, 'html'))

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(user, password)
        server.sendmail(user, user, msg.as_string())
        server.quit()
        print("Email digest sent successfully.")
    except Exception as e:
        print(f"Failed to send email digest: {e}")

if __name__ == "__main__":
    generate_dashboard()
    send_email_digest()
