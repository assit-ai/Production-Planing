"""
Role-based "My Tasks" dashboard, bulk-upload preview (dry-run -> confirm),
Poloxy actual-output import, and admin backup.

Registered from app.py via init_features(...).
"""
import io
import csv
import json
import re
from datetime import datetime, timedelta

from flask import request, jsonify, Response


# ----------------------------------------------------------------- helpers
def bd_now():
    """Bangladesh time (UTC+6) regardless of server timezone."""
    return datetime.utcnow() + timedelta(hours=6)


def bd_today():
    return bd_now().strftime("%Y-%m-%d")


def bd_month():
    return bd_now().strftime("%Y-%m")


def next_month_str(month):
    y, m = int(month[:4]), int(month[5:7])
    m += 1
    if m > 12:
        y, m = y + 1, 1
    return f"{y:04d}-{m:02d}"


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y",
                 "%Y/%m/%d", "%d.%m.%Y", "%d-%b-%y", "%m/%d/%Y")


def norm_date(v, default=None):
    """Return YYYY-MM-DD or `default` (None if unparseable and no default)."""
    if v is None or str(v).strip() == "":
        return default
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    for cand in (s, s[:10], s.split(" ")[0]):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(cand, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def to_float(v):
    """float or None when not numeric. Accepts '1,250.5'."""
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[,\s]", "", str(v))
    try:
        return float(s)
    except ValueError:
        return None


def to_int(v):
    """int or None when not numeric."""
    f = to_float(v)
    return int(f) if f is not None else None


def read_table(file_storage):
    """Read first sheet of .xlsx (or a .csv) into a list of row lists."""
    name = (file_storage.filename or "").lower()
    if name.endswith(".xls"):
        raise ValueError("Legacy .xls format is not supported. Please save as .xlsx or .csv and try again.")
    if name.endswith(".csv"):
        text = file_storage.read().decode("utf-8-sig", errors="ignore")
        return [list(r) for r in csv.reader(io.StringIO(text))]
    import openpyxl
    wb = openpyxl.load_workbook(file_storage, data_only=True)
    ws = wb.active
    return [list(r) for r in ws.iter_rows(values_only=True)]


def cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def preview_response(rows, extra=None):
    s = {"ok": 0, "warn": 0, "error": 0, "skip": 0}
    for r in rows:
        s[r["status"]] = s.get(r["status"], 0) + 1
    s["total"] = len(rows)
    s["applicable"] = s["ok"] + s["warn"]
    body = {"success": True, "preview": True, "summary": s,
            "rows": rows[:400], "truncated": len(rows) > 400}
    if extra:
        body.update(extra)
    return jsonify(body)


def is_preview():
    return request.form.get("preview", "1") == "1"


def init_features(app, get_db, check_role_permission, get_current_session,
                  get_current_user_name, invalidate_dashboard_cache):

    # ============================================================ FORECAST
    @app.route("/api/forecast/bulk-upload", methods=["POST"])
    def bulk_upload_forecast():
        allowed, err_resp, err_code = check_role_permission(["Sales"], "bulk upload sales forecast")
        if not allowed:
            return err_resp, err_code
        if "file" not in request.files:
            return jsonify({"error": "No file selected."}), 400
        month = request.form.get("month") or bd_month()
        preview = is_preview()
        try:
            table = read_table(request.files["file"])
        except Exception as e:
            return jsonify({"error": f"Failed to read file: {e}"}), 400

        sku_col = fc_col = None
        name_col = cat_col = ctn_col = None
        header_row = 3
        for r in range(0, min(6, len(table))):
            for c, v in enumerate(table[r][:15]):
                val = str(v or "").strip().lower()
                if "sku code" in val or "item code" in val or val == "sku":
                    sku_col, header_row = c, r + 1
                elif "forecast" in val:
                    fc_col = c
                elif any(k in val for k in ["product", "finished good", "sku name", "item name", "description"]):
                    name_col = c
                elif "category" in val or "unit" in val:
                    cat_col = c
                elif any(k in val for k in ["packet", "carton", "ctn_size", "pack size", "conversion"]):
                    ctn_col = c
        if sku_col is None or fc_col is None:
            sku_col, name_col, cat_col, ctn_col, fc_col, header_row = 1, 2, 3, 4, 5, 3

        conn = get_db()
        try:
            req_cat = str(request.form.get("category_code") or request.form.get("category") or "05").strip()

            all_skus = conn.execute("SELECT id, sku_code, name, category_code, category, ctn_size FROM skus").fetchall()
            skus_by_code = {str(s["sku_code"]).strip().upper(): s for s in all_skus}
            def norm_txt(s):
                return re.sub(r'[^a-z0-9]', '', str(s).lower()) if s else ''
            skus_by_name = {norm_txt(s["name"]): s for s in all_skus if s["name"]}

            existing = {int(r["sku_id"]): r for r in conn.execute(
                "SELECT sku_id, forecast_ctn, original_forecast_ctn, revision_no FROM sales_forecast WHERE month_year = ?",
                (month,)).fetchall()}

            report, seen = [], set()
            for idx in range(header_row, len(table)):
                row = table[idx]
                code_val = cell(row, sku_col)
                if code_val in (None, ""):
                    continue
                code = str(code_val).strip()
                name_val = cell(row, name_col) if name_col is not None else ""
                cat_val = cell(row, cat_col) if cat_col is not None else ""
                ctn_val = cell(row, ctn_col) if ctn_col is not None else ""

                rn = idx + 1
                item = {"row": rn, "code": code, "name": "", "old": None, "new": None,
                        "status": "ok", "message": ""}

                # Match by SKU code, then by product name
                sku = skus_by_code.get(code.upper())
                if not sku and name_val:
                    sku = skus_by_name.get(norm_txt(name_val))
                if not sku:
                    sku = skus_by_name.get(norm_txt(code))

                fc = to_float(cell(row, fc_col))

                if code.upper() in seen:
                    item.update(name=sku["name"] if sku else (name_val or code), status="skip", message="Duplicate row in file - using first occurrence")
                elif fc is None:
                    item.update(name=sku["name"] if sku else (name_val or code), status="error", message="Invalid forecast quantity")
                elif fc < 0:
                    item.update(name=sku["name"] if sku else (name_val or code), new=fc, status="error", message="Forecast quantity cannot be negative")
                elif not sku:
                    # Dynamically recognize & accept NEW SKU!
                    seen.add(code.upper())
                    cat_str = str(cat_val or "").lower()
                    if "02" in cat_str or "frozen" in cat_str:
                        target_cat_code = "02"
                        target_cat_name = "Frozen Foods"
                    elif "01" in cat_str or "chicken" in cat_str or "process" in cat_str:
                        target_cat_code = "01"
                        target_cat_name = "Process Chicken"
                    elif "05" in cat_str or "dry" in cat_str or "bakery" in cat_str:
                        target_cat_code = "05"
                        target_cat_name = "Dry Foods"
                    else:
                        target_cat_code = req_cat if req_cat not in ("ALL", "") else "05"
                        cat_map = {"05": "Dry Foods", "02": "Frozen Foods", "01": "Process Chicken"}
                        target_cat_name = str(cat_val).strip() if cat_val else cat_map.get(target_cat_code, "Finished Goods")

                    prod_name = str(name_val).strip() if name_val else code
                    target_ctn_size = to_int(ctn_val) or 12

                    new_sku_payload = {
                        "sku_code": code,
                        "name": prod_name,
                        "category_code": target_cat_code,
                        "category": target_cat_name,
                        "ctn_size": target_ctn_size
                    }
                    item.update(
                        name=prod_name,
                        old=None,
                        new=fc,
                        sku_id=None,
                        is_new_sku=True,
                        new_sku_data=new_sku_payload,
                        status="ok",
                        message=f"New SKU '{code}' — will be auto-created in Master & BOM (Unit {target_cat_code})"
                    )
                else:
                    seen.add(code.upper())
                    ex = existing.get(int(sku["id"]))
                    old = float(ex["forecast_ctn"] or 0) if ex else None
                    item.update(name=sku["name"], old=old, new=fc, sku_id=int(sku["id"]))
                    if ex is not None and abs(old - fc) < 0.001:
                        item.update(status="skip", message="No change in value")
                    elif ex is not None:
                        item.update(message=f"Revision ({old:g} → {fc:g}, {fc - old:+g})")
                        if old > 0 and abs(fc - old) / old > 0.5:
                            item.update(status="warn", message=item["message"] + " - 50%+ change, please review")
                    elif fc == 0:
                        item.update(status="warn", message="New item with 0 forecast")
                    else:
                        item.update(message="New forecast entry (Rev 0)")
                report.append(item)

            if not report:
                return jsonify({"error": "No valid data rows found in file. Please use the official template."}), 400
            if preview:
                return preview_response(report, {"month": month, "title": f"Sales Forecast — {month}"})

            user = get_current_user_name("Bulk Upload")
            today = bd_today()
            applied = 0
            for it in report:
                if it["status"] not in ("ok", "warn"):
                    continue
                sid, val = it.get("sku_id"), it["new"]

                # If this is a new SKU, dynamically register in skus table first
                if it.get("is_new_sku") or not sid:
                    new_data = it.get("new_sku_data") or {}
                    cur = conn.cursor()
                    existing_sku = cur.execute("SELECT id FROM skus WHERE UPPER(sku_code) = UPPER(?)", (it["code"],)).fetchone()
                    if existing_sku:
                        sid = existing_sku["id"]
                    else:
                        cur.execute("""
                            INSERT INTO skus (category_code, sku_code, name, category, ctn_size, status)
                            VALUES (?, ?, ?, ?, ?, 'Active')
                        """, (
                            new_data.get("category_code", req_cat),
                            it["code"],
                            new_data.get("name", it["code"]),
                            new_data.get("category", "Finished Goods"),
                            new_data.get("ctn_size", 12)
                        ))
                        sid = cur.lastrowid
                    it["sku_id"] = sid

                w1 = round(val * 0.25, 1)
                w2 = round(val * 0.25, 1)
                w3 = round(val * 0.25, 1)
                w4 = max(0.0, round(val - (w1 + w2 + w3), 1))
                ex = existing.get(sid)
                if ex is None:
                    conn.execute("""
                        INSERT INTO sales_forecast (sku_id, month_year, forecast_ctn, original_forecast_ctn, revision_no,
                            revised_date, revision_notes, week1_ctn, week2_ctn, week3_ctn, week4_ctn, created_by, updated_at)
                        VALUES (?, ?, ?, ?, 0, ?, 'Bulk upload', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (sid, month, val, val, today, w1, w2, w3, w4, user))
                else:
                    prev = float(ex["forecast_ctn"] or 0)
                    new_rev = int(ex["revision_no"] or 0) + 1
                    conn.execute("""
                        INSERT INTO sales_forecast_revisions (sku_id, month_year, revision_no, revised_date,
                            previous_ctn, revised_ctn, variance_ctn, reason, revised_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'Bulk upload', ?)
                    """, (sid, month, new_rev, today, prev, val, round(val - prev, 2), user))
                    conn.execute("""
                        UPDATE sales_forecast SET forecast_ctn = ?, revision_no = ?, revised_date = ?,
                            revision_notes = 'Bulk upload', week1_ctn = ?, week2_ctn = ?, week3_ctn = ?, week4_ctn = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE sku_id = ? AND month_year = ?
                    """, (val, new_rev, today, w1, w2, w3, w4, sid, month))
                applied += 1
            conn.commit()
            invalidate_dashboard_cache()
            return jsonify({"success": True, "count": applied,
                            "message": f"Successfully updated forecast for {applied} SKUs ({month})"})
        except Exception as e:
            return jsonify({"error": f"Forecast upload failed: {e}"}), 400
        finally:
            conn.close()

    # ============================================================ PRODUCTION PLANS
    @app.route("/api/production/bulk-upload", methods=["POST"])
    def bulk_upload_production_plans():
        allowed, err_resp, err_code = check_role_permission(["Production"], "bulk upload production plans")
        if not allowed:
            return err_resp, err_code
        if "file" not in request.files:
            return jsonify({"error": "No file selected."}), 400
        cat = request.form.get("category", "ALL")
        month = request.form.get("month") or bd_month()
        preview = is_preview()
        try:
            table = read_table(request.files["file"])
        except Exception as e:
            return jsonify({"error": f"Failed to read file: {e}"}), 400

        sku_col, name_col, date_col, ctn_col, notes_col, header_row = 0, 1, 3, 4, 5, 3
        for r in range(0, min(5, len(table))):
            for c, v in enumerate(table[r][:10]):
                val = str(v or "").strip().lower()
                if "sku code" in val:
                    sku_col, header_row = c, r + 1
                elif "product" in val or "name" in val or "finished good" in val:
                    name_col = c
                elif "plan date" in val or "date" in val:
                    date_col = c
                elif "carton" in val:
                    ctn_col = c
                elif "notes" in val:
                    notes_col = c

        conn = get_db()
        try:
            req_cat = str(cat if cat not in ("ALL", "", None) else "05").strip()
            all_skus = conn.execute("SELECT id, sku_code, name, category_code, category, ctn_size FROM skus").fetchall()
            skus_by_code = {str(s["sku_code"]).strip().upper(): s for s in all_skus}
            def norm_txt(s):
                return re.sub(r'[^a-z0-9]', '', str(s).lower()) if s else ''
            skus_by_name = {norm_txt(s["name"]): s for s in all_skus if s["name"]}

            existing = {(int(r["sku_id"]), r["plan_date"]) for r in conn.execute(
                "SELECT sku_id, plan_date FROM production_plans WHERE status != 'Cancelled'").fetchall()}

            report, seen = [], set()
            for idx in range(header_row, len(table)):
                row = table[idx]
                code_val = cell(row, sku_col)
                if code_val in (None, ""):
                    continue
                code = str(code_val).strip()
                name_val = cell(row, name_col) if name_col is not None else ""
                item = {"row": idx + 1, "code": code, "name": "", "date": "", "new": None,
                        "status": "ok", "message": ""}
                
                sku = skus_by_code.get(code.upper())
                if not sku and name_val:
                    sku = skus_by_name.get(norm_txt(name_val))
                if not sku:
                    sku = skus_by_name.get(norm_txt(code))

                ctns = to_float(cell(row, ctn_col))
                pdate = norm_date(cell(row, date_col), f"{month}-01")
                if ctns is None or ctns <= 0:
                    item.update(name=sku["name"] if sku else (name_val or code), status="error", message="Carton quantity must be greater than 0")
                elif pdate is None:
                    item.update(name=sku["name"] if sku else (name_val or code), new=ctns, status="error", message="Invalid Plan Date format")
                elif not sku:
                    # Auto-register new SKU
                    prod_name = str(name_val).strip() if name_val else code
                    item.update(name=prod_name, date=pdate, new=ctns, sku_id=None,
                                is_new_sku=True,
                                new_sku_data={
                                    "sku_code": code,
                                    "name": prod_name,
                                    "category_code": req_cat,
                                    "category": "Finished Goods",
                                    "ctn_size": 12
                                },
                                cat=req_cat, ctn_size=12,
                                notes=str(cell(row, notes_col) or "Bulk imported plan").strip(),
                                status="ok",
                                message=f"New SKU '{code}' — will be auto-created in Master & BOM (Unit {req_cat})")
                else:
                    item.update(name=sku["name"], date=pdate, new=ctns, sku_id=int(sku["id"]),
                                cat=sku["category_code"], ctn_size=sku["ctn_size"] or 12,
                                notes=str(cell(row, notes_col) or "Bulk imported plan").strip())
                    key = (int(sku["id"]), pdate)
                    if key in existing or key in seen:
                        item.update(status="skip", message="Plan already exists for this SKU and date - skipped duplicate")
                    else:
                        seen.add(key)
                        if cat not in ("ALL", "", None) and sku["category_code"] != cat:
                            item.update(status="warn", message=f"SKU belongs to Unit {sku['category_code']}, uploading to Unit {cat}")
                        elif pdate[:7] != month:
                            item.update(status="warn", message=f"Plan date is for month {pdate[:7]}, selected month is {month}")
                        else:
                            item.update(message="New production plan and material requisition will be generated")
                report.append(item)

            if not report:
                return jsonify({"error": "No valid data rows found in file. Please use the official template."}), 400
            if preview:
                return preview_response(report, {"month": month, "title": f"Production Plans — {month}"})

            user = get_current_user_name("Bulk Import")
            created = 0
            for it in report:
                if it["status"] not in ("ok", "warn"):
                    continue
                cursor = conn.cursor()
                sid = it.get("sku_id")
                if it.get("is_new_sku") or not sid:
                    new_data = it.get("new_sku_data") or {}
                    existing_sku = cursor.execute("SELECT id FROM skus WHERE UPPER(sku_code) = UPPER(?)", (it["code"],)).fetchone()
                    if existing_sku:
                        sid = existing_sku["id"]
                    else:
                        cursor.execute("""
                            INSERT INTO skus (category_code, sku_code, name, category, ctn_size, status)
                            VALUES (?, ?, ?, ?, ?, 'Active')
                        """, (
                            new_data.get("category_code", req_cat),
                            it["code"],
                            new_data.get("name", it["code"]),
                            new_data.get("category", "Finished Goods"),
                            new_data.get("ctn_size", 12)
                        ))
                        sid = cursor.lastrowid
                    it["sku_id"] = sid

                max_id = int(cursor.execute("SELECT COALESCE(MAX(id), 0) FROM production_plans").fetchone()[0] or 0)
                pmonth = it["date"][:7]
                plan_no = f"PP-{it['cat']}-{pmonth.replace('-', '')}-{max_id + 1:03d}"
                cursor.execute("""
                    INSERT INTO production_plans (category_code, plan_no, month_year, plan_date, sku_id, planned_ctn,
                        planned_pcs, created_by, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (it["cat"], plan_no, pmonth, it["date"], sid, it["new"],
                      it["new"] * it["ctn_size"], user, it["notes"]))
                plan_id = int(cursor.lastrowid or (max_id + 1))
                req_no = f"MR-{it['cat']}-{pmonth.replace('-', '')}-{plan_id:03d}"
                cursor.execute("""
                    INSERT INTO material_requisitions (category_code, req_no, plan_id, month_year, req_date, status, requested_by, remarks)
                    VALUES (?, ?, ?, ?, ?, 'Pending', ?, ?)
                """, (it["cat"], req_no, plan_id, pmonth, it["date"], user, f"Auto-generated requisition for {plan_no}"))
                req_id = cursor.lastrowid
                for b in conn.execute("""
                    SELECT b.material_id, b.rate_per_ctn, m.current_stock, m.in_transit
                    FROM bom b
                    JOIN materials m ON b.material_id = m.id
                    WHERE b.sku_id = ?
                """, (it["sku_id"],)).fetchall():
                    gross_req = round(it["new"] * b["rate_per_ctn"], 2)
                    avail_stock = round((b["current_stock"] or 0.0) + (b["in_transit"] or 0.0), 2)
                    net_req = max(0.0, round(gross_req - avail_stock, 2))
                    cursor.execute("""
                        INSERT INTO requisition_items (requisition_id, material_id, gross_required_qty, available_stock, required_qty)
                        VALUES (?, ?, ?, ?, ?)
                    """, (req_id, b["material_id"], gross_req, avail_stock, net_req))
                created += 1
            conn.commit()
            invalidate_dashboard_cache()
            return jsonify({"success": True, "count": created,
                            "message": f"Successfully created {created} production plans and material requisitions"})
        except Exception as e:
            return jsonify({"error": f"Production plan upload failed: {e}"}), 400
        finally:
            conn.close()

    # ============================================================ MRR
    @app.route("/api/store/mrr/bulk-upload", methods=["POST"])
    def bulk_upload_mrr():
        allowed, err_resp, err_code = check_role_permission(["Store"], "bulk upload MRR entries")
        if not allowed:
            return err_resp, err_code
        if "file" not in request.files or not request.files["file"].filename:
            return jsonify({"error": "No file selected."}), 400
        preview = is_preview()
        try:
            table = read_table(request.files["file"])
        except Exception as e:
            return jsonify({"error": f"Failed to read file: {e}"}), 400

        conn = get_db()
        try:
            mats = {str(m["item_code"]).strip().upper(): m for m in conn.execute(
                "SELECT id, item_code, name, category_code, current_stock, unit_price FROM materials").fetchall()}
            today = bd_today()
            report = []
            for idx in range(3, len(table)):  # data starts at row 4
                row = table[idx]
                code_val = cell(row, 1)
                if code_val in (None, ""):
                    continue
                code = str(code_val).strip().upper()
                item = {"row": idx + 1, "code": code, "name": "", "new": None, "status": "ok", "message": ""}
                mat = mats.get(code)
                qty = to_float(cell(row, 6))
                if not mat:
                    item.update(status="error", message="Item Code not found in materials master")
                    report.append(item)
                    continue
                item["name"] = mat["name"]
                if qty is None or qty <= 0:
                    item.update(status="error", message="Quantity must be greater than 0")
                    report.append(item)
                    continue
                price = to_float(cell(row, 7))
                master = float(mat["unit_price"] or 0)
                if price is None or price <= 0:
                    price = master
                mdate = norm_date(cell(row, 3), today)
                note = "Will be added to stock"
                status = "ok"
                if mdate is None:
                    mdate, status, note = today, "warn", "Unrecognized date format - defaulting to today"
                elif master > 0 and price > 0 and abs(price - master) / master > 0.3:
                    status, note = "warn", f"Price differs by 30%+ from master rate ({master:g} vs {price:g})"
                item.update(status=status, message=note, new=qty, price=price, date=mdate,
                            mat_id=int(mat["id"]), cat=mat["category_code"],
                            supplier=str(cell(row, 4) or "Standard Supplier").strip(),
                            chalan=str(cell(row, 5) or f"CH-{bd_now().strftime('%Y%m')}-{idx + 1}").strip(),
                            vehicle=str(cell(row, 8) or "").strip(),
                            remarks=str(cell(row, 9) or "Bulk Excel Inward").strip())
                report.append(item)

            if not report:
                return jsonify({"error": "No valid data rows found. Please use the MRR template."}), 400
            if preview:
                return preview_response(report, {"title": "MRR Stock Inward"})

            user = get_current_user_name("Store Staff")
            cursor = conn.cursor()
            done = 0
            for it in report:
                if it["status"] not in ("ok", "warn"):
                    continue
                max_id = int(cursor.execute("SELECT COALESCE(MAX(id), 0) FROM mrr_entries").fetchone()[0] or 0)
                mrr_no = f"MRR-{it['cat']}-{bd_now().strftime('%Y%m')}-{max_id + 1:03d}"
                cursor.execute("""
                    INSERT INTO mrr_entries (category_code, mrr_no, mrr_date, supplier_name, chalan_no, material_id,
                        received_qty, unit_price, total_amount, vehicle_no, remarks, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (it["cat"], mrr_no, it["date"], it["supplier"], it["chalan"], it["mat_id"], it["new"],
                      it["price"], round(it["new"] * it["price"], 2), it["vehicle"], it["remarks"], user))
                cursor.execute("UPDATE materials SET current_stock = ROUND(current_stock + ?, 2) WHERE id = ?",
                               (it["new"], it["mat_id"]))
                bal = cursor.execute("SELECT current_stock FROM materials WHERE id = ?", (it["mat_id"],)).fetchone()[0]
                cursor.execute("""
                    INSERT INTO store_transactions (material_id, txn_type, qty, balance_after, reference_no, notes, created_by)
                    VALUES (?, 'MRR_ENTRY', ?, ?, ?, ?, ?)
                """, (it["mat_id"], it["new"], bal, mrr_no,
                      f"Bulk MRR from {it['supplier']} (Chalan: {it['chalan']})", user))
                done += 1
            conn.commit()
            invalidate_dashboard_cache()
            return jsonify({"success": True, "processed_count": done,
                            "message": f"Successfully imported {done} MRR stock inward entries into godown stock"})
        except Exception as e:
            return jsonify({"error": f"MRR upload failed: {e}"}), 400
        finally:
            conn.close()

    # ============================================================ POLOXY ACTUAL OUTPUT
    @app.route("/api/production/output-import", methods=["POST"])
    def import_production_output():
        """Actual production output comes from Poloxy's downloaded output report
        (Excel/CSV). Preview first, then confirm. Re-importing the same report is
        safe: values are SET per plan (not added)."""
        allowed, err_resp, err_code = check_role_permission(["Production"], "import actual production output")
        if not allowed:
            return err_resp, err_code
        if "file" not in request.files or not request.files["file"].filename:
            return jsonify({"error": "Please select a valid Poloxy Output Report file."}), 400
        preview = is_preview()
        cat = request.form.get("category", "ALL")
        qty_unit = request.form.get("qty_unit", "pcs")  # pcs (packet) | ctn
        default_date = norm_date(request.form.get("default_date"), bd_today())
        try:
            table = read_table(request.files["file"])
        except Exception as e:
            return jsonify({"error": f"Failed to read file: {e}"}), 400

        hdr, cols = None, {}
        for i, row in enumerate(table[:25]):
            found = {}
            for j, v in enumerate(row):
                t = str(v or "").strip().lower()
                if not t:
                    continue
                if "date" in t and "date" not in found:
                    found["date"] = j
                elif ("code" in t) and "code" not in found:
                    found["code"] = j
                elif any(k in t for k in ("item name", "product", "item", "name", "description")) and "name" not in found:
                    found["name"] = j
                elif any(k in t for k in ("output", "produced", "quantity", "qty")) and "qty" not in found:
                    found["qty"] = j
            if "qty" in found and ("code" in found or "name" in found):
                hdr, cols = i, found
                break
        if hdr is None:
            return jsonify({"error": "Required columns missing. Expected: Item/SKU Code (or Name) and Output/Quantity."}), 400

        conn = get_db()
        try:
            sku_rows = conn.execute("SELECT id, sku_code, name, category_code, ctn_size FROM skus").fetchall()
            by_code = {str(s["sku_code"]).strip().upper(): s for s in sku_rows}
            by_name = {str(s["name"]).strip().lower(): s for s in sku_rows}

            report, agg = [], {}
            for idx in range(hdr + 1, len(table)):
                row = table[idx]
                if all(v in (None, "") for v in row):
                    continue
                code = str(cell(row, cols.get("code")) or "").strip()
                name = str(cell(row, cols.get("name")) or "").strip()
                first = str(row[0] or "").strip().lower() if row else ""
                if (not code and not name) or first.startswith(("total", "grand")) or name.lower().startswith(("total", "grand")):
                    continue
                item = {"row": idx + 1, "code": code or name, "name": name if code else "",
                        "date": "", "new": None, "old": None, "status": "ok", "message": ""}
                sku = by_code.get(code.upper()) if code else None
                if sku is None and name:
                    sku = by_name.get(name.lower())
                qty = to_float(cell(row, cols["qty"]))
                d = norm_date(cell(row, cols.get("date")), default_date) if "date" in cols else default_date
                if sku is None:
                    item.update(status="error", message="Item not found in SKU master catalog")
                elif cat not in ("ALL", "", None) and sku["category_code"] != cat:
                    item.update(name=sku["name"], status="skip", message=f"SKU belongs to another category (Unit {sku['category_code']})")
                elif qty is None:
                    item.update(name=sku["name"], status="error", message="Invalid output quantity number")
                elif qty <= 0:
                    item.update(name=sku["name"], status="skip", message="Output quantity is 0 or negative - skipped")
                elif d is None:
                    item.update(name=sku["name"], status="error", message="Unrecognized date format")
                else:
                    item.update(name=sku["name"], date=d)
                    key = (int(sku["id"]), d)
                    a = agg.setdefault(key, {"sku": sku, "qty": 0.0, "rows": []})
                    a["qty"] += qty
                    a["rows"].append(idx + 1)
                    continue
                report.append(item)

            for (sid, d), a in agg.items():
                sku = a["sku"]
                size = float(sku["ctn_size"] or 12)
                qty_ctn = a["qty"] if qty_unit == "ctn" else a["qty"] / size
                qty_ctn = round(qty_ctn, 2)
                plan = conn.execute("""
                    SELECT id, plan_no, planned_ctn, actual_produced_ctn FROM production_plans
                    WHERE sku_id = ? AND plan_date = ? AND status != 'Cancelled' ORDER BY id LIMIT 1
                """, (sid, d)).fetchone()
                rows_txt = ",".join(str(x) for x in a["rows"][:5])
                item = {"row": rows_txt, "code": sku["sku_code"], "name": sku["name"], "date": d,
                        "new": qty_ctn, "old": None, "status": "ok", "message": ""}
                if plan is None:
                    item.update(status="error", message="No active plan found for this date and SKU - create plan first")
                else:
                    old = float(plan["actual_produced_ctn"] or 0)
                    planned = float(plan["planned_ctn"] or 0)
                    item.update(old=old, plan_id=int(plan["id"]), plan_no=plan["plan_no"], planned=planned,
                                size=size, message=f"{plan['plan_no']}: {old:g} → {qty_ctn:g} ctn")
                    if abs(old - qty_ctn) < 0.005:
                        item.update(status="skip", message="No change (already updated)")
                    elif planned > 0 and qty_ctn > planned * 1.5:
                        item.update(status="warn", message=item["message"] + f" - 1.5x higher than planned target ({planned:g})")
                report.append(item)

            report.sort(key=lambda r: ({"error": 0, "warn": 1, "ok": 2, "skip": 3}.get(r["status"], 4), str(r.get("date"))))
            if not report:
                return jsonify({"error": "No valid production output rows found in report."}), 400
            title = "Poloxy Actual Output"
            if preview:
                return preview_response(report, {"title": title, "unit_note": "Quantity in " + ("Cartons" if is_ctn else "Packets/Pieces")})

            now_txt = bd_now().strftime("%d-%b-%Y %I:%M:%S %p")
            cursor = conn.cursor()
            applied = 0
            for it in report:
                if it["status"] not in ("ok", "warn"):
                    continue
                new_ctn, old = it["new"], it["old"] or 0.0
                status = "Completed" if it["planned"] > 0 and new_ctn >= it["planned"] else "In Production"
                cursor.execute("""
                    UPDATE production_plans SET actual_produced_ctn = ?, actual_produced_pcs = ?, status = ?,
                        poloxy_synced_at = ? WHERE id = ?
                """, (new_ctn, round(new_ctn * it["size"], 2), status, now_txt, it["plan_id"]))
                delta = new_ctn - old
                if delta > 0:
                    sku_id = cursor.execute("SELECT sku_id FROM production_plans WHERE id = ?", (it["plan_id"],)).fetchone()[0]
                    for b in cursor.execute("SELECT material_id, rate_per_ctn FROM bom WHERE sku_id = ?", (sku_id,)).fetchall():
                        cursor.execute("""
                            UPDATE materials SET floor_stock = MAX(0.0, ROUND(COALESCE(floor_stock, 0.0) - ?, 4)) WHERE id = ?
                        """, (round(delta * b["rate_per_ctn"], 4), b["material_id"]))
                applied += 1
            stamp = bd_now().strftime("%Y-%m-%d %H:%M")
            for k, v in (("last_output_import_at", stamp), ("last_output_import_rows", str(applied)),
                         ("last_output_import_by", get_current_user_name("User"))):
                cursor.execute("""
                    INSERT INTO erp_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """, (k, v))
            conn.commit()
            invalidate_dashboard_cache()
            return jsonify({"success": True, "count": applied,
                            "message": f"Successfully updated actual output for {applied} plans from Poloxy report"})
        except Exception as e:
            return jsonify({"error": f"Output import failed: {e}"}), 400
        finally:
            conn.close()

    # ============================================================ MY TASKS DASHBOARD
    def _cat_clause(col, cats):
        if not cats:
            return "", []
        return f" AND {col} IN ({','.join('?' * len(cats))})", list(cats)

    @app.route("/api/dashboard/my-tasks", methods=["GET"])
    def dashboard_my_tasks():
        sess = get_current_session()
        role = sess.get("role") or ""
        month = request.args.get("month") or bd_month()
        req_cat = (request.args.get("category") or "ALL").strip()
        allowed = (sess.get("allowed_category") or "ALL").strip()
        allowed_list = [c.strip() for c in allowed.split(",") if c.strip() and c.strip().upper() != "ALL"]
        if allowed_list:
            cats = [req_cat] if req_cat in allowed_list else allowed_list
        else:
            cats = [] if req_cat.upper() in ("ALL", "", "UNDEFINED", "NULL", "NONE") else [req_cat]
        today = bd_today()
        full = role in ("Super Admin", "Admin", "Management")
        groups = []
        if full or role == "Sales":
            groups.append("Sales")
        if full or role == "Production":
            groups.append("Production")
        if full or role == "Store":
            groups.append("Store")
        if full or role == "Supply Chain":
            groups.append("Supply Chain")
        if not groups:
            groups = ["Store"]

        conn = get_db()
        tasks, summary = [], []

        def add(group, level, icon, title, detail, count=None, label=None, view=None, options=None):
            tasks.append({"group": group, "level": level, "icon": icon, "title": title, "detail": detail,
                          "count": count, "action": ({"label": label, "view": view, "options": options or {}} if view else None)})

        try:
            sk_c, sk_p = _cat_clause("s.category_code", cats)
            pl_c, pl_p = _cat_clause("category_code", cats)
            mt_c, mt_p = _cat_clause("m.category_code", cats)

            def one(sql, params):
                r = conn.execute(sql, params).fetchone()
                return r[0] if r is not None else 0

            # ---- month performance summary (for everyone)
            perf = conn.execute(f"""
                SELECT COALESCE(SUM(planned_ctn),0) AS planned, COALESCE(SUM(actual_produced_ctn),0) AS actual
                FROM production_plans WHERE month_year = ? AND status != 'Cancelled' {pl_c}
            """, [month] + pl_p).fetchone()
            planned, actual = float(perf["planned"] or 0), float(perf["actual"] or 0)
            ach = round(actual / planned * 100, 1) if planned > 0 else 0.0
            crit_total = one(f"SELECT COUNT(*) FROM materials m WHERE m.current_stock < m.safety_stock {mt_c}", mt_p)
            summary.append({"label": f"{month} Actual / Plan", "value": f"{actual:,.0f} / {planned:,.0f} ctn", "sub": f"Achievement: {ach:.1f}%"})
            summary.append({"label": "Critical Materials", "value": str(crit_total), "sub": "Below Safety Stock"})

            # ---- SALES
            if "Sales" in groups:
                total_sku = one(f"SELECT COUNT(*) FROM skus s WHERE s.status = 'Active' {sk_c}", sk_p)
                have = one(f"""SELECT COUNT(DISTINCT sf.sku_id) FROM sales_forecast sf JOIN skus s ON sf.sku_id = s.id
                               WHERE sf.month_year = ? AND s.status = 'Active' {sk_c}""", [month] + sk_p)
                missing = max(0, total_sku - have)
                nm = next_month_str(month)
                n_have = one(f"""SELECT COUNT(*) FROM sales_forecast sf JOIN skus s ON sf.sku_id = s.id
                                 WHERE sf.month_year = ? {sk_c}""", [nm] + sk_p)
                n0 = 0
                if missing > 0:
                    add("Sales", "warning", "fa-chart-line", f"Pending Forecast for {month}",
                        f"{missing} of {total_sku} SKUs missing forecast entries", missing,
                        "Set Forecast", "forecast")
                    n0 += 1
                if bd_now().day >= 20 and n_have == 0:
                    add("Sales", "info", "fa-calendar-plus", f"Start Forecast for Next Month ({nm})",
                        "End of month planning - advance forecast required for production plan", None,
                        "Upload / Entry", "forecast")
                    n0 += 1
                if n0 == 0:
                    add("Sales", "success", "fa-check-circle", "Sales: All Caught Up", f"Forecast entries complete for all SKUs in {month}", None, "View", "forecast")

            # ---- PRODUCTION
            if "Production" in groups:
                n0 = 0
                tp = conn.execute(f"""SELECT COUNT(*) AS c, COALESCE(SUM(planned_ctn),0) AS s FROM production_plans
                                      WHERE plan_date = ? AND status != 'Cancelled' {pl_c}""", [today] + pl_p).fetchone()
                if tp["c"]:
                    add("Production", "info", "fa-industry", "Today's Production Plans",
                        f"{tp['c']} active plans, target: {float(tp['s']):,.0f} cartons", tp["c"], "View Plans", "production")
                    n0 += 1
                overdue = one(f"""SELECT COUNT(*) FROM production_plans WHERE month_year = ? AND plan_date < ?
                                  AND actual_produced_ctn = 0 AND status != 'Cancelled' {pl_c}""", [month, today] + pl_p)
                last = conn.execute("SELECT value FROM erp_settings WHERE key = 'last_output_import_at'").fetchone()
                last_txt = last["value"] if last else None
                if overdue:
                    add("Production", "danger", "fa-exclamation-circle", "Actual Production Output Pending",
                        f"{overdue} past plans have zero output recorded. Import Poloxy output report"
                        + (f" (Last import: {last_txt})" if last_txt else " (No output imported yet)"),
                        overdue, "Import Poloxy Output", "production", {"action": "output-import"})
                    n0 += 1
                elif last_txt:
                    try:
                        age = bd_now() - datetime.strptime(last_txt, "%Y-%m-%d %H:%M")
                        if age > timedelta(days=2):
                            add("Production", "warning", "fa-sync-alt", "Poloxy Output is over 2 days old",
                                f"Last imported: {last_txt}", None, "Import Poloxy Output", "production", {"action": "output-import"})
                            n0 += 1
                    except ValueError:
                        pass
                fc_total = one(f"""SELECT COALESCE(SUM(sf.forecast_ctn),0) FROM sales_forecast sf JOIN skus s ON sf.sku_id = s.id
                                   WHERE sf.month_year = ? {sk_c}""", [month] + sk_p)
                if fc_total > 0 and planned == 0:
                    add("Production", "warning", "fa-clipboard-list", f"Create Production Plans for {month}",
                        f"Sales forecast is {float(fc_total):,.0f} cartons, but no production plans created yet", None,
                        "Create Plan", "production")
                    n0 += 1
                if n0 == 0:
                    add("Production", "success", "fa-check-circle", "Production: All Caught Up", "No pending outputs or actions required today", None, "View", "production")

            # ---- STORE
            if "Store" in groups:
                n0 = 0
                rq_c, rq_p = _cat_clause("category_code", cats)
                pend = one(f"""SELECT COUNT(*) FROM material_requisitions WHERE status IN ('Pending','Partially Issued') {rq_c}""", rq_p)
                if pend:
                    add("Store", "warning", "fa-dolly", "Pending Material Requisitions",
                        f"{pend} material requisitions pending issue to production floor", pend, "View Requisitions", "store", {"subTab": "requisitions"})
                    n0 += 1
                crit = conn.execute(f"""SELECT m.name, m.current_stock, m.safety_stock FROM materials m
                                        WHERE m.current_stock < m.safety_stock {mt_c}
                                        ORDER BY (m.current_stock * 1.0 / NULLIF(m.safety_stock,0)) ASC LIMIT 3""", mt_p).fetchall()
                if crit_total:
                    names = ", ".join(str(c["name"]) for c in crit)
                    add("Store", "danger", "fa-box-open", "Critical Material Stocks",
                        f"{crit_total} materials are below safety stock threshold. Lowest: {names}", crit_total,
                        "View Critical", "store", {"filter": "critical"})
                    n0 += 1
                pr_c, pr_p = _cat_clause("category_code", cats)
                transit = one(f"SELECT COUNT(*) FROM purchase_requisitions WHERE status = 'In-Transit' {pr_c}", pr_p)
                if transit:
                    add("Store", "info", "fa-truck", "Shipments in Transit - Pending MRR",
                        f"{transit} purchase orders in-transit - log MRR upon arrival", transit, "View POs", "procurement")
                    n0 += 1
                if n0 == 0:
                    add("Store", "success", "fa-check-circle", "Store: All Caught Up", "No pending requisitions or critical stock deficits", None, "Inventory", "store")

            # ---- SUPPLY CHAIN
            if "Supply Chain" in groups:
                n0 = 0
                pr_c, pr_p = _cat_clause("category_code", cats)
                uncovered = one(f"""SELECT COUNT(*) FROM materials m WHERE m.current_stock < m.safety_stock {mt_c}
                                    AND m.id NOT IN (SELECT material_id FROM purchase_requisitions
                                                     WHERE status IN ('Pending','Approved','In-Transit'))""", mt_p)
                if uncovered:
                    add("Supply Chain", "danger", "fa-shopping-cart", "Critical Materials Missing PR",
                        f"{uncovered} critical deficit materials have no open purchase requisitions", uncovered,
                        "Create PR", "procurement")
                    n0 += 1
                pend_pr = one(f"SELECT COUNT(*) FROM purchase_requisitions WHERE status = 'Pending' {pr_c}", pr_p)
                if pend_pr:
                    add("Supply Chain", "warning", "fa-file-signature", "PRs Pending Approval",
                        f"{pend_pr} Purchase Requisitions pending approval", pend_pr, "View PRs", "procurement")
                    n0 += 1
                if n0 == 0:
                    add("Supply Chain", "success", "fa-check-circle", "Supply Chain: All Caught Up", "No urgent procurement actions pending", None, "PO List", "procurement")
        finally:
            conn.close()

        order = {"danger": 0, "warning": 1, "info": 2, "success": 3}
        tasks.sort(key=lambda t: (groups.index(t["group"]) if t["group"] in groups else 9, order.get(t["level"], 4)))
        urgent = sum(1 for t in tasks if t["level"] in ("danger", "warning"))
        return jsonify({"role": role, "name": sess.get("name"), "month": month, "today": today,
                        "category": cats, "groups": groups, "urgent": urgent,
                        "summary": summary, "tasks": tasks})

    # ============================================================ BACKUP
    @app.route("/api/admin/backup", methods=["GET"])
    def admin_backup():
        if get_current_session().get("role") not in ("Super Admin", "Admin"):
            return jsonify({"error": "Access Denied: Only Super Admin can take system backup"}), 403
        conn = get_db()
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()]
            dump = {"_meta": {"created_at": bd_now().strftime("%Y-%m-%d %H:%M:%S"), "tables": len(names)}}
            for n in names:
                rows = [dict(r) for r in conn.execute(f'SELECT * FROM "{n}"').fetchall()]
                if n == "users":
                    for r in rows:
                        r.pop("password", None)
                dump[n] = rows
        finally:
            conn.close()
        fname = f"paragon_backup_{bd_now().strftime('%Y%m%d_%H%M')}.json"
        return Response(json.dumps(dump, default=str, ensure_ascii=False), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename={fname}"})
