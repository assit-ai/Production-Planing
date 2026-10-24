"""
Turso Cloud Database Client & Migration Engine
Paragon Agro Ltd. - Production & Store Planning Portal

Provides seamless cloud-native database connectivity for multi-user access across separate internet connections.
Features:
- Pure HTTP/REST pipeline (zero C-extension dependency, works everywhere including Vercel Serverless).
- Full compatibility with sqlite3 interface (.execute(), .fetchall(), .fetchone(), Row-like dict access).
- One-click migration from local SQLite (planning_system.db) to Turso Cloud.
"""

import os
import json
import sqlite3
import requests
from typing import Any, List, Dict, Optional

class TursoRow(dict):
    """Provides both dict-style ['col'] and attribute/index-style access like sqlite3.Row."""
    def __init__(self, cols: List[str], vals: List[Any]):
        super().__init__()
        self._cols = cols
        self._vals = vals
        for c, v in zip(cols, vals):
            self[c] = v

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return super().__getitem__(key)

    def keys(self):
        return self._cols

    def values(self):
        return self._vals


class TursoCursor:
    def __init__(self, conn: 'TursoConnection'):
        self.conn = conn
        self._results = []
        self._cols = []
        self.lastrowid = None
        self.rowcount = 0

    def execute(self, sql: str, params: Optional[tuple] = None):
        res = self.conn.execute_stmt(sql, params)
        self._cols = res.get("cols", [])
        self._results = res.get("rows", [])
        self.rowcount = res.get("affected_row_count", 0)
        self.lastrowid = res.get("last_insert_rowid")
        return self

    def fetchall(self) -> List[TursoRow]:
        return [TursoRow(self._cols, r) for r in self._results]

    def fetchone(self) -> Optional[TursoRow]:
        if not self._results:
            return None
        return TursoRow(self._cols, self._results[0])


class TursoConnection:
    def __init__(self, database_url: str, auth_token: str):
        # Normalize database URL to https://
        clean_url = database_url.strip()
        if clean_url.startswith("libsql://"):
            clean_url = "https://" + clean_url[len("libsql://"):]
        elif not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = "https://" + clean_url

        clean_url = clean_url.rstrip("/")
        if not clean_url.endswith("/v2/pipeline"):
            self.endpoint = f"{clean_url}/v2/pipeline"
        else:
            self.endpoint = clean_url

        self.auth_token = auth_token.strip()
        self.headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }

    def _serialize_arg(self, val: Any) -> dict:
        if val is None:
            return {"type": "null"}
        elif isinstance(val, bool):
            return {"type": "integer", "value": str(int(val))}
        elif isinstance(val, int):
            return {"type": "integer", "value": str(val)}
        elif isinstance(val, float):
            return {"type": "float", "value": val}
        elif isinstance(val, (bytes, bytearray)):
            import base64
            return {"type": "blob", "base64": base64.b64encode(val).decode("ascii")}
        else:
            return {"type": "text", "value": str(val)}

    def _parse_row_val(self, val_obj: dict) -> Any:
        if not isinstance(val_obj, dict):
            return val_obj
        t = val_obj.get("type")
        v = val_obj.get("value")
        if t == "null":
            return None
        elif t == "integer":
            return int(v) if v is not None else 0
        elif t == "float":
            return float(v) if v is not None else 0.0
        elif t == "blob":
            import base64
            return base64.b64decode(val_obj.get("base64", ""))
        else:
            return v

    def execute_stmt(self, sql: str, params: Optional[tuple] = None) -> dict:
        stmt_args = []
        if params:
            for p in params:
                stmt_args.append(self._serialize_arg(p))

        payload = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": sql,
                        "args": stmt_args
                    }
                },
                {"type": "close"}
            ]
        }

        try:
            resp = requests.post(self.endpoint, headers=self.headers, json=payload, timeout=20)
            if resp.status_code != 200:
                raise Exception(f"Turso HTTP {resp.status_code}: {resp.text}")

            data = resp.json()
            results = data.get("results", [])
            if not results:
                return {"cols": [], "rows": [], "affected_row_count": 0, "last_insert_rowid": None}

            first_res = results[0]
            if first_res.get("type") == "error":
                raise Exception(first_res.get("error", {}).get("message", "Unknown Turso Error"))

            exec_res = first_res.get("response", {}).get("result", {})
            cols = [c["name"] for c in exec_res.get("cols", [])]
            raw_rows = exec_res.get("rows", [])

            parsed_rows = []
            for r in raw_rows:
                parsed_rows.append([self._parse_row_val(cell) for cell in r])

            return {
                "cols": cols,
                "rows": parsed_rows,
                "affected_row_count": exec_res.get("affected_row_count", 0),
                "last_insert_rowid": exec_res.get("last_insert_rowid")
            }
        except Exception as e:
            raise e

    def execute_batch(self, stmts: List[tuple]) -> list:
        """
        Executes a batch of (sql, params) statements in a single Turso pipeline HTTP request.
        Drastically improves migration and bulk operation speed across WAN.
        """
        if not stmts:
            return []

        requests_payload = [
            {"type": "execute", "stmt": {"sql": "PRAGMA foreign_keys = OFF;"}}
        ]
        for sql, params in stmts:
            stmt_args = [self._serialize_arg(p) for p in (params or ())]
            requests_payload.append({
                "type": "execute",
                "stmt": {
                    "sql": sql,
                    "args": stmt_args
                }
            })
        requests_payload.append({"type": "close"})

        try:
            resp = requests.post(self.endpoint, headers=self.headers, json={"requests": requests_payload}, timeout=60)
            if resp.status_code != 200:
                raise Exception(f"Turso HTTP {resp.status_code}: {resp.text}")

            data = resp.json()
            results = data.get("results", [])
            for r in results:
                if r.get("type") == "error":
                    raise Exception(r.get("error", {}).get("message", "Batch statement error"))
            return results
        except Exception as e:
            raise e

    def execute(self, sql: str, params: Optional[tuple] = None) -> TursoCursor:
        cur = TursoCursor(self)
        cur.execute(sql, params)
        return cur

    def cursor(self) -> TursoCursor:
        return TursoCursor(self)

    def commit(self):
        # Auto-committed in pipeline requests
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_turso_connection(url: str, token: str) -> dict:
    """Tests live connection to Turso Cloud by querying database version and tables."""
    try:
        conn = TursoConnection(url, token)
        res = conn.execute("SELECT sqlite_version() AS version, CURRENT_TIMESTAMP AS server_time").fetchone()
        if not res:
            return {"success": False, "error": "No response from Turso database."}

        tables_res = conn.execute("SELECT COUNT(*) AS table_count FROM sqlite_master WHERE type='table'").fetchone()
        table_count = tables_res["table_count"] if tables_res else 0

        return {
            "success": True,
            "sqlite_version": res["version"],
            "server_time": res["server_time"],
            "existing_tables": table_count,
            "message": "Connected successfully to Turso Cloud!"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def migrate_local_sqlite_to_turso(sqlite_path: str, turso_url: str, turso_token: str) -> dict:
    """Migrates schema and all data from local SQLite planning_system.db to Turso Cloud using high-efficiency batching."""
    if not os.path.exists(sqlite_path):
        return {"success": False, "error": f"Local database not found at {sqlite_path}"}

    local_conn = sqlite3.connect(sqlite_path)
    local_conn.row_factory = sqlite3.Row
    turso = TursoConnection(turso_url, turso_token)

    try:
        cursor = local_conn.cursor()
        tables = cursor.execute("""
            SELECT name, sql FROM sqlite_master 
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY rootpage ASC
        """).fetchall()

        # Step 1: Clean drop of existing tables in reverse order
        drop_stmts = [(f'DROP TABLE IF EXISTS "{t["name"]}"', ()) for t in reversed(tables)]
        turso.execute_batch(drop_stmts)

        # Step 2: Create all tables
        create_stmts = [(t["sql"], ()) for t in tables if t["sql"]]
        turso.execute_batch(create_stmts)

        # Step 3: Copy data rows in batches of 75 rows per pipeline call
        migrated_tables = []
        total_rows_migrated = 0
        BATCH_SIZE = 75

        for t in tables:
            t_name = t["name"]
            rows = cursor.execute(f'SELECT * FROM "{t_name}"').fetchall()
            if rows:
                cols = [d[0] for d in cursor.description]
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join([f'"{c}"' for c in cols])
                insert_sql = f'INSERT INTO "{t_name}" ({col_names}) VALUES ({placeholders})'

                for i in range(0, len(rows), BATCH_SIZE):
                    batch = rows[i:i + BATCH_SIZE]
                    stmts = [(insert_sql, tuple(r)) for r in batch]
                    turso.execute_batch(stmts)
                    total_rows_migrated += len(batch)

            migrated_tables.append({"table": t_name, "rows": len(rows)})

        local_conn.close()
        return {
            "success": True,
            "migrated_tables": migrated_tables,
            "total_tables": len(migrated_tables),
            "total_rows": total_rows_migrated,
            "message": f"Successfully migrated {len(migrated_tables)} tables ({total_rows_migrated} rows) to Turso Cloud!"
        }
    except Exception as e:
        local_conn.close()
        return {
            "success": False,
            "error": f"Migration failed: {str(e)}"
        }

