"""
Paragon Agro Ltd. - One-Click Database Migration to Turso Cloud
Usage:
  python migrate_to_turso.py --url <TURSO_DB_URL> --token <TURSO_AUTH_TOKEN>
Or set environment variables:
  set TURSO_DATABASE_URL=https://...
  set TURSO_AUTH_TOKEN=...
  python migrate_to_turso.py
"""

import os
import sys
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(BASE_DIR, "portal", "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from turso_client import test_turso_connection, migrate_local_sqlite_to_turso

def main():
    parser = argparse.ArgumentParser(description="Migrate local SQLite database to Turso Cloud")
    parser.add_argument("--url", default=os.environ.get("TURSO_DATABASE_URL"), help="Turso Database URL (https://... or libsql://...)")
    parser.add_argument("--token", default=os.environ.get("TURSO_AUTH_TOKEN"), help="Turso Auth Token (Bearer token)")
    parser.add_argument("--db-path", default=os.path.join(BACKEND_DIR, "planning_system.db"), help="Path to local planning_system.db")
    args = parser.parse_args()

    print("=" * 70)
    print("   PARAGON AGRO LIMITED - TURSO CLOUD DATABASE MIGRATION")
    print("=" * 70)

    url = args.url
    token = args.token

    if not url:
        url = input("Enter Turso Database URL (e.g. https://paragon-planning-yourorg.turso.io): ").strip()
    if not token:
        token = input("Enter Turso Auth Token: ").strip()

    if not url or not token:
        print("[!] Error: Turso Database URL and Auth Token are required!")
        sys.exit(1)

    print(f"\n[1/3] Testing live connection to Turso Cloud at: {url} ...")
    test_res = test_turso_connection(url, token)
    if not test_res.get("success"):
        print(f"[X] Connection Failed: {test_res.get('error')}")
        sys.exit(1)

    print(f"[✓] Connected successfully! Remote SQLite Version: {test_res.get('sqlite_version')}")
    print(f"    Server Time: {test_res.get('server_time')}")

    print(f"\n[2/3] Reading local database: {args.db_path} ...")
    if not os.path.exists(args.db_path):
        print(f"[X] Local database file not found at: {args.db_path}")
        sys.exit(1)

    print("\n[3/3] Migrating schema and records to Turso Cloud...")
    mig_res = migrate_local_sqlite_to_turso(args.db_path, url, token)
    if not mig_res.get("success"):
        print(f"[X] Migration Failed: {mig_res.get('error')}")
        sys.exit(1)

    print("\n" + "=" * 70)
    print(f"[✓] SUCCESS! {mig_res.get('message')}")
    print("=" * 70)
    print("\nMigrated Tables Summary:")
    for t in mig_res.get("migrated_tables", []):
        print(f"  • {t['table']:<28}: {t['rows']:>5} rows")

    print("\n[Next Steps]:")
    print("1. Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in your Vercel Environment Variables.")
    print("2. The web portal will now automatically read & write to your shared Turso Cloud Database!")
    print("3. Multiple users on separate internet connections can now work simultaneously.")

if __name__ == "__main__":
    main()
