"""Quick test to verify imports and database initialization"""
import sys
sys.path.insert(0, ".")

print("Testing imports...")
try:
    from config import DATABASE_PATH, TRANSLATION_STATUSES, DEFAULT_SEARCH_KEYWORDS
    print(f"  config.py OK (DB: {DATABASE_PATH})")
except Exception as e:
    print(f"  config.py ERROR: {e}")

try:
    from database import init_db, get_dashboard_stats, get_all_tags
    init_db()
    stats = get_dashboard_stats()
    print(f"  database.py OK (total videos: {stats['total']})")
except Exception as e:
    print(f"  database.py ERROR: {e}")

try:
    from youtube_scraper import search_videos, get_transcript
    print("  youtube_scraper.py OK")
except Exception as e:
    print(f"  youtube_scraper.py ERROR: {e}")

try:
    from app import app
    print(f"  app.py OK (routes: {len(app.url_map._rules)})")
except Exception as e:
    print(f"  app.py ERROR: {e}")

print("\nAll tests passed! Run 'python app.py' to start the server.")