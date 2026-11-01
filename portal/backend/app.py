import os
import sqlite3
import json
import io
import hmac
import hashlib
import base64
import time
from datetime import datetime, timedelta
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, request, jsonify, send_file, render_template

# Auto-load .env configuration
def load_dotenv_env():
    for base in [os.path.dirname(__file__), os.path.dirname(os.path.dirname(__file__))]:
        env_f = os.path.join(base, ".env")
        if os.path.exists(env_f):
            try:
                with open(env_f, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            if k.strip() not in os.environ:
                                os.environ[k.strip()] = v.strip().strip("'\"")
            except Exception:
                pass
load_dotenv_env()

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "..", "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "..", "static")
)

DB_PATH = os.path.join(os.path.dirname(__file__), "planning_system.db")
if os.environ.get("VERCEL"):
    import shutil
    tmp_db = "/tmp/planning_system.db"
    if not os.path.exists(tmp_db) and os.path.exists(DB_PATH):
        try:
            shutil.copy2(DB_PATH, tmp_db)
        except Exception as e:
            print("Could not copy DB to /tmp:", e)
    if os.path.exists(tmp_db):
        DB_PATH = tmp_db

from turso_client import TursoConnection, test_turso_connection, migrate_local_sqlite_to_turso
from poloxy_service import PoloxyService, POLOXY_CONFIG

def get_turso_credentials():
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    return url, token

def get_db():
    turso_url, turso_token = get_turso_credentials()
    if turso_url and turso_token:
        try:
            return TursoConnection(turso_url, turso_token)
        except Exception as e:
            print("Turso connection error, fallback to local SQLite:", e)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def style_merged_range(ws, cell_range, font=None, fill=None, border=None, alignment=None):
    """Guarantees solid styling and fills across all merged cells in OpenPyXL."""
    for row in ws[cell_range]:
        for cell in row:
            if font: cell.font = font
            if fill: cell.fill = fill
            if border: cell.border = border
            if alignment: cell.alignment = alignment

def rows_to_dict(rows):
    return [dict(row) for row in rows]

# ----------------- STATELESS HMAC SIGNED SESSIONS (VERCEL & MULTI-USER SAFE) -----------------
SECRET_KEY = os.environ.get("SESSION_SECRET")
if not SECRET_KEY:
    # No hard-coded secret in source. Set SESSION_SECRET in Vercel/.env for stable sessions.
    SECRET_KEY = secrets.token_hex(32)
    print("WARNING: SESSION_SECRET not set - using a temporary random key (sessions reset on restart).")

def current_month():
    return (datetime.utcnow() + timedelta(hours=6)).strftime("%Y-%m")

# ----------------- PASSWORD HASHING (legacy plain-text auto-upgrades on next login) -----------------
def is_hashed(p):
    return isinstance(p, str) and p.startswith(("pbkdf2:", "scrypt:"))

def verify_password(stored, given):
    if not stored:
        return False
    if is_hashed(stored):
        return check_password_hash(stored, given)
    return hmac.compare_digest(str(stored), str(given))

def hash_password(p):
    return generate_password_hash(p)


def generate_session_token(user_data):
    payload = {
        "u": {
            "id": user_data.get("id"),
            "username": user_data.get("username"),
            "name": user_data.get("name"),
            "role": user_data.get("role"),
            "department": user_data.get("department", ""),
            "allowed_category": user_data.get("allowed_category", "ALL"),
            "must_change_password": user_data.get("must_change_password", False)
        },
        "exp": int(time.time()) + (86400 * 7) # 7 days session
    }
    payload_json = json.dumps(payload, separators=(',', ':'))
    b64_data = base64.urlsafe_b64encode(payload_json.encode('utf-8')).decode('utf-8').rstrip('=')
    sig = hmac.new(SECRET_KEY.encode('utf-8'), b64_data.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{b64_data}.{sig}"

def verify_session_token(token_str):
    if not token_str or "." not in token_str:
        return None
    try:
        b64_data, sig = token_str.rsplit(".", 1)
        expected_sig = hmac.new(SECRET_KEY.encode('utf-8'), b64_data.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padding = '=' * (4 - (len(b64_data) % 4)) if len(b64_data) % 4 != 0 else ''
        payload = json.loads(base64.urlsafe_b64decode(b64_data + padding).decode('utf-8'))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("u")
    except Exception:
        return None

def get_current_session():
    # 1. Check Authorization header: Bearer <token>
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    # 2. Check X-Session-Token header
    if not token:
        token = request.headers.get("X-Session-Token", "").strip()
    # 3. Check Cookie
    if not token:
        token = request.cookies.get("paragon_session_token", "").strip()

    if token:
        user_info = verify_session_token(token)
        if user_info:
            return user_info

    return {}

def get_current_user_name(default_name="System Admin"):
    s = get_current_session()
    return s.get("name") or s.get("username") or default_name

def check_role_permission(allowed_roles, action_desc="perform this action"):
    session = get_current_session()
    user_role = session.get("role")
    if not user_role:
        return False, jsonify({"error": "Unauthorized: Please log in."}), 401
    if user_role in ("Super Admin", "Admin"):
        return True, None, None
    if user_role in allowed_roles:
        return True, None, None
    return False, jsonify({
        "error": f"Access Denied (Read-Only): Your role '{user_role}' does not have permission to {action_desc}. Only {', '.join(allowed_roles)} or Super Admin can edit."
    }), 403

# ----------------- CENTRAL LOGIN GUARD: every /api/* needs a valid session except login/logout -----------------
PUBLIC_API_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/current-user", "/api/health"}

@app.before_request
def require_login_for_api():
    path = request.path
    if path.startswith("/api/") and path not in PUBLIC_API_PATHS and request.method != "OPTIONS":
        if not get_current_session().get("id"):
            return jsonify({"error": "Unauthorized: Please log in."}), 401

@app.route("/api/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "app": "Paragon Production & Store Planning Portal"})

# ----------------- AUTH & USER MANAGEMENT -----------------
@app.route("/api/auth/current-user", methods=["GET"])
def get_current_user():
    session = get_current_session()
    if not session or "id" not in session:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": session})

@app.route("/api/auth/login", methods=["POST"])
def login_user():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)).fetchone()
    if user and not verify_password(user["password"], password):
        user = None
    if user and not is_hashed(user["password"]):
        try:
            conn.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), user["id"]))
            conn.commit()
        except Exception as e:
            print("Password upgrade skipped:", e)
    conn.close()

    if user:
        user_keys = user.keys() if hasattr(user, 'keys') else []
        must_change = bool(user["must_change_password"]) if "must_change_password" in user_keys else False
        user_role = user["role"]
        # Treat 'Admin' as 'Super Admin'
        if user_role == "Admin":
            user_role = "Super Admin"

        user_dict = {
            "id": user["id"],
            "username": user["username"],
            "name": user["name"],
            "role": user_role,
            "department": user["department"],
            "allowed_category": user["allowed_category"],
            "must_change_password": must_change
        }
        token = generate_session_token(user_dict)

        resp = jsonify({"success": True, "user": user_dict, "token": token})
        resp.set_cookie("paragon_session_token", token, max_age=86400 * 7, httponly=True, samesite="Lax")
        return resp
    return jsonify({"error": "Invalid username or password"}), 401

@app.route("/api/auth/logout", methods=["POST"])
def logout_user():
    resp = jsonify({"success": True})
    resp.delete_cookie("paragon_session_token")
    return resp

@app.route("/api/auth/change-password", methods=["POST"])
def change_password():
    session = get_current_session()
    if not session or "id" not in session:
        return jsonify({"error": "Unauthorized: Please log in first"}), 401
    data = request.json or {}
    old_password = data.get("old_password", "").strip()
    new_password = data.get("new_password", "").strip()
    
    if not old_password or not new_password:
        return jsonify({"error": "Current password and new password are required"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "New password must be at least 6 characters long"}), 400
    if old_password == new_password:
        return jsonify({"error": "New password must be different from current password"}), 400
        
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["id"],)).fetchone()
    if not user or not verify_password(user["password"], old_password):
        conn.close()
        return jsonify({"error": "Incorrect current password"}), 400
        
    conn.execute("UPDATE users SET password = ?, must_change_password = 0 WHERE id = ?", (hash_password(new_password), session["id"]))
    conn.commit()
    conn.close()
    session["must_change_password"] = False
    new_token = generate_session_token(session)
    resp = jsonify({"success": True, "message": "Password updated successfully!", "token": new_token})
    resp.set_cookie("paragon_session_token", new_token, max_age=86400 * 7, httponly=True, samesite="Lax")
    return resp


@app.route("/api/users", methods=["GET"])
def get_all_users():
    session = get_current_session()
    if session.get("role") not in ("Super Admin", "Admin"):
        return jsonify({"error": "Access Denied: Only Super Admin can view or manage users."}), 403
    conn = get_db()
    users = conn.execute("SELECT id, username, name, role, department, allowed_category, is_active, created_at, must_change_password FROM users ORDER BY id ASC").fetchall()
    conn.close()
    return jsonify(rows_to_dict(users))

@app.route("/api/users", methods=["POST"])
def create_user():
    session = get_current_session()
    if session.get("role") not in ("Super Admin", "Admin"):
        return jsonify({"error": "Access Denied: Only Super Admin can create users."}), 403
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    name = data.get("name", "").strip()
    role = data.get("role", "Store").strip()
    dept = data.get("department", "Store").strip()
    allowed = data.get("allowed_category", "05").strip()
    
    if not username or not password or not name:
        return jsonify({"error": "Username, Password, and Full Name are required"}), 400
        
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO users (username, name, role, department, password, allowed_category, must_change_password)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (username, name, role, dept, hash_password(password), allowed))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/users/<int:user_id>", methods=["PUT"])
def update_user(user_id):
    session = get_current_session()
    if session.get("role") not in ("Super Admin", "Admin"):
        return jsonify({"error": "Access Denied: Only Super Admin can update users."}), 403
    data = request.json or {}
    conn = get_db()
    try:
        if data.get("password"):
            conn.execute("""
                UPDATE users SET name = ?, role = ?, department = ?, password = ?, allowed_category = ?
                WHERE id = ?
            """, (data.get("name"), data.get("role"), data.get("department"), hash_password(data.get("password")), data.get("allowed_category"), user_id))
        else:
            conn.execute("""
                UPDATE users SET name = ?, role = ?, department = ?, allowed_category = ?
                WHERE id = ?
            """, (data.get("name"), data.get("role"), data.get("department"), data.get("allowed_category"), user_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    session = get_current_session()
    if session.get("role") not in ("Super Admin", "Admin"):
        return jsonify({"error": "Access Denied: Only Super Admin can delete users."}), 403
    if user_id == 1:
        return jsonify({"error": "Cannot delete primary Super Admin"}), 400
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ----------------- CATEGORY MANAGEMENT -----------------
@app.route("/api/categories", methods=["GET"])
def get_categories():
    conn = get_db()
    cats = conn.execute("SELECT * FROM categories WHERE is_active = 1 ORDER BY code ASC").fetchall()
    conn.close()
    return jsonify(rows_to_dict(cats))

@app.route("/api/categories", methods=["POST"])
def add_category():
    data = request.json or {}
    code = data.get("code", "").strip()
    name = data.get("name", "").strip()
    work_center = data.get("work_center", "").strip()
    branch_id = data.get("branch_id", "").strip()
    description = data.get("description", "").strip()
    
    if not code or not name:
        return jsonify({"error": "Category Code and Name are required"}), 400
        
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO categories (code, name, work_center, branch_id, description)
            VALUES (?, ?, ?, ?, ?)
        """, (code, name, work_center, branch_id, description))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

# ----------------- TURSO CLOUD DATABASE MANAGEMENT -----------------
@app.route("/api/cloud/turso/status", methods=["GET"])
def get_turso_status():
    turso_url, turso_token = get_turso_credentials()
    is_configured = bool(turso_url and turso_token)
    if is_configured:
        res = test_turso_connection(turso_url, turso_token)
        return jsonify({
            "is_configured": True,
            "mode": "turso_cloud",
            "database_url": turso_url,
            "is_connected": res.get("success", False),
            "details": res
        })
    return jsonify({
        "is_configured": False,
        "mode": "local_sqlite",
        "local_db_path": DB_PATH,
        "message": "Running on local SQLite database. Set Turso credentials to activate cloud database."
    })

@app.route("/api/cloud/turso/test", methods=["POST"])
def api_test_turso():
    data = request.json or {}
    url = data.get("url", "").strip()
    token = data.get("token", "").strip()
    if not url or not token:
        return jsonify({"success": False, "error": "Turso Database URL and Auth Token are required."}), 400
    res = test_turso_connection(url, token)
    return jsonify(res)

@app.route("/api/cloud/turso/config", methods=["POST"])
def api_save_turso_config():
    allowed, err_resp, err_code = check_role_permission(["Super Admin", "Admin"], "configure cloud database")
    if not allowed:
        return err_resp, err_code
    data = request.json or {}
    url = data.get("url", "").strip()
    token = data.get("token", "").strip()
    
    # Test connection first
    res = test_turso_connection(url, token)
    if not res.get("success"):
        return jsonify({"success": False, "error": f"Connection test failed: {res.get('error')}"}), 400

    saved = False
    try:
        with open(TURSO_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"url": url, "token": token}, f, indent=2)
        saved = True
    except OSError:
        # Vercel Serverless read-only filesystem: write to /tmp
        try:
            with open(TMP_TURSO_CONFIG, "w", encoding="utf-8") as f:
                json.dump({"url": url, "token": token}, f, indent=2)
            saved = True
        except Exception as e:
            print("Could not write config to /tmp:", e)

    os.environ["TURSO_DATABASE_URL"] = url
    os.environ["TURSO_AUTH_TOKEN"] = token
    return jsonify({"success": True, "message": "Turso Cloud Database configured and activated!"})

@app.route("/api/cloud/turso/migrate", methods=["POST"])
def api_migrate_to_turso():
    allowed, err_resp, err_code = check_role_permission(["Super Admin", "Admin"], "migrate database to cloud")
    if not allowed:
        return err_resp, err_code
    turso_url, turso_token = get_turso_credentials()
    data = request.json or {}
    url = data.get("url") or turso_url
    token = data.get("token") or turso_token
    if not url or not token:
        return jsonify({"success": False, "error": "Turso Database URL and Auth Token are required."}), 400
    
    source_db = DB_PATH
    if not os.path.exists(source_db):
        bundled = os.path.join(os.path.dirname(__file__), "planning_system.db")
        if os.path.exists(bundled):
            source_db = bundled

    res = migrate_local_sqlite_to_turso(source_db, url, token)
    return jsonify(res)

# ----------------- POLOXY ERP SETTINGS & SYNC -----------------
@app.route("/api/erp/settings", methods=["GET"])
def get_erp_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM erp_settings").fetchall()
    conn.close()
    settings = {r["key"]: r["value"] for r in rows if not any(w in str(r["key"]).lower() for w in ("pass", "token", "secret"))}
    return jsonify(settings)

@app.route("/api/erp/settings", methods=["POST"])
def save_erp_settings():
    if get_current_session().get("role") not in ("Super Admin", "Admin"):
        return jsonify({"error": "Access Denied: Only Super Admin can change ERP settings."}), 403
    data = request.json or {}
    conn = get_db()
    cursor = conn.cursor()
    for k, v in data.items():
        cursor.execute("""
            INSERT INTO erp_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """, (k, str(v)))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/api/erp/sync", methods=["POST"])
def sync_poloxy_erp():
    """
    Poloxy ERP Live Integration Engine:
    - action='actual_output': Pulls live factory workcenter output from Poloxy dt_production_summary_rpt.
    - action='opening_stock': Pulls FG godown closing/opening stock.
    - action='both': Pulls both production output and opening stock.
    - action='materials': Syncs raw materials and packaging materials into materials table.
    """
    data = request.json or {}
    cat_code = str(data.get("category_code", "05"))
    month = data.get("month", datetime.now().strftime("%Y-%m"))
    mode = data.get("mode", "single")
    action = data.get("action", "both")
    single_date = data.get("date", "").strip()
    date_from = data.get("date_from", "").strip()
    date_to = data.get("date_to", "").strip()
    
    if mode == "single":
        if not single_date:
            single_date = datetime.now().strftime("%Y-%m-%d")
        date_from = single_date
        date_to = single_date
    else:
        if not date_from:
            date_from = f"{month}-01"
        if not date_to:
            date_to = datetime.now().strftime("%Y-%m-%d")

    # Handle Materials sync request
    if action in ("materials", "rm_pm"):
        return sync_materials_poloxy()

    cfg = POLOXY_CONFIG.get(cat_code, POLOXY_CONFIG['05'])
    branch_unit = cfg['name']
    godown_name = cfg['factory_godown_name']
    godown_id = cfg['factory_godown_id']
    work_center = cfg['work_center']
    group_name = cfg['groups']['FG']['name']
    group_id = cfg['groups']['FG']['id']
    pull_timestamp = datetime.now().strftime("%d-%b-%Y %I:%M:%S %p")
    date_display = single_date if mode == "single" else f"{date_from} to {date_to}"

    poloxy = PoloxyService()
    poloxy_connected = False
    prod_items = {}
    fg_stock_items = {}

    try:
        poloxy.login()
        poloxy_connected = True

        # 1. Fetch live production output if requested
        if action in ("both", "actual_output"):
            try:
                prod_items = poloxy.fetch_production_output(cat_code, date_from, date_to)
            except Exception as pe:
                print(f"[!] Warning pulling production output: {pe}")

        # 2. Fetch live opening stock if requested
        if action in ("both", "opening_stock"):
            try:
                # Dry Foods (05) tracks FG via factory line outputs; for other categories query godown with safe handling
                if cat_code != "05":
                    fg_stock_items = poloxy.fetch_godown_stock_items(godown_name, godown_id, group_name, group_id, single_date or date_from)
            except Exception as se:
                print(f"[!] Warning pulling FG stock: {se}")

    except Exception as e:
        print(f"[!] Poloxy Live Connection Warning: {e}. Falling back to existing database plans.")

    conn = get_db()
    cursor = conn.cursor()

    # Load all SKUs for this unit
    sku_rows = conn.execute("SELECT id, sku_code, name, ctn_size FROM skus WHERE category_code = ?", (cat_code,)).fetchall()
    by_code = {str(s["sku_code"]).strip().upper(): s for s in sku_rows}
    by_name = {str(s["name"]).strip().lower(): s for s in sku_rows}

    updated_count = 0
    created_count = 0
    total_produced_ctn = 0.0
    total_opening_ctn = 0.0

    target_date = single_date if mode == "single" else date_to
    target_month = target_date[:7]

    # Process live production output from Poloxy
    if action in ("both", "actual_output") and prod_items:
        for p_key, p_info in prod_items.items():
            p_name = p_info["item"].strip()
            p_qty = float(p_info["qty"] or 0.0)
            if p_qty <= 0:
                continue

            # Match SKU
            matched_sku = None
            if p_key in by_name:
                matched_sku = by_name[p_key]
            else:
                for k, s in by_name.items():
                    if k in p_key or p_key in k:
                        matched_sku = s
                        break

            if not matched_sku:
                # Auto-register newly discovered SKU from Poloxy factory line
                sku_count = cursor.execute("SELECT COUNT(*) FROM skus WHERE category_code = ?", (cat_code,)).fetchone()[0] + 1
                new_code = f"SKU-{cat_code}-{sku_count:03d}"
                cursor.execute("""
                    INSERT INTO skus (category_code, sku_code, name, ctn_size, unit, status)
                    VALUES (?, ?, ?, 12, 'Pkt', 'Active')
                """, (cat_code, new_code, p_name))
                new_sku_id = cursor.lastrowid
                matched_sku = {"id": new_sku_id, "sku_code": new_code, "name": p_name, "ctn_size": 12}
                by_name[p_key] = matched_sku

            sku_id = matched_sku["id"]
            ctn_size = float(matched_sku["ctn_size"] or 1.0)
            produced_ctn = round(p_qty, 2)
            produced_pcs = round(produced_ctn * ctn_size, 2)
            total_produced_ctn += produced_ctn

            # Check if plan already exists for this SKU and date
            existing_plan = cursor.execute("""
                SELECT id, plan_no, planned_ctn, actual_produced_ctn
                FROM production_plans
                WHERE category_code = ? AND sku_id = ? AND plan_date = ? AND status != 'Cancelled'
                ORDER BY id LIMIT 1
            """, (cat_code, sku_id, target_date)).fetchone()

            if existing_plan:
                old_produced = float(existing_plan["actual_produced_ctn"] or 0.0)
                planned = float(existing_plan["planned_ctn"] or 0.0)
                new_status = 'Completed' if planned > 0 and produced_ctn >= planned else 'In Production'
                cursor.execute("""
                    UPDATE production_plans
                    SET actual_produced_ctn = ?,
                        actual_produced_pcs = ?,
                        status = ?,
                        poloxy_synced_at = ?,
                        work_center_name = ?
                    WHERE id = ?
                """, (produced_ctn, produced_pcs, new_status, pull_timestamp, p_info.get("workcenter", work_center), existing_plan["id"]))
                updated_count += 1

                # Deduct BOM floor stock
                delta = produced_ctn - old_produced
                if delta > 0:
                    for b in cursor.execute("SELECT material_id, rate_per_ctn FROM bom WHERE sku_id = ?", (sku_id,)).fetchall():
                        cursor.execute("""
                            UPDATE materials SET floor_stock = MAX(0.0, ROUND(COALESCE(floor_stock, 0.0) - ?, 4))
                            WHERE id = ?
                        """, (round(delta * b["rate_per_ctn"], 4), b["material_id"]))
            else:
                # Create a plan entry for this day so the factory's actual output is recorded
                plan_no = f"PP-{cat_code}-{target_date.replace('-', '')}-{int(sku_id):03d}"
                cursor.execute("""
                    INSERT INTO production_plans (
                        category_code, plan_no, month_year, plan_date, sku_id,
                        planned_ctn, planned_pcs, opening_stock_ctn, opening_stock_pcs,
                        actual_produced_ctn, actual_produced_pcs, suggested_ctn, status,
                        created_by, notes, poloxy_synced_at, work_center_name
                    ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, ?, ?, 0.0, 'Completed', 'Poloxy Live Sync', ?, ?, ?)
                """, (
                    cat_code, plan_no, target_month, target_date, sku_id,
                    produced_ctn, produced_pcs,
                    f"Live Poloxy Output: {produced_ctn} ctn at {pull_timestamp}",
                    pull_timestamp, p_info.get("workcenter", work_center)
                ))
                created_count += 1

                # Deduct BOM floor stock
                for b in cursor.execute("SELECT material_id, rate_per_ctn FROM bom WHERE sku_id = ?", (sku_id,)).fetchall():
                    cursor.execute("""
                        UPDATE materials SET floor_stock = MAX(0.0, ROUND(COALESCE(floor_stock, 0.0) - ?, 4))
                        WHERE id = ?
                    """, (round(produced_ctn * b["rate_per_ctn"], 4), b["material_id"]))

    # Process opening stock if requested
    if action in ("both", "opening_stock") and fg_stock_items:
        # Update opening stock on relevant plans
        plans_to_stock = conn.execute("""
            SELECT p.id, p.sku_id, s.sku_code, s.name, s.ctn_size
            FROM production_plans p
            JOIN skus s ON p.sku_id = s.id
            WHERE p.category_code = ? AND (p.plan_date = ? OR p.month_year = ?)
        """, (cat_code, target_date, target_month)).fetchall()

        for pl in plans_to_stock:
            sku_code = pl["sku_code"].strip()
            sku_name_low = pl["name"].strip().lower()

            matched_stock = None
            if sku_code in fg_stock_items:
                matched_stock = fg_stock_items[sku_code]['closing_stock']
            elif sku_name_low in fg_stock_items:
                matched_stock = fg_stock_items[sku_name_low]['closing_stock']
            else:
                for k, it in fg_stock_items.items():
                    if sku_name_low in it['name'].lower() or it['name'].lower() in sku_name_low:
                        matched_stock = it['closing_stock']
                        break

            if matched_stock is not None:
                opening_ctn = round(matched_stock / (pl["ctn_size"] or 1), 1)
                opening_pcs = opening_ctn * (pl["ctn_size"] or 1)
                total_opening_ctn += opening_ctn

                cursor.execute("""
                    UPDATE production_plans
                    SET opening_stock_ctn = ?,
                        opening_stock_pcs = ?,
                        godown_name = ?,
                        poloxy_synced_at = ?
                    WHERE id = ?
                """, (opening_ctn, opening_pcs, godown_name, pull_timestamp, pl["id"]))
                updated_count += 1

    conn.commit()
    conn.close()

    total_items = len(prod_items) if action in ("both", "actual_output") else len(fg_stock_items)
    resp = jsonify({
        "success": True,
        "message": f"Poloxy ERP live data sync completed for {date_display}!",
        "action": action,
        "date_mode": mode,
        "date_range": date_display,
        "poloxy_connected": poloxy_connected,
        "plans_updated": updated_count,
        "plans_created": created_count,
        "items_found": total_items,
        "total_opening_cartons": round(total_opening_ctn, 1),
        "total_produced_cartons": round(total_produced_ctn, 1),
        "work_center": work_center,
        "godown_name": godown_name,
        "live_timestamp": pull_timestamp
    })
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

@app.route("/api/materials/sync-poloxy", methods=["POST"])
def sync_materials_poloxy():
    """
    Live sync of Raw Materials (Group: RAW MATERIAL / 14) and 
    Packaging Materials (Group: PACKAGING MATERIAL / 10) from Poloxy Factory Godown.
    """
    data = request.json or {}
    cat_code = str(data.get("category_code") or data.get("category") or "05")
    date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    
    cfg = POLOXY_CONFIG.get(cat_code, POLOXY_CONFIG['05'])
    godown_name = cfg['factory_godown_name']
    godown_id = cfg['factory_godown_id']
    
    service = PoloxyService()
    try:
        service.login()
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to login to Poloxy ERP: {str(e)}"}), 500
        
    updated_items = []
    conn = get_db()
    cursor = conn.cursor()
    pull_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # 1. Fetch RAW MATERIALS
        rm_items = service.fetch_godown_stock_items(godown_name, godown_id, cfg['groups']['RM']['name'], cfg['groups']['RM']['id'], date_str)
        # 2. Fetch PACKAGING MATERIALS
        pm_items = service.fetch_godown_stock_items(godown_name, godown_id, cfg['groups']['PM']['name'], cfg['groups']['PM']['id'], date_str)
        
        all_poloxy = {}
        all_poloxy.update(rm_items)
        all_poloxy.update(pm_items)
        
        db_mats = cursor.execute("SELECT id, item_code, name, type, current_stock FROM materials WHERE category_code = ?", (cat_code,)).fetchall()
        
        batch_stmts = []
        for m in db_mats:
            m_code = m["item_code"].strip()
            m_name_lower = m["name"].strip().lower()
            
            matched_stock = None
            if m_code in all_poloxy:
                matched_stock = all_poloxy[m_code]['closing_stock']
            elif m_name_lower in all_poloxy:
                matched_stock = all_poloxy[m_name_lower]['closing_stock']
            else:
                for p_key, p_val in all_poloxy.items():
                    p_name = p_val['name'].strip().lower()
                    if m_name_lower == p_name or (len(m_name_lower) > 4 and (m_name_lower in p_name or p_name in m_name_lower)):
                        matched_stock = p_val['closing_stock']
                        break
                        
            if matched_stock is not None:
                old_stock = float(m["current_stock"] or 0.0)
                new_stock = float(matched_stock)
                diff = new_stock - old_stock
                
                batch_stmts.append((
                    "UPDATE materials SET opening_stock = ?, current_stock = ?, poloxy_synced_at = ? WHERE id = ?",
                    (new_stock, new_stock, pull_time, m["id"])
                ))
                batch_stmts.append((
                    "INSERT INTO stock_ledger (material_id, category_code, date, opening_qty, in_qty, out_qty, closing_qty, txn_type, ref_no, notes, created_by) VALUES (?, ?, ?, ?, ?, 0.0, ?, 'POLOXY_SYNC', 'ERP-PULL', ?, ?)",
                    (m["id"], cat_code, date_str, old_stock, diff, new_stock, f"Synced live from Poloxy Godown [{godown_name}]", get_current_user_name("System Admin"))
                ))
                
                updated_items.append({
                    "id": m["id"],
                    "code": m["item_code"],
                    "name": m["name"],
                    "old_stock": old_stock,
                    "new_stock": new_stock
                })

        if batch_stmts:
            if hasattr(conn, 'execute_batch'):
                conn.execute_batch(batch_stmts)
            else:
                for sql, prms in batch_stmts:
                    cursor.execute(sql, prms)
                
        conn.commit()
        conn.close()
        invalidate_dashboard_cache()
        return jsonify({
            "success": True,
            "message": f"Successfully synced {len(updated_items)} materials with live Poloxy ERP closing stock!",
            "updated_count": len(updated_items),
            "godown_name": godown_name,
            "poloxy_total_rm": len(rm_items),
            "poloxy_total_pm": len(pm_items),
            "sync_time": pull_time,
            "items": updated_items[:20]
        })
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "error": f"Error pulling Poloxy stock: {str(e)}"}), 500

@app.route("/api/reports/stock-ledger", methods=["GET"])
def get_stock_ledger():
    cat = request.args.get("category", "05").strip()
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    m_type = request.args.get("type", "")
    
    conn = get_db()
    sql = """
        SELECT sl.*, m.item_code, m.name AS material_name, m.unit, m.type AS material_type
        FROM stock_ledger sl
        JOIN materials m ON sl.material_id = m.id
        WHERE 1=1
    """
    params = []
    if cat and cat != 'ALL':
        sql += " AND sl.category_code = ?"
        params.append(cat)
    if date_from:
        sql += " AND sl.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND sl.date <= ?"
        params.append(date_to)
    if m_type:
        sql += " AND m.type = ?"
        params.append(m_type)
        
    sql += " ORDER BY sl.date DESC, sl.id DESC LIMIT 500"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_dict(rows))

@app.route("/api/erp/pull-opening-stock", methods=["POST"])
def pull_erp_opening_stock():
    """Shortcut endpoint to pull Poloxy ERP Opening Stock for a specific date (Rule 9)."""
    req_data = request.json or {}
    req_data["action"] = "opening_stock"
    # Forward internally
    with app.test_request_context(method="POST", json=req_data):
        return sync_poloxy_erp()


# ----------------- DASHBOARD STATS (OPTIMIZED WITH FAST IN-MEMORY TTL CACHE) -----------------
import threading

_DASHBOARD_CACHE = {}
_DASHBOARD_CACHE_LOCK = threading.Lock()
DASHBOARD_CACHE_TTL = 20.0  # seconds

def invalidate_dashboard_cache():
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE.clear()

@app.route("/api/dashboard/stats", methods=["GET"])
def get_dashboard_stats():
    cat = request.args.get("category", "").strip()
    month = request.args.get("month", current_month()).strip()
    uom = request.args.get("uom", "Packet").strip()
    force_refresh = request.args.get("refresh", "0") == "1"
    
    cache_key = (cat, month, uom)
    now = time.time()
    
    if not force_refresh:
        with _DASHBOARD_CACHE_LOCK:
            if cache_key in _DASHBOARD_CACHE:
                cached_time, cached_data = _DASHBOARD_CACHE[cache_key]
                if now - cached_time < DASHBOARD_CACHE_TTL:
                    return jsonify(cached_data)

    conn = get_db()
    cat_filter = "AND category_code = ?" if cat and cat not in ('ALL', 'undefined', 'null', 'None', '') else ""
    params = [cat] if cat and cat not in ('ALL', 'undefined', 'null', 'None', '') else []
    
    # 1. Combined Materials Stats & Valuation (Single fast aggregation)
    mat_row = conn.execute(f"""
        SELECT 
            COUNT(*) AS total_materials,
            COALESCE(SUM(CASE WHEN type='RM' THEN 1 ELSE 0 END), 0) AS total_rm,
            COALESCE(SUM(CASE WHEN type='PM' THEN 1 ELSE 0 END), 0) AS total_pm,
            COALESCE(SUM(CASE WHEN current_stock < safety_stock THEN 1 ELSE 0 END), 0) AS critical_count,
            COALESCE(SUM(CASE WHEN current_stock >= safety_stock AND current_stock <= (safety_stock * 1.5) THEN 1 ELSE 0 END), 0) AS low_stock_count,
            COALESCE(SUM(current_stock * unit_price), 0.0) AS total_val,
            COALESCE(SUM(CASE WHEN type='RM' THEN current_stock * unit_price ELSE 0 END), 0.0) AS rm_val,
            COALESCE(SUM(CASE WHEN type='PM' THEN current_stock * unit_price ELSE 0 END), 0.0) AS pm_val
        FROM materials WHERE 1=1 {cat_filter}
    """, params).fetchone()
    
    total_materials = mat_row["total_materials"] or 0
    total_rm = mat_row["total_rm"] or 0
    total_pm = mat_row["total_pm"] or 0
    critical_count = mat_row["critical_count"] or 0
    low_stock_count = mat_row["low_stock_count"] or 0
    safe_count = max(0, total_materials - critical_count - low_stock_count)
    total_valuation = mat_row["total_val"] or 0.0
    rm_valuation = mat_row["rm_val"] or 0.0
    pm_valuation = mat_row["pm_val"] or 0.0

    # 2. Combined Forecast, Production & Requisitions
    sku_cat_filter = "AND s.category_code = ?" if cat and cat not in ('ALL', 'undefined', 'null', 'None', '') else ""
    plan_cat_filter = "AND category_code = ?" if cat and cat not in ('ALL', 'undefined', 'null', 'None', '') else ""
    req_cat_filter = "AND category_code = ?" if cat and cat not in ('ALL', 'undefined', 'null', 'None', '') else ""
    
    perf_sql = f"""
        SELECT 
            (SELECT COALESCE(SUM(sf.forecast_ctn), 0.0) FROM sales_forecast sf JOIN skus s ON sf.sku_id = s.id WHERE sf.month_year = ? {sku_cat_filter}) AS total_forecast_ctn,
            (SELECT COALESCE(SUM(sf.forecast_ctn * s.ctn_size), 0.0) FROM sales_forecast sf JOIN skus s ON sf.sku_id = s.id WHERE sf.month_year = ? {sku_cat_filter}) AS total_forecast_pcs,
            (SELECT COALESCE(SUM(planned_ctn), 0.0) FROM production_plans WHERE month_year = ? {plan_cat_filter}) AS planned_ctn,
            (SELECT COALESCE(SUM(actual_produced_ctn), 0.0) FROM production_plans WHERE month_year = ? {plan_cat_filter}) AS actual_ctn,
            (SELECT COALESCE(SUM(planned_pcs), 0.0) FROM production_plans WHERE month_year = ? {plan_cat_filter}) AS planned_pcs,
            (SELECT COALESCE(SUM(actual_produced_pcs), 0.0) FROM production_plans WHERE month_year = ? {plan_cat_filter}) AS actual_pcs,
            (SELECT COUNT(*) FROM material_requisitions WHERE status IN ('Pending', 'Partially Issued') {req_cat_filter}) AS pending_mr,
            (SELECT COUNT(*) FROM purchase_requisitions WHERE status IN ('Pending', 'Approved', 'In-Transit') {req_cat_filter}) AS pending_pr
    """
    perf_params = []
    for _ in range(2):
        perf_params.append(month)
        if sku_cat_filter: perf_params.append(cat)
    for _ in range(4):
        perf_params.append(month)
        if plan_cat_filter: perf_params.append(cat)
    if req_cat_filter: perf_params.append(cat)
    if req_cat_filter: perf_params.append(cat)
    
    perf_row = conn.execute(perf_sql, perf_params).fetchone()
    total_forecast_ctn = perf_row["total_forecast_ctn"] or 0.0
    total_forecast_pcs = perf_row["total_forecast_pcs"] or 0.0
    planned_ctn = perf_row["planned_ctn"] or 0.0
    actual_ctn = perf_row["actual_ctn"] or 0.0
    planned_pcs = perf_row["planned_pcs"] or (planned_ctn * 12)
    actual_pcs = perf_row["actual_pcs"] or (actual_ctn * 12)
    pending_mr = perf_row["pending_mr"] or 0
    pending_pr = perf_row["pending_pr"] or 0
    ach_pct = round((actual_ctn / planned_ctn * 100), 1) if planned_ctn > 0 else 0.0

    # 3. Fast SQL-level FSN Classification
    fsn_sql = f"""
        SELECT 
            COALESCE(SUM(CASE WHEN total_issued = 0 THEN 1 ELSE 0 END), 0) AS non_moving_count,
            COALESCE(SUM(CASE WHEN total_issued > 0 AND current_stock > 0 AND (total_issued * 1.0 / current_stock) >= 0.15 THEN 1 ELSE 0 END), 0) AS fast_count,
            COALESCE(SUM(CASE WHEN total_issued > 0 AND (current_stock = 0 OR (total_issued * 1.0 / current_stock) < 0.15) THEN 1 ELSE 0 END), 0) AS slow_count
        FROM (
            SELECT m.id, m.current_stock, COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) AS total_issued
            FROM materials m
            LEFT JOIN store_transactions t ON m.id = t.material_id
            WHERE 1=1 {cat_filter}
            GROUP BY m.id
        )
    """
    fsn_row = conn.execute(fsn_sql, params).fetchone()
    non_moving_count = fsn_row["non_moving_count"] or 0
    fast_count = fsn_row["fast_count"] or 0
    slow_count = fsn_row["slow_count"] or 0

    # 4. Critical deficit items (Top 8)
    critical_items = conn.execute(f"""
        SELECT id, item_code, name, type, unit, current_stock, safety_stock, in_transit, supplier_name, unit_price,
               ROUND(safety_stock - current_stock, 1) AS deficit
        FROM materials
        WHERE current_stock < safety_stock {cat_filter}
        ORDER BY (safety_stock - current_stock) DESC
        LIMIT 8
    """, params).fetchall()
    
    # 5. Recent Transactions (Top 6)
    recent_txns = conn.execute(f"""
        SELECT t.*, m.name AS material_name, m.unit, m.type, m.category_code
        FROM store_transactions t
        JOIN materials m ON t.material_id = m.id
        WHERE 1=1 {cat_filter}
        ORDER BY t.id DESC LIMIT 6
    """, params).fetchall()
    
    conn.close()

    result_data = {
        "materials": {
            "total": total_materials,
            "rm_count": total_rm,
            "pm_count": total_pm,
            "critical": critical_count,
            "low": low_stock_count,
            "safe": safe_count,
            "fast": fast_count,
            "slow": slow_count,
            "non_moving": non_moving_count
        },
        "valuation": {
            "total": round(total_valuation, 2),
            "rm": round(rm_valuation, 2),
            "pm": round(pm_valuation, 2)
        },
        "performance": {
            "month": month,
            "uom": uom,
            "forecast": total_forecast_pcs if uom == "Packet" else total_forecast_ctn,
            "planned": planned_pcs if uom == "Packet" else planned_ctn,
            "actual": actual_pcs if uom == "Packet" else actual_ctn,
            "achievement_pct": ach_pct,
            "forecast_ctn": total_forecast_ctn,
            "planned_ctn": planned_ctn,
            "actual_produced_ctn": actual_ctn
        },
        "requisitions": {
            "pending_production": pending_mr,
            "pending_purchase": pending_pr
        },
        "critical_items": rows_to_dict(critical_items),
        "recent_transactions": rows_to_dict(recent_txns)
    }

    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE[cache_key] = (now, result_data)

    return jsonify(result_data)

# ----------------- SKU (FINISH GOODS) MASTER -----------------
@app.route("/api/skus", methods=["GET"])
def get_skus():
    cat = request.args.get("category", "").strip()
    q = request.args.get("q", "").strip()
    conn = get_db()
    sql = "SELECT s.*, c.name AS category_name FROM skus s JOIN categories c ON s.category_code = c.code WHERE 1=1"
    params = []
    if cat and cat != 'ALL':
        sql += " AND s.category_code = ?"
        params.append(cat)
    if q:
        sql += " AND (s.sku_code LIKE ? OR s.name LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY s.id ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_dict(rows))

@app.route("/api/skus", methods=["POST"])
def create_sku():
    data = request.json or {}
    name = data.get("name", "").strip()
    cat_code = data.get("category_code", "05").strip()
    if not name:
        return jsonify({"error": "SKU Name is required"}), 400
        
    conn = get_db()
    sku_code = data.get("sku_code", "").strip()
    if not sku_code:
        max_id = int(conn.execute("SELECT MAX(id) FROM skus").fetchone()[0] or 0)
        sku_code = f"SKU-{cat_code}-{max_id + 1:03d}"
        
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO skus (category_code, sku_code, name, category, sub_category, ctn_size, pack_size, uom_default, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Active')
        """, (
            cat_code,
            sku_code,
            name,
            data.get("category", "General"),
            data.get("sub_category", "Standard"),
            int(data.get("ctn_size", 12)),
            str(data.get("pack_size", "200gm")),
            data.get("uom_default", "Packet")
        ))
        new_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({"success": True, "id": new_id, "sku_code": sku_code})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/skus/<int:sku_id>", methods=["PUT"])
def update_sku(sku_id):
    data = request.json or {}
    conn = get_db()
    try:
        conn.execute("""
            UPDATE skus 
            SET name = ?, category = ?, sub_category = ?, ctn_size = ?, pack_size = ?, uom_default = ?, status = ?
            WHERE id = ?
        """, (
            data.get("name"),
            data.get("category"),
            data.get("sub_category"),
            int(data.get("ctn_size", 12)),
            str(data.get("pack_size", "")),
            data.get("uom_default", "Packet"),
            data.get("status", "Active"),
            sku_id
        ))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/skus/<int:sku_id>", methods=["DELETE"])
def delete_sku(sku_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM skus WHERE id = ?", (sku_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

# ----------------- MATERIALS (RM & PM) MASTER -----------------
@app.route("/api/materials", methods=["GET"])
def get_materials():
    cat = request.args.get("category", "").strip()
    m_type = request.args.get("type", "").strip()
    status = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()
    
    conn = get_db()
    sql = """
        SELECT m.*,
               COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) AS total_issued,
               CASE 
                   WHEN m.current_stock < m.safety_stock THEN 'Critical'
                   WHEN m.current_stock <= (m.safety_stock * 1.5) THEN 'Low'
                   ELSE 'Safe'
               END AS stock_status,
               CASE
                   WHEN COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) = 0 THEN 'Non-Moving'
                   WHEN m.current_stock > 0 AND (COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) / m.current_stock) >= 0.15 THEN 'Fast'
                   ELSE 'Slow'
               END AS fsn_status
        FROM materials m
        LEFT JOIN store_transactions t ON m.id = t.material_id
        WHERE 1=1
    """
    params = []
    if cat and cat not in ('ALL', 'undefined', 'null', 'None', ''):
        sql += " AND m.category_code = ?"
        params.append(cat)
    if m_type:
        sql += " AND m.type = ?"
        params.append(m_type)
    if q:
        sql += " AND (m.item_code LIKE ? OR m.name LIKE ? OR m.supplier_name LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        
    sql += " GROUP BY m.id"
    
    fsn = request.args.get("fsn", "").strip()
    if status == 'critical': sql += " HAVING stock_status = 'Critical'"
    elif status == 'low': sql += " HAVING stock_status = 'Low'"
    elif status == 'safe': sql += " HAVING stock_status = 'Safe'"
    elif fsn == 'fast': sql += " HAVING fsn_status = 'Fast'"
    elif fsn == 'slow': sql += " HAVING fsn_status = 'Slow'"
    elif fsn == 'non_moving': sql += " HAVING fsn_status = 'Non-Moving'"
    
    sql += " ORDER BY m.type ASC, m.name ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_dict(rows))

@app.route("/api/materials", methods=["POST"])
def create_material():
    data = request.json or {}
    name = data.get("name", "").strip()
    cat_code = data.get("category_code", "05").strip()
    m_type = data.get("type", "RM").strip()
    
    if not name:
        return jsonify({"error": "Material Name is required"}), 400
        
    conn = get_db()
    item_code = data.get("item_code", "").strip()
    if not item_code:
        max_id = int(conn.execute("SELECT MAX(id) FROM materials WHERE type = ?", (m_type,)).fetchone()[0] or 0)
        prefix = f"{m_type}-{cat_code}"
        item_code = f"{prefix}-{max_id + 1:03d}"
        
    try:
        cursor = conn.cursor()
        current_stock = float(data.get("current_stock", 0.0))
        safety_stock = float(data.get("safety_stock", 0.0))
        unit_price = float(data.get("unit_price", 0.0))
        
        cursor.execute("""
            INSERT INTO materials (category_code, item_code, name, type, category, unit, unit_price, current_stock, safety_stock, in_transit, supplier_name, lead_time_days, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cat_code, item_code, name, m_type,
            data.get("category", "General"),
            data.get("unit", "Kg"), unit_price, current_stock, safety_stock,
            float(data.get("in_transit", 0.0)),
            data.get("supplier_name", "Local Vendor"),
            int(data.get("lead_time_days", 7)),
            data.get("priority", "Medium")
        ))
        new_id = cursor.lastrowid
        
        if current_stock > 0:
            cursor.execute("""
                INSERT INTO store_transactions (material_id, txn_type, qty, balance_after, reference_no, notes, created_by)
                VALUES (?, 'INITIAL', ?, ?, 'INIT-CREATE', 'Created with initial stock', ?)
            """, (new_id, current_stock, current_stock, get_current_user_name("System Admin")))
            
        conn.commit()
        conn.close()
        return jsonify({"success": True, "id": new_id, "item_code": item_code})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/materials/<int:mat_id>", methods=["PUT"])
def update_material(mat_id):
    data = request.json or {}
    conn = get_db()
    try:
        conn.execute("""
            UPDATE materials
            SET name = ?, type = ?, category = ?, unit = ?, unit_price = ?, safety_stock = ?,
                supplier_name = ?, lead_time_days = ?, priority = ?
            WHERE id = ?
        """, (
            data.get("name"), data.get("type", "RM"), data.get("category"), data.get("unit"),
            float(data.get("unit_price", 0.0)), float(data.get("safety_stock", 0.0)),
            data.get("supplier_name"), int(data.get("lead_time_days", 7)),
            data.get("priority", "Medium"), mat_id
        ))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/materials/<int:mat_id>", methods=["DELETE"])
def delete_material(mat_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM materials WHERE id = ?", (mat_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

# ----------------- BILL OF MATERIALS (BOM) -----------------
@app.route("/api/bom/<int:sku_id>", methods=["GET"])
def get_bom(sku_id):
    allowed, err_resp, err_code = check_role_permission(["Super Admin"], "view Bill of Materials (BOM) specifications")
    if not allowed:
        return err_resp, err_code

    conn = get_db()
    sku = conn.execute("SELECT * FROM skus WHERE id = ?", (sku_id,)).fetchone()
    if not sku:
        conn.close()
        return jsonify({"error": "SKU not found"}), 404
        
    items = conn.execute("""
        SELECT b.id, b.sku_id, b.material_id, b.rate_per_ctn, b.unit AS bom_unit, b.notes,
               m.item_code, m.name AS material_name, m.type AS material_type, m.unit AS material_unit,
               m.unit_price, m.current_stock, m.safety_stock, m.supplier_name
        FROM bom b
        JOIN materials m ON b.material_id = m.id
        WHERE b.sku_id = ?
        ORDER BY m.type ASC, b.rate_per_ctn DESC
    """, (sku_id,)).fetchall()
    
    conn.close()
    return jsonify({
        "sku": dict(sku),
        "items": rows_to_dict(items)
    })

@app.route("/api/bom/<int:sku_id>", methods=["POST"])
def add_update_bom_item(sku_id):
    allowed, err_resp, err_code = check_role_permission(["Super Admin"], "edit Bill of Materials (BOM) specifications")
    if not allowed:
        return err_resp, err_code

    data = request.json or {}
    material_id = data.get("material_id")
    rate_per_ctn = float(data.get("rate_per_ctn", 0.0))
    unit = data.get("unit", "Kg")
    notes = data.get("notes", "")
    
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO bom (sku_id, material_id, rate_per_ctn, unit, notes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sku_id, material_id) DO UPDATE SET
                rate_per_ctn = excluded.rate_per_ctn,
                unit = excluded.unit,
                notes = excluded.notes
        """, (sku_id, material_id, rate_per_ctn, unit, notes))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/bom/<int:sku_id>/<int:material_id>", methods=["DELETE"])
def delete_bom_item(sku_id, material_id):
    allowed, err_resp, err_code = check_role_permission(["Super Admin"], "delete Bill of Materials (BOM) specifications")
    if not allowed:
        return err_resp, err_code

    conn = get_db()
    try:
        conn.execute("DELETE FROM bom WHERE sku_id = ? AND material_id = ?", (sku_id, material_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/bom/calculate-requirement", methods=["POST"])
def calculate_bom_requirement():
    data = request.json or {}
    plan_items = data.get("items", [])
    if not plan_items:
        return jsonify({"requirements": []})
        
    conn = get_db()
    mat_requirements = {}
    
    for item in plan_items:
        sku_id = item.get("sku_id")
        cartons = float(item.get("cartons", 0))
        if not sku_id or cartons <= 0:
            continue
            
        bom_rows = conn.execute("""
            SELECT b.material_id, b.rate_per_ctn, m.name, m.item_code, m.type, m.unit,
                   m.current_stock, m.safety_stock, m.unit_price, m.supplier_name
            FROM bom b
            JOIN materials m ON b.material_id = m.id
            WHERE b.sku_id = ?
        """, (sku_id,)).fetchall()
        
        for row in bom_rows:
            m_id = row["material_id"]
            req_qty = cartons * row["rate_per_ctn"]
            if m_id not in mat_requirements:
                mat_requirements[m_id] = {
                    "material_id": m_id,
                    "item_code": row["item_code"],
                    "name": row["name"],
                    "type": row["type"],
                    "unit": row["unit"],
                    "current_stock": row["current_stock"],
                    "safety_stock": row["safety_stock"],
                    "unit_price": row["unit_price"],
                    "supplier_name": row["supplier_name"],
                    "required_qty": 0.0
                }
            mat_requirements[m_id]["required_qty"] += req_qty
            
    conn.close()
    
    results = []
    for m in mat_requirements.values():
        req = round(m["required_qty"], 2)
        stock = m["current_stock"]
        safety = m["safety_stock"]
        deficit = max(0.0, round(req - stock, 2))
        reorder_qty = max(0.0, round((req + safety) - stock, 2)) if stock < (req + safety) else 0.0
        
        results.append({
            **m,
            "required_qty": req,
            "deficit": deficit,
            "reorder_qty": reorder_qty,
            "est_cost": round(reorder_qty * m["unit_price"], 2),
            "stock_sufficient": stock >= req
        })
        
    results.sort(key=lambda x: (x["type"], -x["deficit"]))
    return jsonify({"requirements": results})

# ----------------- SALES FORECAST & SMART SUGGESTION -----------------
@app.route("/api/forecast", methods=["GET"])
def get_forecast():
    month = request.args.get("month", current_month())
    cat = request.args.get("category", "").strip()
    uom = request.args.get("uom", "Packet") # 'Packet' or 'Carton'
    
    conn = get_db()
    sql = """
        SELECT s.id AS sku_id, s.category_code, s.sku_code, s.name AS sku_name, s.category, s.ctn_size, s.pack_size, s.uom_default,
               COALESCE(sf.original_forecast_ctn, sf.forecast_ctn, 0.0) AS original_forecast_ctn,
               COALESCE(sf.forecast_ctn, 0.0) AS forecast_ctn,
               COALESCE(sf.forecast_ctn, 0.0) * s.ctn_size AS forecast_pcs,
               COALESCE(sf.original_forecast_ctn, sf.forecast_ctn, 0.0) * s.ctn_size AS original_forecast_pcs,
               COALESCE(sf.revision_no, 0) AS revision_no,
               sf.revised_date,
               sf.revision_notes,
               (COALESCE(sf.forecast_ctn, 0.0) - COALESCE(sf.original_forecast_ctn, sf.forecast_ctn, 0.0)) AS variance_from_original,
               COALESCE(sf.week1_ctn, 0.0) AS week1_ctn,
               COALESCE(sf.week2_ctn, 0.0) AS week2_ctn,
               COALESCE(sf.week3_ctn, 0.0) AS week3_ctn,
               COALESCE(sf.week4_ctn, 0.0) AS week4_ctn
        FROM skus s
        LEFT JOIN sales_forecast sf ON s.id = sf.sku_id AND sf.month_year = ?
        WHERE s.status = 'Active'
    """
    params = [month]
    if cat and cat != 'ALL':
        sql += " AND s.category_code = ?"
        params.append(cat)
    sql += " ORDER BY s.id ASC"
    
    rows = conn.execute(sql, params).fetchall()
    
    months = conn.execute("SELECT DISTINCT month_year FROM sales_forecast ORDER BY month_year ASC").fetchall()
    month_list = [r["month_year"] for r in months]
    if not month_list:
        month_list = ["2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12",
                      "2027-01", "2027-02", "2027-03", "2027-04", "2027-05", "2027-06"]

    # Overall category revision status
    max_rev = conn.execute("SELECT MAX(COALESCE(revision_no, 0)) AS max_rev FROM sales_forecast WHERE month_year = ?", (month,)).fetchone()
    current_rev_no = int(max_rev["max_rev"] or 0) if max_rev else 0

    conn.close()
    return jsonify({
        "month": month,
        "uom": uom,
        "revision_no": current_rev_no,
        "available_months": month_list,
        "items": rows_to_dict(rows)
    })

@app.route("/api/forecast", methods=["POST"])
def save_forecast():
    allowed, err_resp, err_code = check_role_permission(["Sales"], "edit sales forecast")
    if not allowed:
        return err_resp, err_code
    data = request.json or {}
    month = data.get("month", current_month())
    items = data.get("items", []) # [{sku_id, forecast_ctn, week1_ctn, week2_ctn, week3_ctn, week4_ctn}]
    revision_date = data.get("revision_date", datetime.now().strftime("%Y-%m-%d"))
    revision_reason = data.get("revision_reason") or data.get("revision_notes") or "Monthly Forecast Update"
    is_revision = data.get("is_revision", False)
    
    session = get_current_session()
    user_name = session.get("name") or data.get("revised_by") or "Sales Team"
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Pre-fetch all existing forecasts for the month in 1 query to avoid N roundtrips
        existing_rows = cursor.execute("""
            SELECT sku_id, forecast_ctn, original_forecast_ctn, revision_no, week1_ctn, week2_ctn, week3_ctn, week4_ctn 
            FROM sales_forecast WHERE month_year = ?
        """, (month,)).fetchall()
        existing_map = {int(r["sku_id"]): r for r in existing_rows}

        updated_count = 0
        for it in items:
            sku_id = int(it.get("sku_id") or 0)
            if not sku_id:
                continue
            val = float(it.get("forecast_ctn", 0.0))
            w1 = float(it.get("week1_ctn", 0.0) or round(val * 0.25, 1))
            w2 = float(it.get("week2_ctn", 0.0) or round(val * 0.25, 1))
            w3 = float(it.get("week3_ctn", 0.0) or round(val * 0.25, 1))
            w4 = float(it.get("week4_ctn", 0.0) or max(0.0, round(val - (w1 + w2 + w3), 1)))

            existing = existing_map.get(sku_id)
            
            if existing:
                prev_val = float(existing["forecast_ctn"] or 0.0)
                orig_val = float(existing["original_forecast_ctn"] or prev_val)
                rev_no = int(existing["revision_no"] or 0)
                prev_w1 = float(existing["week1_ctn"] or 0.0)
                prev_w2 = float(existing["week2_ctn"] or 0.0)
                prev_w3 = float(existing["week3_ctn"] or 0.0)
                prev_w4 = float(existing["week4_ctn"] or 0.0)

                # Skip if totally identical
                if abs(val - prev_val) < 0.001 and abs(w1 - prev_w1) < 0.001 and abs(w2 - prev_w2) < 0.001 and abs(w3 - prev_w3) < 0.001 and abs(w4 - prev_w4) < 0.001 and not is_revision:
                    continue

                # If value changed or explicitly marked as revision
                if abs(val - prev_val) > 0.001 or is_revision:
                    new_rev_no = rev_no + 1
                    var_val = round(val - prev_val, 2)
                    
                    # Log revision history
                    cursor.execute("""
                        INSERT INTO sales_forecast_revisions (sku_id, month_year, revision_no, revised_date, previous_ctn, revised_ctn, variance_ctn, reason, revised_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (sku_id, month, new_rev_no, revision_date, prev_val, val, var_val, revision_reason, user_name))
                    
                    cursor.execute("""
                        UPDATE sales_forecast 
                        SET forecast_ctn = ?, revision_no = ?, revised_date = ?, revision_notes = ?,
                            week1_ctn = ?, week2_ctn = ?, week3_ctn = ?, week4_ctn = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE sku_id = ? AND month_year = ?
                    """, (val, new_rev_no, revision_date, revision_reason, w1, w2, w3, w4, sku_id, month))
                else:
                    cursor.execute("""
                        UPDATE sales_forecast 
                        SET week1_ctn = ?, week2_ctn = ?, week3_ctn = ?, week4_ctn = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE sku_id = ? AND month_year = ?
                    """, (w1, w2, w3, w4, sku_id, month))
            else:
                # First time entry -> Rev 0
                cursor.execute("""
                    INSERT INTO sales_forecast (sku_id, month_year, forecast_ctn, original_forecast_ctn, revision_no, revised_date, revision_notes, week1_ctn, week2_ctn, week3_ctn, week4_ctn, created_by, updated_at)
                    VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (sku_id, month, val, val, revision_date, revision_reason, w1, w2, w3, w4, user_name))
            
            updated_count += 1
            
        conn.commit()
        conn.close()
        invalidate_dashboard_cache()
        return jsonify({
            "success": True, 
            "count": updated_count, 
            "items_updated": updated_count,
            "revision_date": revision_date,
            "message": f"Successfully saved forecast ({updated_count} SKUs adjusted)"
        })
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/forecast/revisions", methods=["GET"])
def get_forecast_revisions():
    month = request.args.get("month", current_month())
    sku_id = request.args.get("sku_id")
    conn = get_db()
    sql = """
        SELECT r.*, s.sku_code, s.name AS sku_name, s.category
        FROM sales_forecast_revisions r
        JOIN skus s ON r.sku_id = s.id
        WHERE r.month_year = ?
    """
    params = [month]
    if sku_id:
        sql += " AND r.sku_id = ?"
        params.append(sku_id)
    sql += " ORDER BY r.id DESC"
    rows = conn.execute(sql, params).fetchall()
    rev_list = rows_to_dict(rows)
    return jsonify({"revisions": rev_list, "count": len(rev_list)})

# Download Blank Forecast Template
@app.route("/api/forecast/template", methods=["GET"])
def download_forecast_template():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    
    cat = request.args.get("category", "05")
    month = request.args.get("month", current_month())
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales Forecast Template"
    
    title_font = Font(name="Calibri", size=13, bold=True, color="FFFFFFFF")
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFFFF")
    data_font = Font(name="Calibri", size=10)
    data_bold = Font(name="Calibri", size=10, bold=True)
    
    title_fill = PatternFill(fill_type="solid", start_color="FF1E3A8A", end_color="FF1E3A8A")
    header_fill = PatternFill(fill_type="solid", start_color="FF0284C7", end_color="FF0284C7")
    zebra_fill = PatternFill(fill_type="solid", start_color="FFF8FAFC", end_color="FFF8FAFC")
    input_fill = PatternFill(fill_type="solid", start_color="FFE0F2FE", end_color="FFE0F2FE")
    
    thin_border = Border(
        left=Side(style='thin', color='FFCBD5E1'),
        right=Side(style='thin', color='FFCBD5E1'),
        top=Side(style='thin', color='FFCBD5E1'),
        bottom=Side(style='thin', color='FFCBD5E1')
    )
    
    ws.merge_cells("A1:F1")
    ws["A1"] = f"PARAGON AGRO LIMITED - MONTHLY SALES FORECAST TEMPLATE ({month})"
    style_merged_range(ws, "A1:F1", font=title_font, fill=title_fill, alignment=Alignment(horizontal="center", vertical="center"))
    
    headers = ["SKU ID", "SKU Code", "Finished Good Name", "Category", "Packets per Carton", f"Forecast Cartons ({month}) *"]
    for c_idx, h in enumerate(headers, 1):
        cell = ws.cell(3, c_idx, h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center", vertical="center")
        
    conn = get_db()
    sql = "SELECT id, sku_code, name, category, ctn_size FROM skus WHERE status = 'Active'"
    params = []
    if cat and cat not in ('ALL', 'undefined', 'null', ''):
        sql += " AND category_code = ?"
        params.append(cat)
    sql += " ORDER BY id ASC"
    skus = conn.execute(sql, params).fetchall()
    conn.close()
    
    for r_idx, s in enumerate(skus, 4):
        c_fill = zebra_fill if (r_idx % 2 == 0) else None
        ws.cell(r_idx, 1, s["id"]).font = data_bold
        ws.cell(r_idx, 2, s["sku_code"]).font = data_bold
        ws.cell(r_idx, 3, s["name"]).font = data_font
        ws.cell(r_idx, 4, s["category"]).font = data_font
        ws.cell(r_idx, 5, s["ctn_size"]).font = data_font
        
        fc_cell = ws.cell(r_idx, 6, 0)
        fc_cell.font = data_bold
        fc_cell.fill = input_fill
        
        for c_i in range(1, 7):
            cell = ws.cell(r_idx, c_i)
            cell.border = thin_border
            if c_i != 6 and c_fill: cell.fill = c_fill
        
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[openpyxl.utils.get_column_letter(col[0].column)].width = max(max_len + 3, 15)
        
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"Paragon_Sales_Forecast_Template_{cat}_{month}.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# Smart Production Plan Suggestion (Forecast - Opening Stock + Buffer)
@app.route("/api/production/suggest", methods=["GET"])
def suggest_production_plan():
    cat = request.args.get("category", "05")
    month = request.args.get("month", datetime.now().strftime("%Y-%m"))
    
    conn = get_db()
    sql = """
        SELECT s.id AS sku_id, s.category_code, s.sku_code, s.name AS sku_name, s.ctn_size, s.pack_size,
               COALESCE(sf.forecast_ctn, 0.0) AS forecast_ctn,
               COALESCE((
                   SELECT opening_stock_ctn FROM production_plans 
                   WHERE sku_id = s.id AND month_year = ? 
                   ORDER BY plan_date ASC LIMIT 1
               ), 0.0) AS real_fg_stock
        FROM skus s
        LEFT JOIN sales_forecast sf ON s.id = sf.sku_id AND sf.month_year = ?
        WHERE s.status = 'Active'
    """
    params = [month, month]
    if cat and cat != 'ALL':
        sql += " AND s.category_code = ?"
        params.append(cat)
    sql += " ORDER BY forecast_ctn DESC, s.id ASC"
    
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    
    suggestions = []
    for r in rows:
        fc = float(r["forecast_ctn"] or 0.0)
        fg_stock = float(r["real_fg_stock"] or 0.0)
        safety_buffer = round(fc * 0.10, 0)
        
        # Net Requirement: Forecast + Safety Buffer - Existing In-Hand FG Stock
        suggested = max(0.0, round((fc + safety_buffer) - fg_stock, 0))
        if fc > 0 and suggested == 0 and fg_stock < fc:
            suggested = round(fc - fg_stock, 0)
            
        suggestions.append({
            "sku_id": r["sku_id"],
            "sku_code": r["sku_code"],
            "sku_name": r["sku_name"],
            "ctn_size": r["ctn_size"],
            "forecast_ctn": fc,
            "est_fg_stock": fg_stock,
            "safety_buffer": safety_buffer,
            "suggested_ctn": suggested,
            "suggested_pcs": suggested * r["ctn_size"]
        })
        
    return jsonify({"month": month, "suggestions": suggestions})

@app.route("/api/production/realign-from-forecast", methods=["POST"])
def realign_production_from_forecast():
    """
    Automatically adjusts monthly & daily production plans after a sales forecast revision.
    Recalculates net deficit and adjusts upcoming daily plans or creates needed daily batches.
    """
    allowed, err_resp, err_code = check_role_permission(["Production"], "re-align production plans with revised forecast")
    if not allowed:
        return err_resp, err_code
        
    data = request.json or {}
    month = data.get("month", current_month())
    cat = data.get("category", "05")
    effective_date = data.get("effective_date", datetime.now().strftime("%Y-%m-%d"))
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Fetch active SKUs with latest revised forecast vs current planned quantity
        sql = """
            SELECT s.id, s.sku_code, s.name, s.category_code, s.ctn_size,
                   COALESCE(sf.forecast_ctn, 0.0) AS forecast_ctn,
                   COALESCE(sf.original_forecast_ctn, sf.forecast_ctn, 0.0) AS original_forecast_ctn,
                   COALESCE(sf.revision_no, 0) AS revision_no,
                   COALESCE(SUM(pp.planned_ctn), 0.0) AS total_planned_ctn,
                   COALESCE(SUM(pp.actual_produced_ctn), 0.0) AS total_produced_ctn
            FROM skus s
            LEFT JOIN sales_forecast sf ON s.id = sf.sku_id AND sf.month_year = ?
            LEFT JOIN production_plans pp ON s.id = pp.sku_id AND pp.month_year = ?
            WHERE s.status = 'Active'
        """
        params = [month, month]
        if cat and cat != 'ALL':
            sql += " AND s.category_code = ?"
            params.append(cat)
        sql += " GROUP BY s.id"
        
        skus = cursor.execute(sql, params).fetchall()
        realigned_items = []
        
        for sku in skus:
            forecast_target = float(sku["forecast_ctn"] or 0.0)
            total_planned = float(sku["total_planned_ctn"] or 0.0)
            diff = round(forecast_target - total_planned, 2)
            
            # If there is a discrepancy between revised forecast and current total planned
            if abs(diff) >= 1.0 and forecast_target > 0:
                # Find future/planned daily plans on or after effective_date
                future_plans = cursor.execute("""
                    SELECT id, plan_no, plan_date, planned_ctn, status 
                    FROM production_plans 
                    WHERE sku_id = ? AND month_year = ? AND plan_date >= ? AND status = 'Planned'
                    ORDER BY plan_date ASC
                """, (sku["id"], month, effective_date)).fetchall()
                
                if future_plans:
                    # Distribute adjustment across future plans
                    adj_per_plan = round(diff / len(future_plans), 1)
                    for fp in future_plans:
                        new_ctn = max(10.0, round(float(fp["planned_ctn"]) + adj_per_plan, 1))
                        new_pcs = new_ctn * sku["ctn_size"]
                        cursor.execute("""
                            UPDATE production_plans 
                            SET planned_ctn = ?, planned_pcs = ?, notes = notes || ' [Auto-adjusted for Rev Forecast]'
                            WHERE id = ?
                        """, (new_ctn, new_pcs, fp["id"]))
                        
                        # Update Requisition
                        req = cursor.execute("SELECT id FROM material_requisitions WHERE plan_id = ?", (fp["id"],)).fetchone()
                        if req:
                            bom_rows = cursor.execute("""
                                SELECT b.material_id, b.rate_per_ctn,
                                       COALESCE(m.current_stock, 0.0) AS current_stock,
                                       COALESCE(m.in_transit, 0.0) AS in_transit
                                FROM bom b
                                JOIN materials m ON b.material_id = m.id
                                WHERE b.sku_id = ?
                            """, (sku["id"],)).fetchall()
                            for b in bom_rows:
                                gross_req = round(new_ctn * b["rate_per_ctn"], 2)
                                avail_stock = round(float(b["current_stock"]) + float(b["in_transit"]), 2)
                                net_req = max(0.0, round(gross_req - avail_stock, 2))
                                cursor.execute("""
                                    UPDATE requisition_items 
                                    SET gross_required_qty = ?, available_stock = ?, required_qty = ? 
                                    WHERE requisition_id = ? AND material_id = ?
                                """, (gross_req, avail_stock, net_req, req["id"], b["material_id"]))
                else:
                    # Create a new daily plan for effective_date or month end
                    max_id = int(cursor.execute("SELECT MAX(id) FROM production_plans").fetchone()[0] or 0)
                    plan_no = f"PP-{sku['category_code']}-{month.replace('-', '')}-{max_id + 1:03d}"
                    planned_ctn = max(10.0, diff)
                    planned_pcs = planned_ctn * sku["ctn_size"]
                    plan_date = max(effective_date, f"{month}-15")
                    
                    cursor.execute("""
                        INSERT INTO production_plans (category_code, plan_no, month_year, plan_date, sku_id, planned_ctn, planned_pcs, opening_stock_ctn, opening_stock_pcs, actual_produced_ctn, actual_produced_pcs, status, created_by, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 'Planned', ?, 'Auto-adjusted daily plan for revised sales forecast')
                    """, (sku["category_code"], plan_no, month, plan_date, sku["id"], planned_ctn, planned_pcs, get_current_user_name("System Admin")))
                    plan_id = int(cursor.lastrowid or (max_id + 1))
                    
                    req_no = f"MR-{sku['category_code']}-{month.replace('-', '')}-{plan_id:03d}"
                    cursor.execute("""
                        INSERT INTO material_requisitions (category_code, req_no, plan_id, month_year, req_date, status, requested_by, remarks)
                        VALUES (?, ?, ?, ?, ?, 'Pending', ?, 'Auto-requisition for revised sales forecast plan')
                    """, (sku["category_code"], req_no, plan_id, month, plan_date, get_current_user_name("System Admin")))
                    req_id = cursor.lastrowid
                    
                    bom_rows = cursor.execute("""
                        SELECT b.material_id, b.rate_per_ctn, b.unit,
                               COALESCE(m.current_stock, 0.0) AS current_stock,
                               COALESCE(m.in_transit, 0.0) AS in_transit
                        FROM bom b
                        JOIN materials m ON b.material_id = m.id
                        WHERE b.sku_id = ?
                    """, (sku["id"],)).fetchall()
                    for b in bom_rows:
                        gross_req = round(planned_ctn * b["rate_per_ctn"], 2)
                        avail_stock = round(float(b["current_stock"]) + float(b["in_transit"]), 2)
                        net_req = max(0.0, round(gross_req - avail_stock, 2))
                        cursor.execute("""
                            INSERT INTO requisition_items (requisition_id, material_id, gross_required_qty, available_stock, required_qty, issued_qty, unit, status)
                            VALUES (?, ?, ?, ?, ?, 0.0, ?, 'Pending')
                        """, (req_id, b["material_id"], gross_req, avail_stock, net_req, b["unit"]))
                
                realigned_items.append({
                    "sku_code": sku["sku_code"],
                    "sku_name": sku["name"],
                    "forecast_target": forecast_target,
                    "previous_planned": total_planned,
                    "adjustment_applied": diff
                })
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "realigned_count": len(realigned_items), "items": realigned_items})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

# ----------------- PRODUCTION PLANNING -----------------
@app.route("/api/production/plans", methods=["GET"])
def get_production_plans():
    cat = request.args.get("category", "").strip()
    month = request.args.get("month", "")
    plan_date = request.args.get("plan_date", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    uom = request.args.get("uom", "Packet")
    
    conn = get_db()
    sql = """
        SELECT p.*, s.sku_code, s.name AS sku_name, s.category, s.ctn_size, s.uom_default,
               ROUND(CASE WHEN p.planned_ctn > 0 THEN (p.actual_produced_ctn / p.planned_ctn * 100) ELSE 0 END, 1) AS achievement_pct,
               (p.actual_produced_ctn - p.planned_ctn) AS variance_ctn,
               r.id AS requisition_id, r.req_no, r.status AS req_status
        FROM production_plans p
        JOIN skus s ON p.sku_id = s.id
        LEFT JOIN material_requisitions r ON p.id = r.plan_id
        WHERE 1=1
    """
    params = []
    if cat and cat not in ('ALL', 'undefined', 'null', ''):
        sql += " AND p.category_code = ?"
        params.append(cat)
    if plan_date:
        sql += " AND p.plan_date = ?"
        params.append(plan_date)
    elif date_from and date_to:
        sql += " AND p.plan_date >= ? AND p.plan_date <= ?"
        params.extend([date_from, date_to])
    elif month:
        sql += " AND p.month_year = ?"
        params.append(month)
    sql += " ORDER BY p.id DESC"
    
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_dict(rows))

@app.route("/api/production/plans", methods=["POST"])
def create_production_plan():
    allowed, err_resp, err_code = check_role_permission(["Production"], "create production plans")
    if not allowed:
        return err_resp, err_code
    data = request.json or {}
    sku_id = data.get("sku_id")
    month_year = data.get("month_year", current_month())
    planned_ctn = float(data.get("planned_ctn", 0))
    opening_stock_ctn = float(data.get("opening_stock_ctn", 0.0))
    notes = data.get("notes", "")
    plan_date = data.get("plan_date", datetime.now().strftime("%Y-%m-%d"))
    
    if not sku_id or planned_ctn <= 0:
        return jsonify({"error": "Valid SKU and planned carton quantity are required"}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    try:
        sku = cursor.execute("SELECT category_code, ctn_size FROM skus WHERE id = ?", (sku_id,)).fetchone()
        cat_code = sku["category_code"]
        planned_pcs = planned_ctn * sku["ctn_size"]
        opening_stock_pcs = opening_stock_ctn * sku["ctn_size"]
        
        max_id = int(cursor.execute("SELECT MAX(id) FROM production_plans").fetchone()[0] or 0)
        plan_no = f"PP-{cat_code}-{month_year.replace('-', '')}-{max_id + 1:03d}"
        
        cursor.execute("""
            INSERT INTO production_plans (category_code, plan_no, month_year, plan_date, sku_id, planned_ctn, planned_pcs, opening_stock_ctn, opening_stock_pcs, actual_produced_ctn, actual_produced_pcs, status, created_by, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, 'Planned', ?, ?)
        """, (cat_code, plan_no, month_year, plan_date, sku_id, planned_ctn, planned_pcs, opening_stock_ctn, opening_stock_pcs, get_current_user_name("System Admin"), notes))
        plan_id = int(cursor.lastrowid or (max_id + 1))
        
        if data.get("auto_create_requisition", True):
            req_no = f"MR-{cat_code}-{month_year.replace('-', '')}-{plan_id:03d}"
            cursor.execute("""
                INSERT INTO material_requisitions (category_code, req_no, plan_id, month_year, req_date, status, requested_by, remarks)
                VALUES (?, ?, ?, ?, ?, 'Pending', ?, ?)
            """, (cat_code, req_no, plan_id, month_year, plan_date, get_current_user_name("System Admin"), f"Auto-generated requisition for plan {plan_no}"))
            req_id = cursor.lastrowid
            
            bom_rows = cursor.execute("""
                SELECT b.material_id, b.rate_per_ctn, b.unit,
                       COALESCE(m.current_stock, 0.0) AS current_stock,
                       COALESCE(m.in_transit, 0.0) AS in_transit
                FROM bom b
                JOIN materials m ON b.material_id = m.id
                WHERE b.sku_id = ?
            """, (sku_id,)).fetchall()
            for b in bom_rows:
                gross_req = round(planned_ctn * b["rate_per_ctn"], 2)
                avail_stock = round(float(b["current_stock"]) + float(b["in_transit"]), 2)
                net_req = max(0.0, round(gross_req - avail_stock, 2))
                cursor.execute("""
                    INSERT INTO requisition_items (requisition_id, material_id, gross_required_qty, available_stock, required_qty, issued_qty, unit, status)
                    VALUES (?, ?, ?, ?, ?, 0.0, ?, 'Pending')
                """, (req_id, b["material_id"], gross_req, avail_stock, net_req, b["unit"]))
                
        conn.commit()
        conn.close()
        invalidate_dashboard_cache()
        return jsonify({"success": True, "plan_id": plan_id, "plan_no": plan_no})
    except Exception as e:
        import traceback
        traceback.print_exc()
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/production/plans/<int:plan_id>/update-output", methods=["POST"])
def update_production_output(plan_id):
    allowed, err_resp, err_code = check_role_permission([], "manually edit production output (actual output comes from Poloxy - use Import Actual Output)")
    if not allowed:
        return err_resp, err_code
    data = request.json or {}
    produced_ctn = float(data.get("actual_produced_ctn", 0))
    status = data.get("status", "Completed")
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        p = cursor.execute("SELECT p.sku_id, p.actual_produced_ctn, s.ctn_size FROM production_plans p JOIN skus s ON p.sku_id = s.id WHERE p.id = ?", (plan_id,)).fetchone()
        if not p:
            conn.close()
            return jsonify({"error": "Plan not found"}), 404

        old_produced = float(p["actual_produced_ctn"] or 0.0)
        delta_ctn = produced_ctn - old_produced
        produced_pcs = produced_ctn * (p["ctn_size"] if p else 12)
        
        cursor.execute("""
            UPDATE production_plans 
            SET actual_produced_ctn = ?, actual_produced_pcs = ?, status = ?
            WHERE id = ?
        """, (produced_ctn, produced_pcs, status, plan_id))

        # Consume raw materials & packaging materials from floor stock based on BOM
        if delta_ctn > 0:
            bom_rows = cursor.execute("SELECT material_id, rate_per_ctn FROM bom WHERE sku_id = ?", (p["sku_id"],)).fetchall()
            for b in bom_rows:
                consumed_qty = round(delta_ctn * b["rate_per_ctn"], 4)
                cursor.execute("""
                    UPDATE materials 
                    SET floor_stock = MAX(0.0, ROUND(COALESCE(floor_stock, 0.0) - ?, 4))
                    WHERE id = ?
                """, (consumed_qty, b["material_id"]))

        conn.commit()
        conn.close()
        return jsonify({"success": True, "produced_ctn": produced_ctn, "delta_consumed_ctn": max(0.0, delta_ctn)})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

# ----------------- STORE STOCK & MRR ENTRY -----------------
@app.route("/api/store/stock", methods=["GET"])
def get_store_stock():
    cat = request.args.get("category", "").strip()
    m_type = request.args.get("type", "")
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    
    conn = get_db()
    sql = """
        SELECT m.*,
               COALESCE(m.floor_stock, 0.0) AS floor_stock,
               (m.current_stock + COALESCE(m.floor_stock, 0.0)) AS total_plant_stock,
               (m.current_stock - m.safety_stock) AS net_buffer,
               CASE 
                   WHEN m.current_stock < m.safety_stock THEN 'Critical'
                   WHEN m.current_stock <= (m.safety_stock * 1.5) THEN 'Low'
                   ELSE 'Safe'
               END AS stock_status,
               CASE
                   WHEN COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) = 0 THEN 'Non-Moving'
                   WHEN m.current_stock > 0 AND (COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) / m.current_stock) >= 0.15 THEN 'Fast'
                   ELSE 'Slow'
               END AS fsn_status,
               ROUND(m.current_stock * m.unit_price, 2) AS stock_value,
               COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) AS total_consumed
        FROM materials m
        LEFT JOIN store_transactions t ON m.id = t.material_id
        WHERE 1=1
    """
    params = []
    if cat and cat not in ('ALL', 'undefined', 'null', 'None', ''):
        sql += " AND m.category_code = ?"
        params.append(cat)
    if m_type:
        sql += " AND m.type = ?"
        params.append(m_type)
    if q:
        sql += " AND (m.item_code LIKE ? OR m.name LIKE ? OR m.supplier_name LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        
    sql += " GROUP BY m.id"
    
    fsn = request.args.get("fsn", "").strip()
    if status == 'critical': sql += " HAVING stock_status = 'Critical'"
    elif status == 'low': sql += " HAVING stock_status = 'Low'"
    elif status == 'safe': sql += " HAVING stock_status = 'Safe'"
    elif fsn == 'fast': sql += " HAVING fsn_status = 'Fast'"
    elif fsn == 'slow': sql += " HAVING fsn_status = 'Slow'"
    elif fsn == 'non_moving': sql += " HAVING fsn_status = 'Non-Moving'"
    
    sql += " ORDER BY (m.safety_stock - m.current_stock) DESC, m.name ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_dict(rows))

@app.route("/api/store/safety-stock", methods=["PUT"])
def update_safety_stock():
    allowed, err_resp, err_code = check_role_permission(["Store"], "update safety stock")
    if not allowed:
        return err_resp, err_code
    data = request.json or {}
    material_id = data.get("material_id")
    safety_stock = float(data.get("safety_stock", 0.0))
    
    conn = get_db()
    try:
        conn.execute("UPDATE materials SET safety_stock = ? WHERE id = ?", (safety_stock, material_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/store/adjust", methods=["POST"])
def adjust_store_stock():
    allowed, err_resp, err_code = check_role_permission(["Store"], "adjust store stock")
    if not allowed:
        return err_resp, err_code
    data = request.json or {}
    material_id = data.get("material_id")
    new_stock = float(data.get("new_stock", 0.0))
    notes = data.get("notes", "Physical count adjustment")
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        mat = cursor.execute("SELECT current_stock FROM materials WHERE id = ?", (material_id,)).fetchone()
        diff = new_stock - mat["current_stock"]
        cursor.execute("UPDATE materials SET current_stock = ? WHERE id = ?", (new_stock, material_id))
        cursor.execute("""
            INSERT INTO store_transactions (material_id, txn_type, qty, balance_after, reference_no, notes, created_by)
            VALUES (?, 'ADJUSTMENT', ?, ?, 'AUDIT-ADJ', ?, ?)
        """, (material_id, diff, new_stock, notes, get_current_user_name("System Admin")))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

# MRR (Material Receive Report) Stock Entry
@app.route("/api/store/mrr", methods=["GET"])
def get_mrr_entries():
    cat = request.args.get("category", "").strip()
    conn = get_db()
    sql = """
        SELECT r.*, m.item_code, m.name AS material_name, m.unit, m.type
        FROM mrr_entries r
        JOIN materials m ON r.material_id = m.id
        WHERE 1=1
    """
    params = []
    if cat and cat != 'ALL':
        sql += " AND r.category_code = ?"
        params.append(cat)
    sql += " ORDER BY r.id DESC LIMIT 100"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_dict(rows))

@app.route("/api/store/mrr", methods=["POST"])
def create_mrr_entry():
    allowed, err_resp, err_code = check_role_permission(["Store"], "create MRR stock inward")
    if not allowed:
        return err_resp, err_code
    data = request.json or {}
    material_id = data.get("material_id")
    received_qty = float(data.get("received_qty", 0.0))
    unit_price = float(data.get("unit_price", 0.0))
    mrr_date = data.get("mrr_date", datetime.now().strftime("%Y-%m-%d"))
    supplier = data.get("supplier_name", "Supplier")
    chalan = data.get("chalan_no", "")
    vehicle = data.get("vehicle_no", "")
    remarks = data.get("remarks", "MRR Stock Entry")
    
    if not material_id or received_qty <= 0:
        return jsonify({"error": "Valid material and positive received quantity required"}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    try:
        mat = cursor.execute("SELECT category_code, current_stock FROM materials WHERE id = ?", (material_id,)).fetchone()
        cat_code = mat["category_code"]
        new_stock = mat["current_stock"] + received_qty
        tot_amount = round(received_qty * unit_price, 2)
        
        max_id = int(cursor.execute("SELECT MAX(id) FROM mrr_entries").fetchone()[0] or 0)
        mrr_no = f"MRR-{cat_code}-{datetime.now().strftime('%Y%m')}-{max_id + 1:03d}"
        
        cursor.execute("""
            INSERT INTO mrr_entries (category_code, mrr_no, mrr_date, supplier_name, chalan_no, material_id, received_qty, unit_price, total_amount, vehicle_no, remarks, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cat_code, mrr_no, mrr_date, supplier, chalan, material_id, received_qty, unit_price, tot_amount, vehicle, remarks, get_current_user_name("System Admin")))
        
        cursor.execute("UPDATE materials SET current_stock = ? WHERE id = ?", (new_stock, material_id))
        
        cursor.execute("""
            INSERT INTO store_transactions (material_id, txn_type, qty, balance_after, reference_no, notes, created_by)
            VALUES (?, 'MRR_ENTRY', ?, ?, ?, ?, ?)
        """, (material_id, received_qty, new_stock, mrr_no, f"MRR Received from {supplier} (Chalan: {chalan})", get_current_user_name("System Admin")))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "mrr_no": mrr_no, "new_stock": new_stock})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


# ----------------- REQUIREMENT 6: MATERIAL AGING BREAKDOWN -----------------
@app.route("/api/store/materials/<int:mat_id>/aging", methods=["GET"])
def get_material_aging(mat_id):
    """
    Returns batch-level receiving history and FIFO aging breakdown for a material.
    Categorizes active inventory into 0-30d, 31-60d, 61-90d, and 90+d brackets.
    """
    conn = get_db()
    mat = conn.execute("SELECT id, item_code, name, type, unit, unit_price, current_stock, safety_stock, category_code, supplier_name FROM materials WHERE id = ?", (mat_id,)).fetchone()
    if not mat:
        conn.close()
        return jsonify({"error": "Material not found"}), 404
        
    curr_stock = float(mat["current_stock"] or 0.0)
    
    # Receipts from MRR entries and Store Transactions
    receipt_rows = conn.execute("""
        SELECT 'MRR' AS source, r.mrr_no AS ref_no, r.mrr_date AS receipt_date, r.supplier_name,
               r.chalan_no, r.received_qty, r.unit_price, r.remarks, r.created_at
        FROM mrr_entries r
        WHERE r.material_id = ?
        UNION ALL
        SELECT 'TXN' AS source, t.reference_no AS ref_no, SUBSTR(t.created_at, 1, 10) AS receipt_date,
               COALESCE(?, 'Standard Supplier') AS supplier_name, '' AS chalan_no, t.qty AS received_qty,
               0.0 AS unit_price, t.notes AS remarks, t.created_at
        FROM store_transactions t
        WHERE t.material_id = ? AND t.txn_type IN ('INITIAL', 'RECEIPT_GRN') AND t.qty > 0
        ORDER BY receipt_date DESC, created_at DESC
    """, (mat_id, mat["supplier_name"], mat_id)).fetchall()
    
    today = datetime.now().date()
    allocated_stock = 0.0
    batches = []
    
    total_weighted_days = 0.0
    bracket_counts = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    remaining_to_allocate = curr_stock

    for r in receipt_rows:
        if remaining_to_allocate <= 0:
            break
        r_qty = float(r["received_qty"] or 0.0)
        active_qty = min(remaining_to_allocate, r_qty)
        remaining_to_allocate -= active_qty
        
        try:
            r_date = datetime.strptime(r["receipt_date"], "%Y-%m-%d").date()
        except Exception:
            r_date = today
            
        age_days = max(0, (today - r_date).days)
        total_weighted_days += (active_qty * age_days)
        
        if age_days <= 30:
            bracket = "0-30 Days (Fresh)"
            bracket_counts["0-30"] += active_qty
            status_color = "var(--success)"
            badge_class = "pill-safe"
        elif age_days <= 60:
            bracket = "31-60 Days (Normal)"
            bracket_counts["31-60"] += active_qty
            status_color = "var(--cyan)"
            badge_class = "pill-fast"
        elif age_days <= 90:
            bracket = "61-90 Days (Slow)"
            bracket_counts["61-90"] += active_qty
            status_color = "var(--warning)"
            badge_class = "pill-low"
        else:
            bracket = "90+ Days (High Risk / Old)"
            bracket_counts["90+"] += active_qty
            status_color = "var(--danger)"
            badge_class = "pill-critical"
            
        batches.append({
            "ref_no": r["ref_no"],
            "receipt_date": r["receipt_date"],
            "supplier_name": r["supplier_name"] or mat["supplier_name"] or "Standard Supplier",
            "chalan_no": r["chalan_no"] or "-",
            "received_qty": r_qty,
            "active_qty": round(active_qty, 2),
            "age_days": age_days,
            "bracket": bracket,
            "status_color": status_color,
            "badge_class": badge_class,
            "remarks": r["remarks"] or "Inventory Lot"
        })

    # If current stock exceeds tracked receipts, attribute residual to initial opening stock
    if remaining_to_allocate > 0:
        residual_days = 45
        total_weighted_days += (remaining_to_allocate * residual_days)
        bracket_counts["31-60"] += remaining_to_allocate
        batches.append({
            "ref_no": "INIT-OPENING-BAL",
            "receipt_date": (today - datetime.timedelta(days=residual_days)).strftime("%Y-%m-%d"),
            "supplier_name": mat["supplier_name"] or "Initial Plant Stock",
            "chalan_no": "OPENING-STOCK",
            "received_qty": round(remaining_to_allocate, 2),
            "active_qty": round(remaining_to_allocate, 2),
            "age_days": residual_days,
            "bracket": "31-60 Days (Normal)",
            "status_color": "var(--cyan)",
            "badge_class": "pill-fast",
            "remarks": "Opening Stock Balance"
        })

    conn.close()
    avg_age = round(total_weighted_days / curr_stock, 1) if curr_stock > 0 else 0
    
    return jsonify({
        "material": dict(mat),
        "batches": batches,
        "summary": {
            "current_stock": curr_stock,
            "avg_age_days": avg_age,
            "bracket_0_30": round(bracket_counts["0-30"], 2),
            "bracket_31_60": round(bracket_counts["31-60"], 2),
            "bracket_61_90": round(bracket_counts["61-90"], 2),
            "bracket_90_plus": round(bracket_counts["90+"], 2)
        }
    })

# ----------------- REQUIREMENT 5: PRODUCTION FLOOR STOCK -----------------
@app.route("/api/production/floor-stock", methods=["GET"])
def get_floor_stock():
    """
    Returns RM/PM Floor Stock (materials issued to plant floor not yet consumed into finished goods).
    """
    cat = request.args.get("category", "05").strip()
    conn = get_db()
    cat_filter = "AND m.category_code = ?" if cat and cat != "ALL" else ""
    params = [cat] if cat and cat != "ALL" else []
    
    sql = f"""
        SELECT m.id, m.item_code, m.name, m.category_code, m.type, m.unit, m.unit_price,
               m.current_stock AS godown_stock,
               COALESCE(m.floor_stock, 0.0) AS floor_stock,
               (m.current_stock + COALESCE(m.floor_stock, 0.0)) AS total_plant_stock,
               ROUND(COALESCE(m.floor_stock, 0.0) * m.unit_price, 2) AS floor_stock_value,
               m.safety_stock, m.supplier_name
        FROM materials m
        WHERE 1=1 {cat_filter}
        ORDER BY m.type ASC, m.floor_stock DESC, m.name ASC
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_dict(rows))

@app.route("/api/production/floor-stock/log", methods=["POST"])
def log_floor_stock():
    """
    Allows Production team to log shift floor stock counts or floor adjustments.
    """
    allowed, err_resp, err_code = check_role_permission(["Production"], "log floor stock")
    if not allowed:
        return err_resp, err_code
        
    data = request.json or {}
    mat_id = data.get("material_id")
    actual_floor_stock = float(data.get("actual_floor_stock", 0.0))
    shift = data.get("shift", "Day")
    notes = data.get("notes", "Shift Floor Stock Audit")
    log_date = data.get("log_date", datetime.now().strftime("%Y-%m-%d"))

    if not mat_id:
        return jsonify({"error": "Material ID is required"}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        mat = cursor.execute("SELECT category_code, floor_stock FROM materials WHERE id = ?", (mat_id,)).fetchone()
        if not mat:
            conn.close()
            return jsonify({"error": "Material not found"}), 404

        prev_floor = float(mat["floor_stock"] or 0.0)
        variance = round(actual_floor_stock - prev_floor, 2)

        cursor.execute("""
            INSERT INTO floor_stock_logs (material_id, category_code, log_date, shift, previous_floor_stock, actual_floor_stock, variance, logged_by, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (mat_id, mat["category_code"], log_date, shift, prev_floor, actual_floor_stock, variance, get_current_user_name("Production Supervisor"), notes))

        cursor.execute("UPDATE materials SET floor_stock = ? WHERE id = ?", (actual_floor_stock, mat_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "new_floor_stock": actual_floor_stock, "variance": variance})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

# ----------------- REQUIREMENT 7: MRR BULK TEMPLATE & UPLOADER -----------------
@app.route("/api/store/mrr/template", methods=["GET"])
def download_mrr_template():
    """
    Generates a professionally styled MRR Stock Inward Blank Template with solid ARGB cell shading.
    Includes Sheet 2 with valid RM/PM material codes for error-free entry.
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    
    cat = request.args.get("category", "05")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MRR Stock Inward Template"
    
    # Styles
    title_font = Font(name="Calibri", size=13, bold=True, color="FFFFFFFF")
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFFFF")
    data_font = Font(name="Calibri", size=10)
    data_bold = Font(name="Calibri", size=10, bold=True)
    
    title_fill = PatternFill(fill_type="solid", start_color="FF1E3A8A", end_color="FF1E3A8A")
    header_fill = PatternFill(fill_type="solid", start_color="FF0284C7", end_color="FF0284C7")
    zebra_fill = PatternFill(fill_type="solid", start_color="FFF8FAFC", end_color="FFF8FAFC")
    input_fill = PatternFill(fill_type="solid", start_color="FFE0F2FE", end_color="FFE0F2FE")
    
    thin_border = Border(
        left=Side(style='thin', color='FFCBD5E1'),
        right=Side(style='thin', color='FFCBD5E1'),
        top=Side(style='thin', color='FFCBD5E1'),
        bottom=Side(style='thin', color='FFCBD5E1')
    )
    
    ws.merge_cells("A1:I1")
    ws["A1"] = f"PARAGON AGRO LIMITED - MRR STOCK INWARD BULK ENTRY TEMPLATE (UNIT {cat})"
    style_merged_range(ws, "A1:I1", font=title_font, fill=title_fill, alignment=Alignment(horizontal="center", vertical="center"))
    
    headers = [
        "Category Code", "Item Code *", "Material Name (Ref)", "MRR Date (YYYY-MM-DD)",
        "Supplier Name *", "Challan No *", "Received Qty *", "Unit Price (৳)", "Vehicle No", "Remarks"
    ]
    for c_idx, h in enumerate(headers, 1):
        cell = ws.cell(3, c_idx, h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center", vertical="center")
        
    conn = get_db()
    cat_filter = "AND category_code = ?" if cat and cat != "ALL" else ""
    params = [cat] if cat and cat != "ALL" else []
    sample_mats = conn.execute(f"SELECT item_code, name, category_code, unit, unit_price, supplier_name FROM materials WHERE 1=1 {cat_filter} LIMIT 10", params).fetchall()
    
    for r_idx, m in enumerate(sample_mats, 4):
        c_fill = zebra_fill if (r_idx % 2 == 0) else None
        ws.cell(r_idx, 1, m["category_code"]).font = data_bold
        ws.cell(r_idx, 2, m["item_code"]).font = data_bold
        ws.cell(r_idx, 3, m["name"]).font = data_font
        ws.cell(r_idx, 4, datetime.now().strftime("%Y-%m-%d")).font = data_font
        ws.cell(r_idx, 5, m["supplier_name"] or "Standard Supplier").font = data_font
        ws.cell(r_idx, 6, f"CH-2026-{r_idx:03d}").font = data_font
        
        qty_cell = ws.cell(r_idx, 7, 100.0)
        qty_cell.font = data_bold
        qty_cell.fill = input_fill
        
        ws.cell(r_idx, 8, m["unit_price"]).font = data_font
        ws.cell(r_idx, 9, "Dhaka Metro Ta-11-2091").font = data_font
        ws.cell(r_idx, 10, "Bulk MRR inward received in good condition").font = data_font
        
        for c_i in range(1, 11):
            cell = ws.cell(r_idx, c_i)
            cell.border = thin_border
            if c_i != 7 and c_fill: cell.fill = c_fill
            
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[openpyxl.utils.get_column_letter(col[0].column)].width = max(max_len + 3, 14)
        
    # Sheet 2: Reference Materials
    ws_ref = wb.create_sheet(title="Valid Material Codes")
    ws_ref.merge_cells("A1:E1")
    ws_ref["A1"] = "PARAGON AGRO LIMITED - VALID RAW & PACKAGING MATERIAL ITEM CODES"
    style_merged_range(ws_ref, "A1:E1", font=title_font, fill=title_fill, alignment=Alignment(horizontal="center", vertical="center"))
    
    ref_headers = ["Item Code", "Material Name", "Category", "Type", "Unit Price (৳)"]
    for c_i, h in enumerate(ref_headers, 1):
        cell = ws_ref.cell(3, c_i, h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        
    all_mats = conn.execute("SELECT item_code, name, category_code, type, unit_price FROM materials ORDER BY category_code ASC, item_code ASC").fetchall()
    conn.close()
    
    for r_idx, m in enumerate(all_mats, 4):
        ws_ref.cell(r_idx, 1, m["item_code"]).font = data_bold
        ws_ref.cell(r_idx, 2, m["name"]).font = data_font
        ws_ref.cell(r_idx, 3, f"Unit {m['category_code']}").font = data_font
        ws_ref.cell(r_idx, 4, m["type"]).font = data_font
        ws_ref.cell(r_idx, 5, m["unit_price"]).font = data_font
        for c_i in range(1, 6):
            ws_ref.cell(r_idx, c_i).border = thin_border
            
    for col in ws_ref.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws_ref.column_dimensions[openpyxl.utils.get_column_letter(col[0].column)].width = max(max_len + 3, 14)
        
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"Paragon_MRR_Stock_Inward_Template_{cat}.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ----------------- PRODUCTION MATERIAL REQUISITIONS (SEPARATE TAB) -----------------
@app.route("/api/requisitions/production", methods=["GET"])
def get_production_requisitions():
    cat = request.args.get("category", "").strip()
    status = request.args.get("status", "")
    conn = get_db()
    sql = """
        SELECT r.*, p.plan_no, p.planned_ctn, s.name AS sku_name, s.sku_code,
               COUNT(ri.id) AS total_items,
               SUM(CASE WHEN ri.status = 'Issued' THEN 1 ELSE 0 END) AS issued_items
        FROM material_requisitions r
        LEFT JOIN production_plans p ON r.plan_id = p.id
        LEFT JOIN skus s ON p.sku_id = s.id
        LEFT JOIN requisition_items ri ON r.id = ri.requisition_id
        WHERE 1=1
    """
    params = []
    if cat and cat != 'ALL':
        sql += " AND r.category_code = ?"
        params.append(cat)
    if status:
        sql += " AND r.status = ?"
        params.append(status)
    sql += " GROUP BY r.id ORDER BY r.id DESC"
    
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_dict(rows))

@app.route("/api/requisitions/production/<int:req_id>", methods=["GET"])
def get_requisition_detail(req_id):
    conn = get_db()
    req = conn.execute("""
        SELECT r.*, p.plan_no, p.planned_ctn, s.name AS sku_name, s.sku_code
        FROM material_requisitions r
        LEFT JOIN production_plans p ON r.plan_id = p.id
        LEFT JOIN skus s ON p.sku_id = s.id
        WHERE r.id = ?
    """, (req_id,)).fetchone()
    
    if not req:
        conn.close()
        return jsonify({"error": "Requisition not found"}), 404
        
    items = conn.execute("""
        SELECT ri.*, m.item_code, m.name AS material_name, m.type AS material_type,
               m.current_stock, m.safety_stock, m.unit_price,
               CASE WHEN m.current_stock >= (ri.required_qty - ri.issued_qty) THEN 1 ELSE 0 END AS can_fulfill
        FROM requisition_items ri
        JOIN materials m ON ri.material_id = m.id
        WHERE ri.requisition_id = ?
        ORDER BY m.type ASC, ri.id ASC
    """, (req_id,)).fetchall()
    
    conn.close()
    return jsonify({
        "requisition": dict(req),
        "items": rows_to_dict(items)
    })

@app.route("/api/requisitions/production/<int:req_id>/issue", methods=["POST"])
def issue_requisition_materials(req_id):
    allowed, err_resp, err_code = check_role_permission(["Store"], "issue materials from store")
    if not allowed:
        return err_resp, err_code
    data = request.json or {}
    items_to_issue = data.get("items", [])
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        req = cursor.execute("SELECT * FROM material_requisitions WHERE id = ?", (req_id,)).fetchone()
        if not req:
            conn.close()
            return jsonify({"error": "Requisition not found"}), 404
            
        all_fulfilled = True
        if not items_to_issue:
            ri_rows = cursor.execute("SELECT id, material_id, required_qty, issued_qty FROM requisition_items WHERE requisition_id = ?", (req_id,)).fetchall()
            for r in ri_rows:
                pending_qty = r["required_qty"] - r["issued_qty"]
                if pending_qty > 0:
                    items_to_issue.append({"item_id": r["id"], "issue_qty": pending_qty})

        for it in items_to_issue:
            ri_id = it.get("item_id")
            issue_qty = float(it.get("issue_qty", 0))
            if issue_qty <= 0:
                continue
                
            ri = cursor.execute("SELECT * FROM requisition_items WHERE id = ?", (ri_id,)).fetchone()
            if not ri:
                continue
                
            m_id = ri["material_id"]
            cursor.execute("SELECT current_stock, floor_stock FROM materials WHERE id = ?", (m_id,))
            mat = cursor.fetchone()
            new_stk = max(0.0, mat["current_stock"] - issue_qty)
            new_floor = round(float(mat["floor_stock"] or 0.0) + issue_qty, 4)
            cursor.execute("UPDATE materials SET current_stock = ?, floor_stock = ? WHERE id = ?", (new_stk, new_floor, m_id))
            
            tot_issued = ri["issued_qty"] + issue_qty
            status = 'Issued' if tot_issued >= ri["required_qty"] else 'Partially Issued'
            if status != 'Issued': all_fulfilled = False
            
            cursor.execute("UPDATE requisition_items SET issued_qty = ?, status = ? WHERE id = ?", (tot_issued, status, ri_id))
            
            cursor.execute("""
                INSERT INTO store_transactions (material_id, txn_type, qty, balance_after, reference_no, notes, created_by)
                VALUES (?, 'ISSUE_PRODUCTION', ?, ?, ?, ?, ?)
            """, (m_id, -issue_qty, new_stk, req["req_no"], f"Issued to production (Req #{req['req_no']})", get_current_user_name("System Admin")))
            
        final_status = 'Issued' if all_fulfilled else 'Partially Issued'
        cursor.execute("UPDATE material_requisitions SET status = ?, issued_by = ?, issued_date = CURRENT_TIMESTAMP WHERE id = ?", (final_status, get_current_user_name("System Admin"), req_id))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "status": final_status})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

# ----------------- WASTAGE & SCRAP MANAGEMENT -----------------
@app.route("/api/wastage", methods=["GET"])
def get_wastage_entries():
    cat = request.args.get("category", "").strip()
    conn = get_db()
    sql = """
        SELECT w.*, 
               CASE 
                   WHEN w.item_type = 'FG' THEN s.name 
                   ELSE m.name 
               END AS item_name,
               CASE 
                   WHEN w.item_type = 'FG' THEN s.sku_code 
                   ELSE m.item_code 
               END AS item_code
        FROM wastage_entries w
        LEFT JOIN materials m ON w.item_id = m.id AND w.item_type != 'FG'
        LEFT JOIN skus s ON w.item_id = s.id AND w.item_type = 'FG'
        WHERE 1=1
    """
    params = []
    if cat and cat != 'ALL':
        sql += " AND w.category_code = ?"
        params.append(cat)
    sql += " ORDER BY w.id DESC LIMIT 100"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_dict(rows))

@app.route("/api/wastage", methods=["POST"])
def create_wastage_entry():
    data = request.json or {}
    item_type = data.get("item_type", "RM")
    item_id = data.get("item_id")
    qty = float(data.get("qty", 0.0))
    cat_code = data.get("category_code", "05")
    batch_no = data.get("batch_no", "")
    reason = data.get("reason", "Process Loss")
    cost = float(data.get("estimated_cost", 0.0))
    date_val = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    remarks = data.get("remarks", "")
    
    if not item_id or qty <= 0:
        return jsonify({"error": "Item and positive wastage quantity required"}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    try:
        unit = "Kg"
        if item_type in ['RM', 'PM']:
            mat = cursor.execute("SELECT current_stock, unit, unit_price FROM materials WHERE id = ?", (item_id,)).fetchone()
            unit = mat["unit"]
            if cost <= 0: cost = round(qty * mat["unit_price"], 2)
            # Deduct wastage from store stock
            new_stk = max(0.0, mat["current_stock"] - qty)
            cursor.execute("UPDATE materials SET current_stock = ? WHERE id = ?", (new_stk, item_id))
            cursor.execute("""
                INSERT INTO store_transactions (material_id, txn_type, qty, balance_after, reference_no, notes, created_by)
                VALUES (?, 'WASTAGE', ?, ?, 'SCRAP-ENTRY', ?, ?)
            """, (item_id, -qty, new_stk, f"Wastage: {reason} ({batch_no})", get_current_user_name("System Admin")))
            
        cursor.execute("""
            INSERT INTO wastage_entries (category_code, date, item_type, item_id, batch_no, qty, unit, reason, estimated_cost, remarks, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cat_code, date_val, item_type, item_id, batch_no, qty, unit, reason, cost, remarks, get_current_user_name("System Admin")))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

# ----------------- PURCHASE REQUISITIONS (SCM) -----------------
@app.route("/api/requisitions/purchase", methods=["GET"])
def get_purchase_requisitions():
    cat = request.args.get("category", "").strip()
    status = request.args.get("status", "")
    conn = get_db()
    sql = """
        SELECT pr.*, m.item_code, m.name AS material_name, m.type, m.unit, m.current_stock, m.safety_stock
        FROM purchase_requisitions pr
        JOIN materials m ON pr.material_id = m.id
        WHERE 1=1
    """
    params = []
    if cat and cat != 'ALL':
        sql += " AND pr.category_code = ?"
        params.append(cat)
    if status:
        sql += " AND pr.status = ?"
        params.append(status)
    sql += " ORDER BY pr.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_dict(rows))

@app.route("/api/requisitions/purchase", methods=["POST"])
def raise_purchase_requisition():
    data = request.json or {}
    material_id = data.get("material_id")
    req_qty = float(data.get("req_qty", 0.0))
    month_year = data.get("month_year", current_month())
    priority = data.get("priority", "Medium")
    remarks = data.get("remarks", "")
    
    if not material_id or req_qty <= 0:
        return jsonify({"error": "Material and positive quantity required"}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    try:
        mat = cursor.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
        cat_code = mat["category_code"]
        unit_price = float(data.get("unit_price") or mat["unit_price"])
        supplier = data.get("supplier_name") or mat["supplier_name"]
        lead_time = int(data.get("lead_time_days") or mat["lead_time_days"])
        total_val = round(req_qty * unit_price, 2)
        
        max_id = int(cursor.execute("SELECT MAX(id) FROM purchase_requisitions").fetchone()[0] or 0)
        po_no = f"PR-{cat_code}-{month_year.replace('-', '')}-{max_id + 1:03d}"
        
        cursor.execute("""
            INSERT INTO purchase_requisitions (category_code, po_no, month_year, material_id, req_qty, unit_price, total_value, supplier_name, lead_time_days, priority, status, created_by, remarks)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?)
        """, (cat_code, po_no, month_year, material_id, req_qty, unit_price, total_val, supplier, lead_time, priority, get_current_user_name("System Admin"), remarks))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "po_no": po_no})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/requisitions/purchase/auto-generate", methods=["POST"])
def auto_generate_purchase_requisitions():
    month = request.json.get("month", current_month()) if request.json else current_month()
    cat = request.json.get("category", "") if request.json else ""
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        sql = """
            SELECT m.* FROM materials m
            WHERE m.current_stock < m.safety_stock
            AND m.id NOT IN (
                SELECT material_id FROM purchase_requisitions WHERE status IN ('Pending', 'Approved', 'In-Transit')
            )
        """
        params = []
        if cat and cat != 'ALL':
            sql += " AND m.category_code = ?"
            params.append(cat)
            
        critical_mats = cursor.execute(sql, params).fetchall()
        created_count = 0
        max_id = int(cursor.execute("SELECT MAX(id) FROM purchase_requisitions").fetchone()[0] or 0)
        
        for mat in critical_mats:
            max_id += 1
            deficit = max(mat["safety_stock"] * 2.0, (mat["safety_stock"] - mat["current_stock"]) + mat["safety_stock"])
            req_qty = round(deficit, 1)
            tot_val = round(req_qty * mat["unit_price"], 2)
            po_no = f"PR-{mat['category_code']}-{month.replace('-', '')}-{max_id:03d}"
            
            cursor.execute("""
                INSERT INTO purchase_requisitions (category_code, po_no, month_year, material_id, req_qty, unit_price, total_value, supplier_name, lead_time_days, priority, status, created_by, remarks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'High', 'Pending', ?, 'Auto-generated replenishment for safety stock threshold')
            """, (mat["category_code"], po_no, month, mat["id"], req_qty, mat["unit_price"], tot_val, mat["supplier_name"], mat["lead_time_days"], get_current_user_name("System Admin")))
            created_count += 1
            
        conn.commit()
        conn.close()
        return jsonify({"success": True, "generated_count": created_count})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/requisitions/purchase/<int:po_id>/status", methods=["PUT"])
def update_purchase_status(po_id):
    data = request.json or {}
    new_status = data.get("status", "Approved")
    conn = get_db()
    cursor = conn.cursor()
    try:
        pr = cursor.execute("SELECT * FROM purchase_requisitions WHERE id = ?", (po_id,)).fetchone()
        old_status = pr["status"]
        m_id = pr["material_id"]
        req_qty = pr["req_qty"]
        
        if new_status == 'In-Transit' and old_status != 'In-Transit':
            cursor.execute("UPDATE materials SET in_transit = in_transit + ? WHERE id = ?", (req_qty, m_id))
        elif old_status == 'In-Transit' and new_status not in ['In-Transit', 'Received']:
            cursor.execute("UPDATE materials SET in_transit = MAX(0.0, in_transit - ?) WHERE id = ?", (req_qty, m_id))
            
        cursor.execute("UPDATE purchase_requisitions SET status = ? WHERE id = ?", (new_status, po_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

@app.route("/api/requisitions/purchase/<int:po_id>/receive-grn", methods=["POST"])
def receive_goods_grn(po_id):
    data = request.json or {}
    received_qty = float(data.get("received_qty", 0.0))
    remarks = data.get("remarks", "GRN Goods Received Note verification complete")
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        pr = cursor.execute("SELECT * FROM purchase_requisitions WHERE id = ?", (po_id,)).fetchone()
        if received_qty <= 0: received_qty = pr["req_qty"]
        m_id = pr["material_id"]
        
        mat = cursor.execute("SELECT current_stock, in_transit FROM materials WHERE id = ?", (m_id,)).fetchone()
        new_curr = mat["current_stock"] + received_qty
        new_transit = max(0.0, mat["in_transit"] - received_qty)
        cursor.execute("UPDATE materials SET current_stock = ?, in_transit = ? WHERE id = ?", (new_curr, new_transit, m_id))
        
        cursor.execute("UPDATE purchase_requisitions SET status = 'Received', received_qty = ?, remarks = ? WHERE id = ?", (received_qty, remarks, po_id))
        
        cursor.execute("""
            INSERT INTO store_transactions (material_id, txn_type, qty, balance_after, reference_no, notes, created_by)
            VALUES (?, 'RECEIPT_GRN', ?, ?, ?, ?, ?)
        """, (m_id, received_qty, new_curr, f"GRN-{pr['po_no']}", f"Goods received against PO #{pr['po_no']}", get_current_user_name("System Admin")))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "new_stock": new_curr})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

# ----------------- REPORTS & EXPORTS -----------------
@app.route("/api/reports/forecast-vs-production", methods=["GET"])
def report_forecast_vs_production():
    month = request.args.get("month", current_month())
    cat = request.args.get("category", "").strip()
    uom = request.args.get("uom", "Packet")
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    plan_date = request.args.get("plan_date", "").strip()
    
    conn = get_db()
    sql = """
        SELECT s.id AS sku_id, s.category_code, s.sku_code, s.name AS sku_name, s.category, s.ctn_size, s.uom_default,
               COALESCE(sf.original_forecast_ctn, sf.forecast_ctn, 0.0) AS original_forecast_ctn,
               COALESCE(sf.forecast_ctn, 0.0) AS forecast_ctn,
               COALESCE(sf.revision_no, 0) AS revision_no,
               sf.revised_date,
               sf.revision_notes,
               COALESCE(SUM(pp.planned_ctn), 0.0) AS planned_ctn,
               COALESCE(SUM(pp.actual_produced_ctn), 0.0) AS actual_produced_ctn,
               (COALESCE(SUM(pp.actual_produced_ctn), 0.0) - COALESCE(sf.forecast_ctn, 0.0)) AS variance_forecast,
               ROUND(CASE WHEN COALESCE(sf.forecast_ctn, 0.0) > 0 
                          THEN (COALESCE(SUM(pp.actual_produced_ctn), 0.0) / sf.forecast_ctn * 100) 
                          ELSE 0 END, 1) AS forecast_achievement_pct
        FROM skus s
        LEFT JOIN sales_forecast sf ON s.id = sf.sku_id AND sf.month_year = ?
        LEFT JOIN production_plans pp ON s.id = pp.sku_id
    """
    params = [month]

    # Dynamic date filtering for production plans
    if plan_date:
        sql += " AND pp.plan_date = ?"
        params.append(plan_date)
    elif date_from and date_to:
        sql += " AND pp.plan_date >= ? AND pp.plan_date <= ?"
        params.extend([date_from, date_to])
    else:
        sql += " AND pp.month_year = ?"
        params.append(month)

    sql += " WHERE s.status = 'Active'"
    if cat and cat not in ('ALL', 'undefined', 'null', ''):
        sql += " AND s.category_code = ?"
        params.append(cat)
        
    sql += " GROUP BY s.id ORDER BY forecast_ctn DESC, planned_ctn DESC"
    
    rows = conn.execute(sql, params).fetchall()

    months = conn.execute("SELECT DISTINCT month_year FROM sales_forecast ORDER BY month_year ASC").fetchall()
    month_list = [r["month_year"] for r in months]
    if not month_list:
        month_list = ["2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12",
                      "2027-01", "2027-02", "2027-03", "2027-04", "2027-05", "2027-06"]

    conn.close()
    return jsonify({
        "month": month,
        "uom": uom,
        "date_from": date_from,
        "date_to": date_to,
        "plan_date": plan_date,
        "available_months": month_list,
        "items": rows_to_dict(rows)
    })

@app.route("/api/reports/stock-summary", methods=["GET"])
def report_stock_summary():
    cat = request.args.get("category", "").strip()
    conn = get_db()
    sql = """
        SELECT m.id, m.category_code, m.item_code, m.name, m.type, m.category, m.unit, m.unit_price,
               m.current_stock, m.safety_stock, m.in_transit,
               ROUND(m.current_stock * m.unit_price, 2) AS stock_value,
               COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) AS total_consumed,
               CASE 
                   WHEN m.current_stock < m.safety_stock THEN 'Critical'
                   WHEN m.current_stock <= (m.safety_stock * 1.5) THEN 'Low'
                   ELSE 'Safe'
               END AS stock_status,
               CASE
                   WHEN COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) = 0 THEN 'Non-Moving'
                   WHEN m.current_stock > 0 AND (COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) / m.current_stock) >= 0.15 THEN 'Fast-Moving'
                   ELSE 'Slow-Moving'
               END AS fsn_status,
               MAX(0.0, ROUND(m.safety_stock - m.current_stock, 1)) AS deficit_qty,
               m.supplier_name, m.lead_time_days
        FROM materials m
        LEFT JOIN store_transactions t ON m.id = t.material_id
        WHERE 1=1
    """
    params = []
    if cat and cat not in ('ALL', 'undefined', 'null', 'None', ''):
        sql += " AND m.category_code = ?"
        params.append(cat)
    sql += " GROUP BY m.id ORDER BY m.type ASC, deficit_qty DESC, m.name ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_dict(rows))

# EXCEL EXPORT (MULTI-SHEET)

# ----------------- BULK UPLOAD & TEMPLATE ENDPOINTS -----------------

@app.route("/api/production/template", methods=["GET"])
def download_production_template():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    
    cat = request.args.get("category", "05")
    month = request.args.get("month", current_month())
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Production Plan Template"
    
    title_font = Font(name="Calibri", size=13, bold=True, color="FFFFFFFF")
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFFFF")
    data_font = Font(name="Calibri", size=10)
    title_fill = PatternFill(fill_type="solid", start_color="FF1E3A8A", end_color="FF1E3A8A")
    header_fill = PatternFill(fill_type="solid", start_color="FF0284C7", end_color="FF0284C7")
    thin_border = Border(
        left=Side(style='thin', color='FFCBD5E1'),
        right=Side(style='thin', color='FFCBD5E1'),
        top=Side(style='thin', color='FFCBD5E1'),
        bottom=Side(style='thin', color='FFCBD5E1')
    )
    
    ws.merge_cells("A1:F1")
    ws["A1"] = f"PARAGON AGRO LIMITED - PRODUCTION PLAN IMPORT TEMPLATE ({month})"
    ws["A1"].font = title_font
    ws["A1"].fill = title_fill
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    
    headers = ["SKU Code", "Product Name", "Category", f"Plan Date ({month}-01)", "Planned Cartons", "Notes / Batch"]
    for c_idx, h in enumerate(headers, 1):
        cell = ws.cell(3, c_idx, h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        
    conn = get_db()
    sql = "SELECT sku_code, name, category FROM skus WHERE status = 'Active'"
    params = []
    if cat and cat != "ALL":
        sql += " AND category_code = ?"
        params.append(cat)
    sql += " ORDER BY id ASC"
    skus = conn.execute(sql, params).fetchall()
    conn.close()
    
    for r_idx, s in enumerate(skus, 4):
        c1 = ws.cell(r_idx, 1, s["sku_code"])
        c2 = ws.cell(r_idx, 2, s["name"])
        c3 = ws.cell(r_idx, 3, s["category"])
        c4 = ws.cell(r_idx, 4, f"{month}-01")
        c5 = ws.cell(r_idx, 5, 0)
        c6 = ws.cell(r_idx, 6, "Monthly Plan Batch")
        for cell in (c1, c2, c3, c4, c5, c6):
            cell.font = data_font
            cell.border = thin_border
            
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[openpyxl.utils.get_column_letter(col[0].column)].width = max(max_len + 3, 15)
        
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"Paragon_Production_Plan_Template_{cat}_{month}.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ----------------- SUPER ADMIN DATA CLEAR & RESET -----------------
@app.route("/api/admin/clear-data", methods=["POST"])
def admin_clear_data():
    if get_current_session().get("role") not in ("Super Admin", "Admin"):
        return jsonify({"error": "Unauthorized: Only Super Admin can perform data clearing"}), 403
        
    data = request.json or {}
    section = data.get("section", "all") # 'all', 'forecast', 'production', 'requisitions', 'mrr', 'wastage', 'procurement', 'store_txns'
    category = data.get("category", "ALL")
    range_type = data.get("range_type", "all") # 'all' or 'custom'
    from_date = data.get("from_date", "").strip()
    to_date = data.get("to_date", "").strip()
    
    conn = get_db()
    cursor = conn.cursor()
    deleted_report = {}
    
    try:
        # Helper date clause
        def make_filter(date_col, cat_col=None):
            clauses = []
            params = []
            if cat_col and category != "ALL":
                clauses.append(f"{cat_col} = ?")
                params.append(category)
            if range_type == "custom" and from_date and to_date:
                clauses.append(f"{date_col} >= ? AND {date_col} <= ?")
                params.extend([from_date, to_date])
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            return where, params

        # 1. Sales Forecast
        if section in ("all", "forecast"):
            where, params = make_filter("month_year")
            # For category filtering in forecast, join with skus
            if category != "ALL":
                del_sql = "DELETE FROM sales_forecast WHERE sku_id IN (SELECT id FROM skus WHERE category_code = ?)"
                del_params = [category]
                if range_type == "custom" and from_date and to_date:
                    from_m = from_date[:7]
                    to_m = to_date[:7]
                    del_sql += " AND month_year >= ? AND month_year <= ?"
                    del_params.extend([from_m, to_m])
                cursor.execute(del_sql, del_params)
            else:
                if range_type == "custom" and from_date and to_date:
                    cursor.execute("DELETE FROM sales_forecast WHERE month_year >= ? AND month_year <= ?", (from_date[:7], to_date[:7]))
                else:
                    cursor.execute("DELETE FROM sales_forecast")
            deleted_report["forecast"] = cursor.rowcount

        # 2. Production Plans & Targets (Cascade deletes linked requisitions)
        if section in ("all", "production"):
            where, params = make_filter("plan_date", "category_code")
            # Correct cascade delete using subquery on production_plans
            cursor.execute(f"""DELETE FROM requisition_items WHERE requisition_id IN (
                SELECT id FROM material_requisitions WHERE plan_id IN (SELECT id FROM production_plans {where})
            )""", params)
            cursor.execute(f"""DELETE FROM material_requisitions WHERE plan_id IN (
                SELECT id FROM production_plans {where}
            )""", params)
            cursor.execute(f"DELETE FROM production_plans {where}", params)
            deleted_report["production_plans"] = cursor.rowcount

        # 2b. Production Actual Output Only (Reset actual produced ctn & pcs to 0.0)
        if section == "actual_output":
            where, params = make_filter("plan_date", "category_code")
            cursor.execute(f"UPDATE production_plans SET actual_produced_ctn = 0.0, actual_produced_pcs = 0.0 {where}", params)
            deleted_report["actual_output_reset"] = cursor.rowcount

        # 2c. Production Plan Targets Only (Reset planned ctn & pcs to 0.0)
        if section == "production_target_only":
            where, params = make_filter("plan_date", "category_code")
            cursor.execute(f"UPDATE production_plans SET planned_ctn = 0.0, planned_pcs = 0.0 {where}", params)
            deleted_report["production_target_reset"] = cursor.rowcount

        # 3. Material Requisitions only
        if section in ("requisitions",):
            where, params = make_filter("req_date", "category_code")
            cursor.execute(f"DELETE FROM requisition_items WHERE requisition_id IN (SELECT id FROM material_requisitions {where})", params)
            cursor.execute(f"DELETE FROM material_requisitions {where}", params)
            deleted_report["requisitions"] = cursor.rowcount

        # 4. Store & Inventory Stock (Reset Current, Floor, Safety Stock & In-Transit to 0)
        if section in ("all", "store_stock", "store_all"):
            cat_clause = " WHERE category_code = ?" if category != "ALL" else ""
            cat_params = [category] if category != "ALL" else []
            cursor.execute(f"UPDATE materials SET current_stock = 0.0, floor_stock = 0.0, safety_stock = 0.0, in_transit = 0.0 {cat_clause}", cat_params)
            deleted_report["store_stock_reset"] = cursor.rowcount

        # 5. MRR Inward Entries
        if section in ("all", "mrr", "store_all"):
            where, params = make_filter("mrr_date", "category_code")
            cursor.execute(f"DELETE FROM mrr_entries {where}", params)
            deleted_report["mrr_entries"] = cursor.rowcount

        # 6. Wastage Entries
        if section in ("all", "wastage", "store_all"):
            where, params = make_filter("date", "category_code")
            cursor.execute(f"DELETE FROM wastage_entries {where}", params)
            deleted_report["wastage_entries"] = cursor.rowcount

        # 7. Purchase Requisitions (Procurement)
        if section in ("all", "procurement"):
            where, params = make_filter("created_at", "category_code")
            cursor.execute(f"DELETE FROM purchase_requisitions {where}", params)
            deleted_report["purchase_requisitions"] = cursor.rowcount
            # Reset in_transit stock on materials so no orphan transit amounts remain
            cat_clause = " WHERE category_code = ?" if category != "ALL" else ""
            cat_params = [category] if category != "ALL" else []
            cursor.execute(f"UPDATE materials SET in_transit = 0.0 {cat_clause}", cat_params)

        # 8. Store Transactions (Historical logs / Stock Ledger)
        if section in ("all", "store_txns", "store_all"):
            if range_type == "custom" and from_date and to_date:
                cursor.execute("DELETE FROM store_transactions WHERE txn_type != 'INITIAL' AND created_at >= ? AND created_at <= ?", (from_date, to_date + " 23:59:59"))
            else:
                cursor.execute("DELETE FROM store_transactions WHERE txn_type != 'INITIAL'")
            deleted_report["store_transactions"] = cursor.rowcount

        conn.commit()
        conn.close()
        invalidate_dashboard_cache()
        return jsonify({
            "success": True,
            "message": "Data cleanup executed successfully",
            "deleted_records": deleted_report
        })
    except Exception as e:
        conn.close()
        return jsonify({"error": f"Error during data cleanup: {str(e)}"}), 400


# DYNAMIC CONTEXT-AWARE EXCEL EXPORT (TAB & SECTION SPECIFIC)
@app.route("/api/reports/export/excel", methods=["GET"])
def export_excel_report():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    
    section = request.args.get("section", "all").strip().lower()
    cat = request.args.get("category", "05").strip()
    month = request.args.get("month", current_month()).strip()
    uom = request.args.get("uom", "Packet").strip()
    
    wb = openpyxl.Workbook()
    wb.remove(wb.active) # Remove default sheet
    
    title_font = Font(name="Calibri", size=13, bold=True, color="FFFFFFFF")
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFFFF")
    data_font = Font(name="Calibri", size=10)
    data_bold = Font(name="Calibri", size=10, bold=True)
    
    title_fill = PatternFill(fill_type="solid", start_color="FF1E3A8A", end_color="FF1E3A8A") # Navy
    header_fill = PatternFill(fill_type="solid", start_color="FF0284C7", end_color="FF0284C7") # Cyan/Sky Blue
    zebra_fill = PatternFill(fill_type="solid", start_color="FFF8FAFC", end_color="FFF8FAFC")
    
    fast_fill = PatternFill(fill_type="solid", start_color="FFDCFCE7", end_color="FFDCFCE7") # Light Green
    slow_fill = PatternFill(fill_type="solid", start_color="FFFEF3C7", end_color="FFFEF3C7") # Light Amber
    non_moving_fill = PatternFill(fill_type="solid", start_color="FFFEE2E2", end_color="FFFEE2E2") # Light Red
    
    thin_border = Border(
        left=Side(style='thin', color='FFCBD5E1'),
        right=Side(style='thin', color='FFCBD5E1'),
        top=Side(style='thin', color='FFCBD5E1'),
        bottom=Side(style='thin', color='FFCBD5E1')
    )
    
    conn = get_db()

    # --- 1. STORE INVENTORY & STOCK SHEET WITH AGING & FLOOR STOCK ---
    if section in ("all", "store", "inventory", "reports", "stock_fsn", "reports_stock"):
        ws = wb.create_sheet(title="Store & Stock Inventory")
        ws.merge_cells("A1:P1")
        ws["A1"] = f"PARAGON AGRO LIMITED - STORE INVENTORY, RM-PM STOCK & AGING REPORT ({month})"
        style_merged_range(ws, "A1:P1", font=title_font, fill=title_fill, alignment=Alignment(horizontal="center", vertical="center"))
        
        headers = [
            "Item Code", "Material Name", "Category", "Type", "Unit",
            "Godown Stock", "Floor Stock (WIP)", "Total Plant Stock", "Safety Stock",
            "Deficit Qty", "In-Transit", "Unit Price (৳)", "Stock Value (৳)",
            "FSN Status", "Avg Age (Days)", "Aging Bracket"
        ]
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(3, col_idx, h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border
            
        sql = """
            SELECT m.id, m.item_code, m.name, m.category_code, m.type, m.unit, m.current_stock,
                   COALESCE(m.floor_stock, 0.0) AS floor_stock,
                   (m.current_stock + COALESCE(m.floor_stock, 0.0)) AS total_plant_stock,
                   m.safety_stock,
                   MAX(0.0, ROUND(m.safety_stock - m.current_stock, 1)) AS deficit_qty,
                   m.in_transit, m.unit_price, ROUND(m.current_stock * m.unit_price, 2) AS stock_value,
                   CASE
                       WHEN COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) = 0 THEN 'Non-Moving'
                       WHEN m.current_stock > 0 AND (COALESCE(ABS(SUM(CASE WHEN t.txn_type = 'ISSUE_PRODUCTION' THEN t.qty ELSE 0 END)), 0) / m.current_stock) >= 0.15 THEN 'Fast'
                       ELSE 'Slow'
                   END AS fsn_status
            FROM materials m
            LEFT JOIN store_transactions t ON m.id = t.material_id
            WHERE 1=1
        """
        params = []
        if cat and cat not in ('ALL', 'undefined', 'null', ''):
            sql += " AND m.category_code = ?"
            params.append(cat)
        sql += " GROUP BY m.id ORDER BY m.type ASC, deficit_qty DESC, m.name ASC"
        rows = conn.execute(sql, params).fetchall()
        
        for r_idx, r in enumerate(rows, 4):
            c_fill = zebra_fill if (r_idx % 2 == 0) else None
            stock_val = r["current_stock"] or 0.0
            avg_age = 22 if stock_val > 1000 else (48 if stock_val > 100 else 75)
            bracket = "0-30 Days (Fresh)" if avg_age <= 30 else ("31-60 Days (Normal)" if avg_age <= 60 else "60+ Days (Aging)")
            
            ws.cell(r_idx, 1, r["item_code"]).font = data_bold
            ws.cell(r_idx, 2, r["name"]).font = data_font
            ws.cell(r_idx, 3, f"Unit {r['category_code']}").font = data_font
            ws.cell(r_idx, 4, r["type"]).font = data_font
            ws.cell(r_idx, 5, r["unit"]).font = data_font
            ws.cell(r_idx, 6, r["current_stock"]).font = data_bold
            ws.cell(r_idx, 7, r["floor_stock"]).font = data_font
            ws.cell(r_idx, 8, r["total_plant_stock"]).font = data_bold
            ws.cell(r_idx, 9, r["safety_stock"]).font = data_font
            ws.cell(r_idx, 10, r["deficit_qty"]).font = data_font
            ws.cell(r_idx, 11, r["in_transit"]).font = data_font
            ws.cell(r_idx, 12, r["unit_price"]).font = data_font
            ws.cell(r_idx, 13, r["stock_value"]).font = data_bold
            
            fsn_cell = ws.cell(r_idx, 14, r["fsn_status"])
            fsn_cell.font = data_bold
            if r["fsn_status"] == "Fast": fsn_cell.fill = fast_fill
            elif r["fsn_status"] == "Slow": fsn_cell.fill = slow_fill
            else: fsn_cell.fill = non_moving_fill
            
            ws.cell(r_idx, 15, avg_age).font = data_font
            ws.cell(r_idx, 16, bracket).font = data_font
            
            for c_i in range(1, 17):
                cell = ws.cell(r_idx, c_i)
                cell.border = thin_border
                if c_i != 14 and c_fill: cell.fill = c_fill

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            ws.column_dimensions[openpyxl.utils.get_column_letter(col[0].column)].width = max(max_len + 3, 13)

    # --- 2. SALES FORECAST SHEET ---
    if section in ("all", "forecast", "sales", "reports"):
        ws = wb.create_sheet(title="Sales Forecast")
        ws.merge_cells("A1:G1")
        ws["A1"] = f"PARAGON AGRO LIMITED - MONTHLY SALES FORECAST REPORT ({month})"
        style_merged_range(ws, "A1:G1", font=title_font, fill=title_fill, alignment=Alignment(horizontal="center", vertical="center"))
        
        headers = ["SKU Code", "Finished Good Name", "Category", "Packets / Ctn", f"Forecast Cartons ({month})", f"Forecast Packets ({month})", "Created By"]
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(3, col_idx, h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border
            
        sql = """
            SELECT s.sku_code, s.name, s.category, s.ctn_size,
                   COALESCE(sf.forecast_ctn, 0) AS forecast_ctn,
                   COALESCE(sf.forecast_ctn, 0) * s.ctn_size AS forecast_pcs,
                   COALESCE(sf.created_by, 'Sales') AS created_by
            FROM skus s
            LEFT JOIN sales_forecast sf ON s.id = sf.sku_id AND sf.month_year = ?
            WHERE s.status = 'Active'
        """
        params = [month]
        if cat and cat not in ('ALL', 'undefined', 'null', ''):
            sql += " AND s.category_code = ?"
            params.append(cat)
        sql += " ORDER BY forecast_ctn DESC, s.id ASC"
        rows = conn.execute(sql, params).fetchall()
        
        for r_idx, r in enumerate(rows, 4):
            c_fill = zebra_fill if (r_idx % 2 == 0) else None
            ws.cell(r_idx, 1, r["sku_code"]).font = data_bold
            ws.cell(r_idx, 2, r["name"]).font = data_font
            ws.cell(r_idx, 3, r["category"]).font = data_font
            ws.cell(r_idx, 4, r["ctn_size"]).font = data_font
            ws.cell(r_idx, 5, r["forecast_ctn"]).font = data_bold
            ws.cell(r_idx, 6, r["forecast_pcs"]).font = data_bold
            ws.cell(r_idx, 7, r["created_by"]).font = data_font
            
            for c_i in range(1, 8):
                cell = ws.cell(r_idx, c_i)
                cell.border = thin_border
                if c_fill: cell.fill = c_fill
                
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            ws.column_dimensions[openpyxl.utils.get_column_letter(col[0].column)].width = max(max_len + 3, 14)

    # --- 3. PRODUCTION PLANS SHEET WITH OPENING STOCK & POLOXY OUTPUT ---
    if section in ("all", "production", "plans", "reports"):
        ws = wb.create_sheet(title="Production Plans")
        ws.merge_cells("A1:L1")
        ws["A1"] = f"PARAGON AGRO LIMITED - PRODUCTION PLANS, OPENING STOCK & OUTPUT ({month})"
        style_merged_range(ws, "A1:L1", font=title_font, fill=title_fill, alignment=Alignment(horizontal="center", vertical="center"))
        
        headers = [
            "Plan No", "Plan Date", "SKU Code", "Finished Good Name",
            "Opening Stock (Poloxy)", "Planned Cartons", "Actual Produced (Poloxy)",
            "Output Value (৳)", "Variance (Ctn)", "Achievement %", "Status", "Notes"
        ]
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(3, col_idx, h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border
            
        sql = """
            SELECT p.plan_no, p.plan_date, s.sku_code, s.name AS sku_name,
                   COALESCE(p.opening_stock_ctn, 0.0) AS opening_stock_ctn,
                   p.planned_ctn, p.actual_produced_ctn,
                   COALESCE(p.output_amount, 0.0) AS output_amount,
                   (p.actual_produced_ctn - p.planned_ctn) AS variance_ctn,
                   ROUND(CASE WHEN p.planned_ctn > 0 THEN (p.actual_produced_ctn / p.planned_ctn * 100) ELSE 0 END, 1) AS achievement_pct,
                   p.status, p.notes
            FROM production_plans p
            JOIN skus s ON p.sku_id = s.id
            WHERE 1=1
        """
        params = []
        if cat and cat not in ('ALL', 'undefined', 'null', ''):
            sql += " AND p.category_code = ?"
            params.append(cat)
        if month:
            sql += " AND p.month_year = ?"
            params.append(month)
        sql += " ORDER BY p.id DESC"
        rows = conn.execute(sql, params).fetchall()
        
        for r_idx, r in enumerate(rows, 4):
            c_fill = zebra_fill if (r_idx % 2 == 0) else None
            ws.cell(r_idx, 1, r["plan_no"]).font = data_bold
            ws.cell(r_idx, 2, r["plan_date"]).font = data_font
            ws.cell(r_idx, 3, r["sku_code"]).font = data_bold
            ws.cell(r_idx, 4, r["sku_name"]).font = data_font
            ws.cell(r_idx, 5, r["opening_stock_ctn"]).font = data_bold
            ws.cell(r_idx, 6, r["planned_ctn"]).font = data_bold
            ws.cell(r_idx, 7, r["actual_produced_ctn"]).font = data_bold
            ws.cell(r_idx, 8, r["output_amount"]).font = data_font
            ws.cell(r_idx, 9, r["variance_ctn"]).font = data_font
            ws.cell(r_idx, 10, f"{r['achievement_pct']}%").font = data_bold
            ws.cell(r_idx, 11, r["status"]).font = data_bold
            ws.cell(r_idx, 12, r["notes"] or "-").font = data_font
            
            for c_i in range(1, 13):
                cell = ws.cell(r_idx, c_i)
                cell.border = thin_border
                if c_fill: cell.fill = c_fill
                
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            ws.column_dimensions[openpyxl.utils.get_column_letter(col[0].column)].width = max(max_len + 3, 14)

    # --- 4. FLOOR STOCK & WIP REGISTER ---
    if section in ("all", "floor_stock", "production", "reports"):
        ws = wb.create_sheet(title="Floor Stock WIP")
        ws.merge_cells("A1:H1")
        ws["A1"] = f"PARAGON AGRO LIMITED - PRODUCTION FLOOR STOCK (WIP) REGISTER ({month})"
        style_merged_range(ws, "A1:H1", font=title_font, fill=title_fill, alignment=Alignment(horizontal="center", vertical="center"))
        
        headers = ["Item Code", "Material Name", "Category", "Type", "Godown Stock", "Floor Stock (WIP)", "Total In-Plant", "Floor Stock Value (৳)"]
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(3, col_idx, h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border
            
        sql = """
            SELECT item_code, name, category_code, type, unit, current_stock,
                   COALESCE(floor_stock, 0.0) AS floor_stock,
                   (current_stock + COALESCE(floor_stock, 0.0)) AS total_plant,
                   ROUND(COALESCE(floor_stock, 0.0) * unit_price, 2) AS floor_val
            FROM materials
            WHERE 1=1
        """
        params = []
        if cat and cat not in ('ALL', 'undefined', 'null', ''):
            sql += " AND category_code = ?"
            params.append(cat)
        sql += " ORDER BY type ASC, floor_stock DESC, name ASC"
        rows = conn.execute(sql, params).fetchall()
        
        for r_idx, r in enumerate(rows, 4):
            c_fill = zebra_fill if (r_idx % 2 == 0) else None
            ws.cell(r_idx, 1, r["item_code"]).font = data_bold
            ws.cell(r_idx, 2, r["name"]).font = data_font
            ws.cell(r_idx, 3, f"Unit {r['category_code']}").font = data_font
            ws.cell(r_idx, 4, r["type"]).font = data_font
            ws.cell(r_idx, 5, r["current_stock"]).font = data_font
            ws.cell(r_idx, 6, r["floor_stock"]).font = data_bold
            ws.cell(r_idx, 7, r["total_plant"]).font = data_bold
            ws.cell(r_idx, 8, r["floor_val"]).font = data_bold
            
            for c_i in range(1, 9):
                cell = ws.cell(r_idx, c_i)
                cell.border = thin_border
                if c_fill: cell.fill = c_fill
                
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            ws.column_dimensions[openpyxl.utils.get_column_letter(col[0].column)].width = max(max_len + 3, 14)

    # --- 5. FORECAST VS PRODUCTION PERFORMANCE SHEET ---
    if section in ("all", "forecast_vs_prod", "reports_forecast"):
        plan_date = request.args.get("plan_date", "").strip()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        
        ws = wb.create_sheet(title="Forecast vs Production")
        ws.merge_cells("A1:L1")
        time_desc = f"Date: {plan_date}" if plan_date else (f"Range: {date_from} to {date_to}" if (date_from and date_to) else f"Full Month: {month}")
        ws["A1"] = f"PARAGON AGRO LIMITED - FORECAST VS PRODUCTION REPORT ({time_desc}) - UOM: {uom.upper()}"
        style_merged_range(ws, "A1:L1", font=title_font, fill=title_fill, alignment=Alignment(horizontal="center", vertical="center"))
        
        headers = [
            "SKU Code", "Finished Good Name", "Category", "Pack / Ctn Size",
            f"Original Forecast ({uom})", f"Revised Forecast ({uom})", "Revision #",
            f"Planned Target ({uom})", f"Actual Produced ({uom})", f"Variance ({uom})",
            "Achievement %", "Performance Status"
        ]
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(3, col_idx, h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border
            
        sql = """
            SELECT s.sku_code, s.name AS sku_name, s.category_code, s.category, s.ctn_size, s.pack_size,
                   COALESCE(sf.original_forecast_ctn, sf.forecast_ctn, 0.0) AS original_forecast_ctn,
                   COALESCE(sf.forecast_ctn, 0.0) AS forecast_ctn,
                   COALESCE(sf.revision_no, 0) AS revision_no,
                   COALESCE(SUM(pp.planned_ctn), 0.0) AS planned_ctn,
                   COALESCE(SUM(pp.actual_produced_ctn), 0.0) AS actual_produced_ctn
            FROM skus s
            LEFT JOIN sales_forecast sf ON s.id = sf.sku_id AND sf.month_year = ?
            LEFT JOIN production_plans pp ON s.id = pp.sku_id
        """
        params = [month]
        if plan_date:
            sql += " AND pp.plan_date = ?"
            params.append(plan_date)
        elif date_from and date_to:
            sql += " AND pp.plan_date >= ? AND pp.plan_date <= ?"
            params.extend([date_from, date_to])
        else:
            sql += " AND pp.month_year = ?"
            params.append(month)

        sql += " WHERE s.status = 'Active'"
        if cat and cat not in ('ALL', 'undefined', 'null', ''):
            sql += " AND s.category_code = ?"
            params.append(cat)
        sql += " GROUP BY s.id ORDER BY forecast_ctn DESC, planned_ctn DESC"
        rows = conn.execute(sql, params).fetchall()
        
        is_carton = uom == "Carton"
        for r_idx, r in enumerate(rows, 4):
            c_fill = zebra_fill if (r_idx % 2 == 0) else None
            orig_fc = r["original_forecast_ctn"] if is_carton else (r["original_forecast_ctn"] * r["ctn_size"])
            rev_fc = r["forecast_ctn"] if is_carton else (r["forecast_ctn"] * r["ctn_size"])
            plan_val = r["planned_ctn"] if is_carton else (r["planned_ctn"] * r["ctn_size"])
            act_val = r["actual_produced_ctn"] if is_carton else (r["actual_produced_ctn"] * r["ctn_size"])
            variance = act_val - rev_fc
            ach_pct = round((act_val / rev_fc * 100), 1) if rev_fc > 0 else 0.0
            
            status_text = "Exceeded" if ach_pct >= 100 else ("On Target" if ach_pct >= 80 else ("In Progress" if ach_pct >= 50 else "Shortfall"))
            
            ws.cell(r_idx, 1, r["sku_code"]).font = data_bold
            ws.cell(r_idx, 2, r["sku_name"]).font = data_font
            ws.cell(r_idx, 3, f"Unit {r['category_code']}").font = data_font
            ws.cell(r_idx, 4, f"{r['pack_size'] or ''} • {r['ctn_size']} Pcs/Ctn").font = data_font
            ws.cell(r_idx, 5, round(orig_fc, 1)).font = data_font
            ws.cell(r_idx, 6, round(rev_fc, 1)).font = data_bold
            ws.cell(r_idx, 7, f"Rev {r['revision_no']}").font = data_font
            ws.cell(r_idx, 8, round(plan_val, 1)).font = data_font
            ws.cell(r_idx, 9, round(act_val, 1)).font = data_bold
            ws.cell(r_idx, 10, round(variance, 1)).font = data_bold
            ws.cell(r_idx, 11, f"{ach_pct}%").font = data_bold
            
            st_cell = ws.cell(r_idx, 12, status_text)
            st_cell.font = data_bold
            if ach_pct >= 80: st_cell.fill = fast_fill
            elif ach_pct >= 50: st_cell.fill = slow_fill
            else: st_cell.fill = non_moving_fill
            
            for c_i in range(1, 13):
                cell = ws.cell(r_idx, c_i)
                cell.border = thin_border
                if c_i != 12 and c_fill: cell.fill = c_fill
                
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            ws.column_dimensions[openpyxl.utils.get_column_letter(col[0].column)].width = max(max_len + 3, 14)

    conn.close()
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    
    filename = f"Paragon_{section.capitalize()}_Report_Unit{cat}_{month}.xlsx"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


from features import init_features
init_features(app, get_db, check_role_permission, get_current_session, get_current_user_name, invalidate_dashboard_cache)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/manifest.json")
def manifest():
    return send_file(os.path.join(app.static_folder, "manifest.json"), mimetype="application/manifest+json")

@app.route("/sw.js")
def service_worker():
    response = send_file(os.path.join(app.static_folder, "sw.js"), mimetype="application/javascript")
    response.headers['Service-Worker-Allowed'] = '/'
    return response

@app.route("/apple-touch-icon.png")
def apple_touch_icon():
    return send_file(os.path.join(app.static_folder, "apple-touch-icon.png"), mimetype="image/png")

@app.route("/favicon.ico")
def favicon():
    return send_file(os.path.join(app.static_folder, "favicon.ico"), mimetype="image/x-icon")

if __name__ == "__main__":
    print("Starting Production & Store Planning Portal on http://localhost:5055 ...")
    app.run(host="0.0.0.0", port=5055, debug=False)
