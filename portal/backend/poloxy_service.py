import os
import re
import json
import urllib.request
import urllib.parse
import http.cookiejar
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Configured mappings for Categories, Factory Godowns and Poloxy Groups
POLOXY_CONFIG = {
    '05': {
        'name': '05. Dry Foods',
        'branch_id': 'B0005',
        'factory_godown_name': '05. Dry Food Factory Godown',
        'factory_godown_id': 'G14',
        'work_center': 'Dry Food Packing(Bakery Puffed Sauce Chanachur & Lachsa)',
        'work_centers': [
            'WC253@#Dry Food Packing-1(Sweets Pastry & Savory)',
            'WC275@#Dry Food Packing Export',
            'WC276@#DF Packing Corporate',
            'WC33@#Dry Food Packing(Bakery Puffed Sauce Chanachur & Lachsa)'
        ],
        'groups': {
            'RM': {'name': 'RAW MATERIAL', 'id': '14'},
            'PM': {'name': 'PACKAGING MATERIAL', 'id': '10'},
            'FG': {'name': 'CUT-Up-Part', 'id': '17'}
        }
    },
    '02': {
        'name': '02. Frozen Foods',
        'branch_id': 'B0002',
        'factory_godown_name': '02. Frozen Foods Factory Godown',
        'factory_godown_id': 'G5',
        'work_center': 'Frozen Finished Goods',
        'groups': {
            'RM': {'name': 'RAW MATERIAL', 'id': '14'},
            'PM': {'name': 'PACKAGING MATERIAL', 'id': '10'},
            'FG': {'name': 'CUT-Up-Part', 'id': '17'}
        }
    },
    '01': {
        'name': '01. Process Chicken',
        'branch_id': 'B0001',
        'factory_godown_name': '01. Process Factory Godown',
        'factory_godown_id': 'G2',
        'work_center': 'Process Chicken Plant',
        'groups': {
            'RM': {'name': 'RAW MATERIAL', 'id': '14'},
            'PM': {'name': 'PACKAGING MATERIAL', 'id': '10'},
            'FG': {'name': 'CUT-Up-Part', 'id': '17'}
        }
    }
}

def clean_num(v):
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    cleaned = re.sub(r'[^0-9.-]', '', str(v).strip())
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0

class PoloxyService:
    def __init__(self, conn=None):
        self.base_url = os.environ.get("POLOXY_URL", "http://erp.paragon.com.bd").rstrip('/')
        self.username = os.environ.get("POLOXY_USER", "").strip()
        self.password = os.environ.get("POLOXY_PASS", "").strip()

        # Fallback to erp_settings in database if environment variables are not set
        if not (self.username and self.password):
            try:
                db_conn = conn
                should_close = False
                if db_conn is None:
                    try:
                        from app import get_db
                        db_conn = get_db()
                        should_close = True
                    except Exception:
                        pass
                if db_conn is not None:
                    rows = db_conn.execute("SELECT key, value FROM erp_settings WHERE key IN ('poloxy_url', 'poloxy_user', 'poloxy_pass')").fetchall()
                    for r in rows:
                        k = r["key"] if hasattr(r, "__getitem__") else r[0]
                        v = r["value"] if hasattr(r, "__getitem__") else r[1]
                        if k == 'poloxy_url' and v and not os.environ.get("POLOXY_URL"):
                            self.base_url = str(v).rstrip('/')
                        elif k == 'poloxy_user' and v and not self.username:
                            self.username = str(v).strip()
                        elif k == 'poloxy_pass' and v and not self.password:
                            self.password = str(v).strip()
                    if should_close:
                        try:
                            db_conn.close()
                        except Exception:
                            pass
            except Exception as e:
                print(f"[!] PoloxyService credential fallback warning: {e}")

        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPRedirectHandler()
        )
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
            'Origin': self.base_url
        }
        self.authenticated = False

    def login(self):
        if not self.username or not self.password:
            raise ValueError("Poloxy ERP credentials missing. Please set POLOXY_USER and POLOXY_PASS in environment or ERP settings.")
        login_url = f"{self.base_url}/POLOXY/NewLogin"
        data = urllib.parse.urlencode({'uname': self.username, 'pwd': self.password}).encode('utf-8')
        req = urllib.request.Request(login_url, data=data, headers={**self.headers, 'Referer': f"{self.base_url}/POLOXY/jsp/login.jsp"})
        resp = self.opener.open(req, timeout=30)
        html = resp.read().decode('utf-8', errors='ignore')
        if "login.jsp" in resp.url and "Invalid" in html:
            raise RuntimeError("Poloxy login failed! Check credentials.")

        # SetContext (Module 10 = Processing)
        set_ctx_url = f"{self.base_url}/POLOXY/SetContext"
        req2 = urllib.request.Request(set_ctx_url, data=urllib.parse.urlencode({'module_id': '10'}).encode('utf-8'),
                                      headers={**self.headers, 'Referer': f"{self.base_url}/POLOXY/jsp/welcome.jsp"})
        resp2 = self.opener.open(req2, timeout=30)
        inputs1 = dict(re.findall(r'<input\s+type=[\'"]hidden[\'"]\s+name=[\'"]([^\'"]+)[\'"]\s+value=[\'"]([^\'"]*)[\'"]',
                                  resp2.read().decode('utf-8', errors='ignore'), re.IGNORECASE))

        # GetContext
        gc_url = f"{self.base_url}/PROCESSINGC/GetContext"
        req3 = urllib.request.Request(gc_url, data=urllib.parse.urlencode(inputs1).encode('utf-8'),
                                      headers={**self.headers, 'Referer': set_ctx_url})
        resp3 = self.opener.open(req3, timeout=30)
        inputs2 = dict(re.findall(r'<input\s+type=[\'"]hidden[\'"]\s+name=[\'"]([^\'"]+)[\'"]\s+value=[\'"]([^\'"]*)[\'"]',
                                  resp3.read().decode('utf-8', errors='ignore'), re.IGNORECASE))

        # SessionTraveller
        st_url = f"{self.base_url}/COMMON/SessionTraveller"
        req4 = urllib.request.Request(st_url, data=urllib.parse.urlencode(inputs2).encode('utf-8'),
                                      headers={**self.headers, 'Referer': resp3.url})
        self.opener.open(req4, timeout=30)

        # Pre-initialize stock report
        try:
            self.opener.open(urllib.request.Request(f"{self.base_url}/COMMON/r_jsp/godownitemstockreport.jsp", headers=self.headers), timeout=20)
        except Exception:
            pass

        self.authenticated = True
        return True

    def fetch_godown_stock_items(self, godown_name, godown_id, group_name, group_id, date_str=None, target_items=None):
        if not self.authenticated:
            self.login()

        if not date_str:
            date_str = datetime.now().strftime("%d/%m/%Y")
        elif "-" in date_str:
            # Convert YYYY-MM-DD to DD/MM/YYYY
            try:
                date_str = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
            except Exception:
                pass

        known_codes = {
            'milk bread 400gm': ('Milk Bread 400gm', 'IT05884'),
            'sandwich bread 700gm': ('Sandwich Bread 700gm', 'IT07043'),
            'gulab jamun': ('Gulab Jamun', 'IT05960'),
            'reshmi jilapi': ('Reshmi Jilapi', 'IT04429'),
            'sweet curd 100gm': ('Sweet Curd 100gm', 'IT04302'),
            'sweet & sour curd 750gm': ('Sweet & Sour Curd 750gm', 'IT09215'),
            'sweet curd 750gm': ('Sweet Curd 750gm', 'IT04303'),
            'mawa laddu 1000gm': ('Mawa Laddu 1000gm', 'IT04432'),
            'motichur laddu 1000gm': ('Motichur Laddu 1000gm', 'IT04430'),
            'mihidana laddu 1000gm': ('Mihidana Laddu 1000gm', 'IT04431'),
            'aflatun': ('Aflatun', 'IT06765'),
            'nimki': ('Nimki', 'IT09717'),
            'chicken patties round': ('Chicken Patties Round', 'IT06886'),
            'chicken patties triangle': ('Chicken Patties Triangle', 'IT06885'),
            'petty burger': ('Petty Burger', 'IT06598'),
            'single sandwich': ('Single Sandwich', 'IT06596'),
            'double sandwich': ('Double Sandwich', 'IT06597'),
            'balushai': ('Balushai', 'IT04433'),
            'kalo jaam': ('Kalo Jaam', 'IT03948'),
            'roshgolla': ('Roshgolla', 'IT03947'),
            'kacha chana 1000gm': ('Kacha Chana 1000gm', 'IT04434'),
            'lal mohon': ('Lal Mohon', 'IT04435'),
            'vug sagor': ('Vug Sagor', 'IT04438')
        }

        def resolve_item_code(name):
            clean = name.strip()
            clean_low = clean.lower()
            if clean_low in known_codes:
                return known_codes[clean_low]
            for k, v in known_codes.items():
                if k in clean_low or clean_low in k:
                    return v
            first_word = clean.split()[0]
            try:
                url = f"{self.base_url}/COMMON/Report_AjaxAutoComplete?term={urllib.parse.quote(first_word)}&param1=comp_item_gdstk_itemid&param2=&param3="
                res = self.opener.open(urllib.request.Request(url, headers=self.headers), timeout=5).read().decode('utf-8', errors='ignore')
                ac_items = json.loads(res) if res.strip() else []
                for it in ac_items:
                    lbl = it['label'].strip()
                    if clean_low == lbl.lower() or clean_low in lbl.lower():
                        return (lbl, it['values'])
                if ac_items:
                    return (ac_items[0]['label'], ac_items[0]['values'])
            except Exception:
                pass
            return (clean, '')

        def fetch_one_item(item_name):
            poloxy_name, code = resolve_item_code(item_name)
            if not code:
                return None
            stock_url = f"{self.base_url}/COMMON/GodownItemStockReport"
            form_data = {
                'item_godown_stock_radio': 'Godownwise',
                'item_stock_chk': '1',
                'data_gdstock_godown': godown_name,
                'gdstock_godown': str(godown_id),
                'stock_type': '0',
                'data_cstock_item_grp': '',
                'cstock_item_grp': '',
                'data_cstock_category_name': '',
                'category_id': '',
                'data_cstock_item_name': poloxy_name,
                'cstock_item_name': str(code),
                'fromm': '01/10/2026',
                'too': date_str,
                'cstock_dummy': 'null',
                'cstock_start_dt': date_str,
                'cstock_end_dt': date_str,
                'max1': 'null'
            }
            try:
                req = urllib.request.Request(
                    stock_url,
                    data=urllib.parse.urlencode(form_data).encode('utf-8'),
                    headers={**self.headers, 'Referer': f"{self.base_url}/COMMON/r_jsp/godownitemstockreport.jsp"}
                )
                resp = self.opener.open(req, timeout=12)
                html = resp.read().decode('utf-8', errors='ignore')
                trs = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL | re.I)
                for tr in trs:
                    cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL)]
                    if len(cells) >= 13:
                        c0 = cells[0].lower()
                        c1 = cells[1].strip()
                        c1_low = c1.lower()
                        c12 = cells[12].strip()
                        if c0 not in ('total', 'item code') and c1 != '' and c1_low not in ('particular', 'total', 'sub total', 'quantity') and c12.lower() != 'quantity':
                            closing_qty = clean_num(c12)
                            uom = cells[2].strip() if len(cells) > 2 else 'Pkt'
                            return {
                                'code': code,
                                'name': c1,
                                'uom': uom,
                                'closing_stock': closing_qty
                            }
            except Exception as e:
                pass
            return None

        items = {}
        if target_items:
            # Query specific target items concurrently
            unique_targets = list(dict.fromkeys([t.strip() for t in target_items if t and t.strip()]))
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = {executor.submit(fetch_one_item, it): it for it in unique_targets}
                for f in as_completed(futures):
                    res = f.result()
                    if res:
                        code = res['code']
                        name = res['name']
                        items[code] = res
                        items[name.lower()] = res
                        items[futures[f].lower()] = res
        else:
            # Fallback if no target items list provided
            stock_url = f"{self.base_url}/COMMON/GodownItemStockReport"
            form_data = {
                'item_godown_stock_radio': 'Godownwise',
                'item_stock_chk': '1',
                'data_gdstock_godown': godown_name,
                'gdstock_godown': str(godown_id),
                'stock_type': '0',
                'data_cstock_item_grp': group_name,
                'cstock_item_grp': str(group_id),
                'data_cstock_category_name': '',
                'category_id': '',
                'data_cstock_item_name': '',
                'cstock_item_name': '',
                'fromm': '01/10/2026',
                'too': date_str,
                'cstock_dummy': 'null',
                'cstock_start_dt': date_str,
                'cstock_end_dt': date_str,
                'max1': 'null'
            }
            try:
                req = urllib.request.Request(
                    stock_url,
                    data=urllib.parse.urlencode(form_data).encode('utf-8'),
                    headers={**self.headers, 'Referer': f"{self.base_url}/COMMON/r_jsp/godownitemstockreport.jsp"}
                )
                resp = self.opener.open(req, timeout=30)
                html = resp.read().decode('utf-8', errors='ignore')
                trs = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL | re.I)
                for tr in trs:
                    cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL)]
                    if len(cells) >= 13:
                        c0 = cells[0].lower()
                        c1 = cells[1].strip()
                        c1_low = c1.lower()
                        c12 = cells[12].strip()
                        if c0 not in ('total', 'item code') and c1 != '' and c1_low not in ('particular', 'total', 'sub total', 'quantity') and c12.lower() != 'quantity':
                            code = cells[0].strip()
                            name = c1
                            uom = cells[2].strip() if len(cells) > 2 else 'Pkt'
                            closing_stock = clean_num(c12)
                            key = code if code else name.lower()
                            entry = {
                                'code': code,
                                'name': name,
                                'uom': uom,
                                'closing_stock': closing_stock
                            }
                            items[key] = entry
                            items[name.lower()] = entry
            except Exception as e:
                print(f"[!] Warning Poloxy stock fallback failed: {e}")

        return items

    def fetch_production_output(self, cat_code, start_date_str=None, end_date_str=None):
        """
        Pulls live actual production output from Poloxy ERP dt_production_summary_rpt.
        Downloads the generated CSV (/PROCESSINGC/Production%20Summary.csv) and parses OUTPUT items.
        Returns dict: {item_name_lower: {'item': item_name, 'workcenter': wc, 'qty': float_qty}, ...}
        """
        if not self.authenticated:
            self.login()

        cfg = POLOXY_CONFIG.get(str(cat_code), POLOXY_CONFIG['05'])
        branch_id = cfg.get('branch_id', 'B0005')
        branch_name = cfg.get('name', '05. Dry Foods')

        if not start_date_str:
            start_date_str = datetime.now().strftime("%d/%m/%Y")
        elif "-" in start_date_str:
            try:
                start_date_str = datetime.strptime(start_date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
            except Exception:
                pass

        if not end_date_str:
            end_date_str = start_date_str
        elif "-" in end_date_str:
            try:
                end_date_str = datetime.strptime(end_date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
            except Exception:
                pass

        # 1. Select target work centers for this branch
        target_wcs = cfg.get('work_centers', [])
        if target_wcs:
            vals = target_wcs
        else:
            try:
                url_wc = f"{self.base_url}/PROCESSINGC/dt_production_summary_rpt?key=dt_production_summary_rpt&branch_id={branch_id}"
                req_wc = urllib.request.Request(url_wc, headers=self.headers)
                h_wc = self.opener.open(req_wc, timeout=20).read().decode('utf-8', errors='ignore')
                vals = re.findall(r'value=[\'"]([^\'"]+)[\'"]', h_wc)
            except Exception as e:
                print(f"[!] Warning fetching work centers: {e}")
                vals = []

        chkbranch = '$'.join(vals) if vals else f"WC33@#{cfg.get('work_center', '')}"
        ids = '/'.join([v.split('@#')[0] for v in vals]) if vals else "WC33"
        names = ';'.join([v.split('@#')[1] for v in vals]) if vals else cfg.get('work_center', '')

        # 2. Submit form requesting HTML report (which accurately contains all workcenters without CSV truncation)
        form_data = {
            'fromm': '01/10/2022',
            'too': '31/12/2027',
            'po_dummy': 'null',
            'brnach_unit_name': branch_name,
            'brnach_unit_id': branch_id,
            'data_gdstock_godown': names,
            'gdstock_godown': ids,
            'chkbranch': chkbranch,
            'st_date': start_date_str,
            'end_date': end_date_str,
            'csv_or_report': 'report',
            'max1': 'null',
            'selectall': ''
        }

        rpt_url = f"{self.base_url}/PROCESSINGC/dt_production_summary_rpt"
        req_rpt = urllib.request.Request(
            rpt_url,
            data=urllib.parse.urlencode(form_data).encode('utf-8'),
            headers={**self.headers, 'Referer': f"{self.base_url}/PROCESSINGC/trading_r_jsp/dt_production_summary_rpt.jsp"}
        )
        
        output_items = {}
        try:
            resp = self.opener.open(req_rpt, timeout=35)
            html_rpt = resp.read().decode('utf-8', errors='ignore')

            # Parse HTML table rows for OUTPUT items
            trs = re.findall(r'<tr[^>]*>.*?</tr>', html_rpt, re.DOTALL | re.I)
            for tr in trs:
                cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL)]
                if len(cells) >= 19 and 'OUTPUT' in [c.upper() for c in cells]:
                    out_idx = -1
                    for i, c in enumerate(cells):
                        if c.upper() == 'OUTPUT':
                            out_idx = i
                            break
                    if out_idx != -1:
                        item_name = cells[out_idx - 4] if out_idx >= 4 else cells[7]
                        wc = cells[out_idx - 5] if out_idx >= 5 else cells[6]
                        pri_qty = clean_num(cells[out_idx + 6] if len(cells) > out_idx + 6 else '0')
                        sec_qty = clean_num(cells[out_idx + 7] if len(cells) > out_idx + 7 else '0')
                        qty = sec_qty if sec_qty > 0 else pri_qty
                        
                        if qty > 0 and item_name.lower() not in ('total', 'sub total', 'primary unit', 'secondary unit', 'item', 'workcenter'):
                            key = item_name.lower().strip()
                            if key in output_items:
                                output_items[key]['qty'] += qty
                            else:
                                output_items[key] = {
                                    'item': item_name,
                                    'workcenter': wc,
                                    'qty': qty
                                }
        except Exception as e:
            print(f"[!] Warning parsing HTML report: {e}")

        # Fallback to CSV if HTML report returned no items
        if not output_items:
            try:
                form_data['csv_or_report'] = 'csv'
                req_csv_submit = urllib.request.Request(
                    rpt_url,
                    data=urllib.parse.urlencode(form_data).encode('utf-8'),
                    headers={**self.headers, 'Referer': f"{self.base_url}/PROCESSINGC/trading_r_jsp/dt_production_summary_rpt.jsp"}
                )
                self.opener.open(req_csv_submit, timeout=30)
                csv_url = f"{self.base_url}/PROCESSINGC/Production%20Summary.csv"
                csv_req = urllib.request.Request(csv_url, headers=self.headers)
                csv_data = self.opener.open(csv_req, timeout=30).read().decode('utf-8', errors='ignore')

                import csv
                import io
                reader = csv.DictReader(io.StringIO(csv_data))
                for row in reader:
                    if row.get('Type', '').strip().upper() == 'OUTPUT':
                        item_name = row.get('Item', '').strip()
                        if not item_name or item_name.lower() in ('total', 'sub total'):
                            continue
                        pri_qty = clean_num(row.get('Output Primary Unit Qty', 0))
                        sec_qty = clean_num(row.get('Output Secondary Unit Qty', 0))
                        qty = sec_qty if sec_qty > 0 else pri_qty
                        if qty <= 0:
                            continue

                        key = item_name.lower().strip()
                        if key in output_items:
                            output_items[key]['qty'] += qty
                        else:
                            output_items[key] = {
                                'item': item_name,
                                'workcenter': row.get('Workcenter', ''),
                                'qty': qty
                            }
            except Exception as e_csv:
                print(f"[!] Warning parsing fallback CSV: {e_csv}")

        return output_items

