"""
Quick Manual Pipeline Runner
For testing the full automated pipeline without waiting for scheduler

Usage:
  python run_pipeline_now.py                    # Auto-detect latest session
  python run_pipeline_now.py    # Specific session
  For each event, you can use the following session names (based on conventional format: FP1, FP2, FP3, Q, R):

[Event]_Practice_1
[Event]_Practice_2
[Event]_Practice_3
[Event]_Qualifying
[Event]_Race
For 2026, the events are:

Australian_Grand_Prix
Chinese_Grand_Prix
Japanese_Grand_Prix
Bahrain_Grand_Prix
Saudi_Arabian_Grand_Prix
Miami_Grand_Prix
Canadian_Grand_Prix
Monaco_Grand_Prix
Barcelona-Catalunya
Austrian_Grand_Prix
British_Grand_Prix
Belgian_Grand_Prix
Hungarian_Grand_Prix
Dutch_Grand_Prix
Italian_Grand_Prix
Spanish_Grand_Prix
Azerbaijan_Grand_Prix
Singapore_Grand_Prix
United_States_Grand_Prix
Mexico_City_Grand_Prix
São_Paulo_Grand_Prix
Las_Vegas_Grand_Prix
Qatar_Grand_Prix
Abu_Dhabi_Grand_Prix
Example command for each:
python run_pipeline_now.py --session Australian_Grand_Prix_Practice_1 --year 2026
python run_pipeline_now.py --session Australian_Grand_Prix_Practice_2 --year 2026
python run_pipeline_now.py --session Australian_Grand_Prix_Practice_3 --year 2026
python run_pipeline_now.py --session Australian_Grand_Prix_Qualifying --year 2026
python run_pipeline_now.py --session Australian_Grand_Prix_Race --year 2026

Conventional:

[Event]_Practice_1
[Event]_Practice_2
[Event]_Practice_3
[Event]_Qualifying
[Event]_Race
Sprint:

[Event]_Practice_1
[Event]_Qualifying
[Event]_Practice_2
[Event]_Sprint
[Event]_Race
Sprint Shootout:

[Event]_Practice_1
[Event]_Qualifying
[Event]_Sprint_Shootout
[Event]_Sprint
[Event]_Race
Sprint Qualifying:

[Event]_Practice_1
[Event]_Sprint_Qualifying
[Event]_Sprint
[Event]_Qualifying
[Event]_Race
"""
import sys
from master_pipeline import MasterPipeline

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Run F1 pipeline immediately')
    parser.add_argument('--session', help='Session name (e.g., Abu_Dhabi_Grand_Prix_Race)', default=None)
    parser.add_argument('--year', type=int, help='Year of the session (e.g., 2025)', default=None)

    args = parser.parse_args()

    print("\n" + "="*70)
    print("MANUAL PIPELINE EXECUTION")
    print("="*70)

    if args.session:
        print(f"Target Session: {args.session}")
    else:
        print("Target Session: Auto-detect latest")
    if args.year:
        print(f"Year: {args.year}")
    else:
        print("Year: Auto-detect")

    print("\nStarting full pipeline execution...")
    print("This will take approximately 6-8 minutes\n")

    pipeline = MasterPipeline(session_name=args.session, year=args.year)
    success = pipeline.run_full_pipeline()
    
    if success:
        print("\n[OK] Pipeline execution completed successfully!")
        print("Check driver_reports/ folder for individual driver analysis")
    else:
        print("\n[WARNING] Pipeline completed with some errors - check logs for details")
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
