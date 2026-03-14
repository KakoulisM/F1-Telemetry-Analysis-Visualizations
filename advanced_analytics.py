"""
Advanced Analytics Runner
Orchestrates Racing Line Analysis and Tire Degradation predictions
"""
import sys
import argparse
from pathlib import Path
from pipeline_logger import get_logger
from racing_line_analyzer import analyze_session as analyze_racing_lines
from tire_degradation_model import (
    train_degradation_model, 
    predict_live_degradation
)


def main():
    """Main entry point for advanced analytics"""
    logger = get_logger()
    
    parser = argparse.ArgumentParser(
        description='F1 Advanced Analytics - Racing Lines & Tire Degradation'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Analytics command')
    
    # Racing Line Analysis
    racing_parser = subparsers.add_parser('racing-line', help='Analyze optimal racing lines')
    racing_parser.add_argument('session', help='Session prefix (e.g., Abu_Dhabi_Grand_Prix_Race)')
    racing_parser.add_argument('--driver', help='Specific driver code (e.g., VER)', default=None)
    racing_parser.add_argument('--no-snowflake', action='store_true', 
                              help='Skip Snowflake export')
    
    # Tire Degradation - Training
    train_parser = subparsers.add_parser('tire-train', help='Train tire degradation model')
    train_parser.add_argument('--sessions', nargs='+', 
                             help='Specific sessions to train on (default: all)')
    
    # Tire Degradation - Post-Session Analysis
    predict_parser = subparsers.add_parser('tire-predict', 
                                          help='Analyze tire degradation for completed session (2hr delay after finish)')
    predict_parser.add_argument('session', help='Session prefix')
    predict_parser.add_argument('--lookahead', type=int, default=3,
                               help='Number of laps model would have predicted ahead (default: 3)')
    
    # Full Analysis (both)
    full_parser = subparsers.add_parser('full-analysis', 
                                       help='Run complete analysis (racing lines + tire predictions)')
    full_parser.add_argument('session', help='Session prefix')
    full_parser.add_argument('--driver', help='Focus driver', default=None)
    full_parser.add_argument('--lookahead', type=int, default=3)
    
    args = parser.parse_args()
    
    if args.command == 'racing-line':
        logger.info("[START] Racing Line Analysis")
        analyze_racing_lines(
            args.session, 
            target_driver=args.driver,
            export_snowflake=not args.no_snowflake
        )
    
    elif args.command == 'tire-train':
        logger.info("[START] Training Tire Degradation Model")
        train_degradation_model(session_prefixes=args.sessions)
    
    elif args.command == 'tire-predict':
        logger.info("[START] Post-Session Tire Degradation Analysis")
        logger.info("(Data available 2 hours after session completion)")
        predict_live_degradation(args.session, lookahead=args.lookahead)
    
    elif args.command == 'full-analysis':
        logger.info("[START] Running Full Advanced Analytics Suite")
        logger.info("="*60)
        
        # Step 1: Racing Line Analysis
        logger.info("\n[1/2] Racing Line Analysis")
        logger.info("-"*60)
        analyze_racing_lines(args.session, target_driver=args.driver)
        
        # Step 2: Tire Degradation Analysis
        logger.info("\n[2/2] Post-Session Tire Degradation Analysis")
        logger.info("-"*60)
        predict_live_degradation(args.session, lookahead=args.lookahead)
        
        logger.info("\n" + "="*60)
        logger.info("[SUCCESS] FULL ANALYSIS COMPLETE")
        logger.info("="*60)
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
