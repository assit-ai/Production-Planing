import os
import re
import urllib.request
import urllib.parse
import http.cookiejar
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Configured mappings for Categories, Factory Godowns and Poloxy Groups
POLOXY_CONFIG = {
    '05': {
        'name': '05. Dry Foods',
        'branch_id': 'B0005',
        'factory_godown_name': '05. Dry Food Factory Godown',
        'factory_godown_id': 'G14',
        'work_center': 'Dry Food Packing(Bakery Puffed Sauce Chanachur & Lachsa)',
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
    def __init__(self):
        self.base_url = os.environ.get("POLOXY_URL", "http://erp.paragon.com.bd").rstrip('/')
        self.username = os.environ.get("POLOXY_USER", "a0162e0019")
        self.password = os.environ.get("POLOXY_PASS", "pal.123456")
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

    def fetch_godown_stock_items(self, godown_name, godown_id, group_name, group_id, date_str=None):
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
            'fromm': '01/10/2022',
            'too': '31/12/2027',
            'cstock_dummy': 'null',
            'cstock_start_dt': date_str,
            'cstock_end_dt': date_str,
            'max1': 'null'
        }

        req = urllib.request.Request(
            stock_url,
            data=urllib.parse.urlencode(form_data).encode('utf-8'),
            headers={**self.headers, 'Referer': f"{self.base_url}/COMMON/r_jsp/godownitemstockreport.jsp"}
        )

        try:
            resp = self.opener.open(req, timeout=30)
            html = resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"[!] Warning: Poloxy stock report timed out or failed: {e}")
            return {}

        items = {}
        trs = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL | re.I)
        for tr in trs:
            cells = [re.sub(r'<[^>]+>', '', cell).strip() for cell in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL)]
            if len(cells) >= 13 and not any(w in cells[0].lower() for w in ['item code', 'total', 'type', 'quantity']):
                code = cells[0].strip()
                name = cells[1].strip()
                uom = cells[2].strip() if len(cells) > 2 else 'Kg'
                closing_stock = clean_num(cells[12])
                
                key = code if code else name.lower()
                items[key] = {
                    'code': code,
                    'name': name,
                    'uom': uom,
                    'closing_stock': closing_stock
                }

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

        # 1. Discover work centers for this branch
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

        # 2. Submit form requesting CSV output
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
            'csv_or_report': 'csv',
            'max1': 'null',
            'selectall': 'ALL' if vals else ''
        }

        rpt_url = f"{self.base_url}/PROCESSINGC/dt_production_summary_rpt"
        req_rpt = urllib.request.Request(
            rpt_url,
            data=urllib.parse.urlencode(form_data).encode('utf-8'),
            headers={**self.headers, 'Referer': f"{self.base_url}/PROCESSINGC/trading_r_jsp/dt_production_summary_rpt.jsp"}
        )
        self.opener.open(req_rpt, timeout=30)

        # 3. Download the generated CSV file
        csv_url = f"{self.base_url}/PROCESSINGC/Production%20Summary.csv"
        # urllib requires escaping space in URL
        encoded_csv_url = f"{self.base_url}/PROCESSINGC/Production%20Summary.csv"
        csv_req = urllib.request.Request(encoded_csv_url, headers=self.headers)
        csv_data = self.opener.open(csv_req, timeout=30).read().decode('utf-8', errors='ignore')

        import csv
        import io
        reader = csv.DictReader(io.StringIO(csv_data))
        output_items = {}

        for row in reader:
            if row.get('Type', '').strip().upper() == 'OUTPUT':
                item_name = row.get('Item', '').strip()
                if not item_name:
                    continue
                pri_qty = clean_num(row.get('Output Primary Unit Qty', 0))
                sec_qty = clean_num(row.get('Output Secondary Unit Qty', 0))
                qty = sec_qty if sec_qty > 0 else pri_qty

                key = item_name.lower().strip()
                if key in output_items:
                    output_items[key]['qty'] += qty
                else:
                    output_items[key] = {
                        'item': item_name,
                        'workcenter': row.get('Workcenter', ''),
                        'qty': qty
                    }

        return output_items

