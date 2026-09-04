import os
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

DB_NAME = 'hd_truck_market.db'
DASHBOARD_URL = "https://swmagers.github.io/TruckFinder2/"

def generate_dashboard():
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

    rows_html = ""
    for t in trucks:
        title, price, miles, engine, payload, score, summary, url, region, last_seen = t
        price_str = f"${price:,}" if price else "N/A"
        miles_str = f"{miles:,} mi" if miles else "N/A"
        payload_str = f"{payload:,} lbs" if payload else "N/A"
        
        # Color-code scores
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

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Airstream Safari HD Truck Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background: #f8f9fa; color: #333; }}
        h1 {{ margin-bottom: 5px; }}
        p {{ color: #666; margin-top: 0; }}
        .table-container {{ overflow-x: auto; background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 15px; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; }}
        th, td {{ padding: 12px 10px; border-bottom: 1px solid #eee; }}
        th {{ background: #f1f3f4; font-weight: 600; color: #444; }}
        tr:hover {{ background: #f8f9fa; }}
    </style>
</head>
<body>
    <h1>🚚 HD Truck Market Intelligence</h1>
    <p>Target: 2007 Airstream Safari 25' (7,000 lbs GVWR) | Updated Automatically</p>
    <div class="table-container">
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
</body>
</html>
"""
    with open("index.html", "w") as f:
        f.write(html_content)
    print("Dashboard index.html generated successfully.")

def send_email_digest():
    user = os.getenv('EMAIL_USER')
    password = os.getenv('EMAIL_PASS')
    if not user or not password:
        print("Skipping email digest: EMAIL_USER or EMAIL_PASS environment variables not set.")
        return

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
    msg['Subject'] = f"🚚 HD Truck Market Digest: Top Picks Identified"
    msg['From'] = user
    msg['To'] = user

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5;">
        <h2>HD Truck Market Update</h2>
        <p>Here are your top-scoring heavy-duty truck matches for your Airstream Safari from the latest market sweep:</p>
        {items_html}
        <p style="margin-top:25px;">
            <a href="{DASHBOARD_URL}" style="background-color:#1a73e8; color:white; padding:10px 18px; text-decoration:none; border-radius:4px; font-weight:bold;">View Complete Live Dashboard</a>
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
