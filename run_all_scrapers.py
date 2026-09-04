import truck_hub
import scraper_cars
import scraper_autotrader
import reporter

def main():
    print("=== Starting Automated HD Truck Market Pipeline ===")
    
    # 1. Initialize Database & Clean Artifacts
    truck_hub.init_db()
    truck_hub.purge_non_hd_records()

    # 2. Execute Cars.com Spoke
    print("\n--- Initiating Cars.com Sweep ---")
    scraper_cars.sweep_cars()
    scraper_cars.process_cars_batch()

    # 3. Execute Autotrader Spoke
    print("\n--- Initiating Autotrader Sweep ---")
    scraper_autotrader.sweep_autotrader()
    scraper_autotrader.process_autotrader_batch()

    # 4. Generate Dashboard & Send Digest
    print("\n--- Generating Reports & Email Digest ---")
    reporter.generate_dashboard()
    reporter.send_email_digest()

    print("\n=== Pipeline Complete ===")

if __name__ == "__main__":
    main()
