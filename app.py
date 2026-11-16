import os
import re
import json
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Header, HTTPException, Query, Request as FastAPIRequest
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

APP_VERSION = "13.0.0"
DATABASE_URL = os.getenv("DATABASE_URL", "")
DATA_GOV_IN_API_KEY = os.getenv("DATA_GOV_IN_API_KEY", "")
SHOP_OWNER_TOKEN = os.getenv("SHOP_OWNER_TOKEN", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
AGMARKNET_RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"
AGMARKNET_URL = f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE}"
CONSUMER_AFFAIRS_URL = "https://fcainfoweb.nic.in/Reports/DB/DBprices.aspx"
WARD_GAZETTE_URL = "https://egazette.servantsofknowledge.in/details/in.gazette.andhra_weekly.2026-09-14.546/"

CATEGORIES = [
    "Vegetables", "Fruits", "Rice & Rice Products", "Wheat & Atta", "Dals & Pulses",
    "Edible Oils", "Spices & Masalas", "Sugar & Jaggery", "Salt", "Dairy & Milk",
    "Eggs", "Chicken & Meat", "Fish & Seafood", "Bakery", "Packaged Foods",
    "Snacks", "Biscuits", "Beverages", "Tea & Coffee", "Dry Fruits & Nuts",
    "Baby Food", "Baby Care", "Personal Care", "Skin Care", "Hair Care",
    "Household Cleaning", "Laundry", "Home & Kitchen", "Pooja Items", "Stationery",
    "School Supplies", "Electronics", "Mobile Accessories", "Clothing", "Footwear",
    "Medicines (OTC)", "Pet Supplies", "Kitchen Essentials", "Water & Disposables",
    "Frozen Foods", "Ready to Cook", "Organic Products", "Millets", "Cereals",
    "Poultry Products", "Bakery Ingredients", "Garden Supplies", "Hardware", "Furniture & Home Decor", "Other"
]


def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured in Render environment variables")
    url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=15)


def iso(v):
    return v.isoformat() if v else None


def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS wards (
            ward_number INTEGER PRIMARY KEY, ward_name TEXT, boundary_description TEXT DEFAULT '',
            updated_at TIMESTAMPTZ DEFAULT NOW())""")
        con.execute("ALTER TABLE wards ADD COLUMN IF NOT EXISTS ward_name TEXT")
        con.execute("ALTER TABLE wards ADD COLUMN IF NOT EXISTS boundary_description TEXT DEFAULT ''")
        con.execute("ALTER TABLE wards ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()")

        con.execute("""CREATE TABLE IF NOT EXISTS localities (
            id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, ward_number INTEGER,
            verified BOOLEAN NOT NULL DEFAULT FALSE, source TEXT DEFAULT 'shop-owner submitted',
            created_at TIMESTAMPTZ DEFAULT NOW())""")
        con.execute("ALTER TABLE localities ADD COLUMN IF NOT EXISTS ward_number INTEGER")
        con.execute("ALTER TABLE localities ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE")
        con.execute("ALTER TABLE localities ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'shop-owner submitted'")
        con.execute("CREATE INDEX IF NOT EXISTS localities_name_idx ON localities(LOWER(name))")

        con.execute("""CREATE TABLE IF NOT EXISTS shops (
            id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, shop_type TEXT NOT NULL DEFAULT 'Supermarket',
            locality_id BIGINT, ward_number INTEGER, address TEXT DEFAULT '', phone TEXT DEFAULT '',
            active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT NOW())""")
        for stmt in [
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS shop_type TEXT NOT NULL DEFAULT 'Supermarket'",
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS locality_id BIGINT",
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS ward_number INTEGER",
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS address TEXT DEFAULT ''",
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT ''",
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE",
        ]: con.execute(stmt)

        con.execute("""CREATE TABLE IF NOT EXISTS products (
            id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '1 kg')""")
        con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'Other'")
        con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS unit TEXT NOT NULL DEFAULT '1 kg'")
        con.execute("CREATE INDEX IF NOT EXISTS products_name_category_unit_idx ON products(LOWER(name), LOWER(category), LOWER(unit))")

        con.execute("""CREATE TABLE IF NOT EXISTS prices (
            id BIGSERIAL PRIMARY KEY, shop_id BIGINT NOT NULL, product_id BIGINT NOT NULL,
            price NUMERIC(12,2) NOT NULL CHECK(price > 0), source TEXT NOT NULL DEFAULT 'shop-owner confirmed',
            status TEXT NOT NULL DEFAULT 'Owner-confirmed', confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW())""")
        for stmt in [
            "ALTER TABLE prices ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'shop-owner confirmed'",
            "ALTER TABLE prices ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'Owner-confirmed'",
            "ALTER TABLE prices ADD COLUMN IF NOT EXISTS confirmed BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE prices ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE prices ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
            "ALTER TABLE prices ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
        ]: con.execute(stmt)
        con.execute("UPDATE prices SET expires_at = observed_at + INTERVAL '24 hours' WHERE expires_at IS NULL")
        con.execute("CREATE INDEX IF NOT EXISTS prices_latest_idx ON prices(product_id, shop_id, observed_at DESC)")

        con.execute("""CREATE TABLE IF NOT EXISTS mandi_prices (
            id BIGSERIAL PRIMARY KEY, state TEXT, district TEXT, market TEXT, commodity TEXT,
            variety TEXT, grade TEXT, arrival_date TEXT, min_price NUMERIC(12,2),
            max_price NUMERIC(12,2), modal_price NUMERIC(12,2), source TEXT DEFAULT 'AGMARKNET / data.gov.in',
            fetched_at TIMESTAMPTZ DEFAULT NOW())""")
        con.execute("CREATE INDEX IF NOT EXISTS mandi_lookup_idx ON mandi_prices(district,market,commodity,arrival_date)")

        con.execute("""CREATE TABLE IF NOT EXISTS government_retail_benchmarks (
            id BIGSERIAL PRIMARY KEY, commodity TEXT NOT NULL, unit TEXT, price NUMERIC(12,2),
            report_date TEXT, geographic_scope TEXT DEFAULT 'All India average',
            source TEXT DEFAULT 'Department of Consumer Affairs', fetched_at TIMESTAMPTZ DEFAULT NOW())""")
        con.execute("CREATE INDEX IF NOT EXISTS govt_benchmark_lookup_idx ON government_retail_benchmarks(commodity,report_date)")

        con.execute("""CREATE TABLE IF NOT EXISTS offers (
            id BIGSERIAL PRIMARY KEY, shop_id BIGINT, title TEXT NOT NULL, description TEXT DEFAULT '',
            valid_until TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW(), active BOOLEAN DEFAULT TRUE)""")
        con.execute("ALTER TABLE offers ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
        con.execute("""CREATE TABLE IF NOT EXISTS source_sync (
            source_key TEXT PRIMARY KEY, last_attempt TIMESTAMPTZ, last_success TIMESTAMPTZ,
            status TEXT, message TEXT DEFAULT '')""")

        for n in range(1, 87):
            con.execute("INSERT INTO wards(ward_number,ward_name) VALUES (%s,%s) ON CONFLICT(ward_number) DO NOTHING", (n, f"Ward {n:02d}"))
        con.commit()


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]
    def handle_data(self, data):
        t=data.strip()
        if t: self.parts.append(t)


class TableExtractor(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows=[]; self.row=None; self.cell=None; self.in_table=False
    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.row=[]
        elif tag in ('td','th') and self.row is not None: self.cell=[]
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data.strip())
    def handle_endtag(self, tag):
        if tag in ('td','th') and self.cell is not None and self.row is not None:
            self.row.append(' '.join(x for x in self.cell if x)); self.cell=None
        elif tag == 'tr' and self.row is not None:
            if self.row: self.rows.append(self.row)
            self.row=None


def mark_sync(key, ok, message):
    try:
        with db() as con:
            con.execute("""INSERT INTO source_sync(source_key,last_attempt,last_success,status,message)
                VALUES(%s,NOW(),CASE WHEN %s THEN NOW() ELSE NULL END,%s,%s)
                ON CONFLICT(source_key) DO UPDATE SET last_attempt=NOW(),
                last_success=CASE WHEN %s THEN NOW() ELSE source_sync.last_success END,
                status=EXCLUDED.status,message=EXCLUDED.message""",
                (key, ok, 'OK' if ok else 'ERROR', str(message)[:500], ok))
            con.commit()
    except Exception as exc: print(f"SYNC_STATUS_ERROR {key}: {exc}")


def fetch_agmarknet():
    if not DATA_GOV_IN_API_KEY: raise RuntimeError("DATA_GOV_IN_API_KEY missing")
    records=[]
    # Query current NTR and legacy Krishna separately so the AP-wide 1,000-row cap
    # cannot silently hide Vijayawada-area records behind other districts.
    for district_name in ("NTR", "Krishna"):
        params={"api-key":DATA_GOV_IN_API_KEY,"format":"json","limit":1000,
                "filters[state]":"Andhra Pradesh","filters[district]":district_name}
        req=Request(AGMARKNET_URL+"?"+urlencode(params),headers={"User-Agent":"BezawadaDeals/13.0"})
        with urlopen(req,timeout=30) as response:
            payload=json.loads(response.read().decode('utf-8'))
        records.extend(payload.get('records') or [])
    saved=0
    with db() as con:
        for r in records:
            district=str(r.get('district') or '').strip()
            if district and district.casefold() not in ('ntr','krishna'): continue
            existing=con.execute("""SELECT id FROM mandi_prices WHERE COALESCE(district,'')=%s AND COALESCE(market,'')=%s
                AND COALESCE(commodity,'')=%s AND COALESCE(variety,'')=%s AND COALESCE(arrival_date,'')=%s LIMIT 1""",
                (district,r.get('market') or '',r.get('commodity') or '',r.get('variety') or '',r.get('arrival_date') or '')).fetchone()
            values=(r.get('state'),district,r.get('market'),r.get('commodity'),r.get('variety'),r.get('grade'),
                    r.get('arrival_date'),r.get('min_price') or None,r.get('max_price') or None,r.get('modal_price') or None)
            if existing:
                con.execute("""UPDATE mandi_prices SET state=%s,district=%s,market=%s,commodity=%s,variety=%s,grade=%s,
                    arrival_date=%s,min_price=%s,max_price=%s,modal_price=%s,fetched_at=NOW() WHERE id=%s""",values+(existing['id'],))
            else:
                con.execute("""INSERT INTO mandi_prices(state,district,market,commodity,variety,grade,arrival_date,min_price,max_price,modal_price,fetched_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())""",values)
            saved+=1
        con.commit()
    mark_sync('agmarknet', True, f"Fetched {len(records)} NTR/Krishna mandi rows; stored {saved}")
    return saved


def fetch_consumer_affairs():
    req=Request(CONSUMER_AFFAIRS_URL, headers={"User-Agent":"Mozilla/5.0 BezawadaDeals/13.0"})
    with urlopen(req, timeout=30) as response: html=response.read().decode('utf-8','ignore')
    text_parser=TextExtractor(); text_parser.feed(html); all_text=' '.join(text_parser.parts)
    date_match=re.search(r"Date\s*:\s*(\d{2}/\d{2}/\d{4})", all_text, re.I)
    report_date=date_match.group(1) if date_match else datetime.now().strftime('%d/%m/%Y')
    table=TableExtractor(); table.feed(html)
    saved=0
    with db() as con:
        for row in table.rows:
            if len(row)<3: continue
            commodity=row[0].strip()
            if commodity.lower() in ('commodities','commodity','') or 'daily average' in commodity.lower(): continue
            unit=row[1].strip() if len(row)>1 else ''
            raw=re.sub(r'[^0-9.]','',row[2].replace(',',''))
            try: price=float(raw)
            except Exception: continue
            if not commodity or price<=0: continue
            existing=con.execute("""SELECT id FROM government_retail_benchmarks WHERE LOWER(commodity)=LOWER(%s)
                AND report_date=%s AND geographic_scope='All India average' LIMIT 1""",(commodity,report_date)).fetchone()
            if existing:
                con.execute("UPDATE government_retail_benchmarks SET unit=%s,price=%s,fetched_at=NOW() WHERE id=%s",(unit,price,existing['id']))
            else:
                con.execute("""INSERT INTO government_retail_benchmarks(commodity,unit,price,report_date,geographic_scope,fetched_at)
                    VALUES(%s,%s,%s,%s,'All India average',NOW())""",(commodity,unit,price,report_date))
            saved+=1
        con.commit()
    if saved==0: raise RuntimeError("Consumer Affairs page format changed or no price rows were parsed")
    mark_sync('consumer_affairs', True, f"Saved {saved} All-India benchmark rows for {report_date}; not Vijayawada shop prices")
    return saved


def fetch_ward_gazette():
    req=Request(WARD_GAZETTE_URL, headers={"User-Agent":"Mozilla/5.0 BezawadaDeals/13.0"})
    with urlopen(req, timeout=30) as response: html=response.read().decode('utf-8','ignore')
    parser=TextExtractor(); parser.feed(html); text='\n'.join(parser.parts)
    pattern=re.compile(r"BOUNDARIES OF WARD NO\s*[:.]?\s*(\d{1,2})(.*?)(?=BOUNDARIES OF WARD NO\s*[:.]?\s*\d{1,2}|$)",re.I|re.S)
    updates=[]
    for match in pattern.finditer(text):
        number=int(match.group(1)); body=re.sub(r'Page\s+\d+\s+of\s+87\s+\d+',' ',match.group(2),flags=re.I)
        body=re.sub(r'\s+',' ',body).strip()
        if 1<=number<=86 and body: updates.append((number,body[:8000]))
    if len(updates)<70: raise RuntimeError(f"Gazette parser found only {len(updates)} of 86 ward boundaries")
    with db() as con:
        for number,body in updates:
            con.execute("UPDATE wards SET boundary_description=%s,updated_at=NOW() WHERE ward_number=%s",(body,number))
        con.commit()
    mark_sync('vmc_ward_gazette',True,f"Imported {len(updates)} official ward boundary descriptions")
    return len(updates)


async def background_sync():
    counters={'agmarknet':0,'consumer':0,'ward':0}
    while True:
        try:
            await asyncio.to_thread(fetch_agmarknet)
        except Exception as exc: mark_sync('agmarknet',False,str(exc))
        try:
            await asyncio.to_thread(fetch_consumer_affairs)
        except Exception as exc: mark_sync('consumer_affairs',False,str(exc))
        # Gazette is static; fetch at startup and then once per 7 days.
        if counters['ward']==0:
            try: await asyncio.to_thread(fetch_ward_gazette)
            except Exception as exc: mark_sync('vmc_ward_gazette',False,str(exc))
        counters['ward']+=1
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(init_db)
    task=asyncio.create_task(background_sync())
    yield
    task.cancel()

app=FastAPI(title='BezawadaDeals',version=APP_VERSION,lifespan=lifespan)
templates=Jinja2Templates(directory='templates')

@app.get('/',response_class=HTMLResponse)
def home(request: FastAPIRequest):
    return templates.TemplateResponse(request=request,name='index.html',context={'version':APP_VERSION})

@app.get('/health')
def health():
    try:
        with db() as con: con.execute('SELECT 1')
        return {'ok':True,'version':APP_VERSION,'database':'connected'}
    except Exception as exc: return {'ok':False,'version':APP_VERSION,'database_error':str(exc)}

@app.get('/api/categories')
def categories(): return {'count':len(CATEGORIES),'categories':CATEGORIES}

@app.get('/api/wards')
def wards():
    with db() as con: rows=con.execute('SELECT ward_number,ward_name,boundary_description,updated_at FROM wards ORDER BY ward_number').fetchall()
    for row in rows: row['updated_at']=iso(row['updated_at'])
    return {'count':len(rows),'wards':rows}

@app.get('/api/localities')
def localities(ward_number:Optional[int]=None):
    sql='SELECT id,name,ward_number,verified,source FROM localities'; args=[]
    if ward_number: sql+=' WHERE ward_number=%s'; args.append(ward_number)
    sql+=' ORDER BY name'
    with db() as con: rows=con.execute(sql,args).fetchall()
    return {'count':len(rows),'localities':rows}

@app.get('/api/shops')
def shops(area:Optional[str]=None,ward_number:Optional[int]=None,shop_type:Optional[str]=None):
    sql="""SELECT s.id,s.name,s.shop_type,s.address,s.phone,s.ward_number,l.name AS area
        FROM shops s LEFT JOIN localities l ON l.id=s.locality_id WHERE s.active=TRUE"""; args=[]
    if area: sql+=' AND LOWER(l.name)=LOWER(%s)'; args.append(area)
    if ward_number: sql+=' AND s.ward_number=%s'; args.append(ward_number)
    if shop_type: sql+=' AND LOWER(s.shop_type)=LOWER(%s)'; args.append(shop_type)
    sql+=' ORDER BY l.name,s.name'
    with db() as con: rows=con.execute(sql,args).fetchall()
    return {'count':len(rows),'shops':rows}

@app.get('/api/unified-price-tracker')
@app.get('/api/price-tracker')
def unified_price_tracker(category:Optional[str]=None,area:Optional[str]=None,ward_number:Optional[int]=None,
                          product:Optional[str]=None,source_type:Optional[str]=None,sort:str='low',include_expired:bool=True):
    sql="""SELECT DISTINCT ON (s.id,pr.id) p.id AS price_id,pr.name AS product,pr.category,pr.unit,p.price,
        s.name AS shop,s.shop_type,s.address,s.ward_number,l.name AS area,p.source,
        CASE WHEN p.expires_at < NOW() THEN 'Expired' ELSE p.status END AS status,
        p.observed_at,p.expires_at
        FROM prices p JOIN products pr ON pr.id=p.product_id JOIN shops s ON s.id=p.shop_id
        LEFT JOIN localities l ON l.id=s.locality_id WHERE s.active=TRUE AND p.confirmed=TRUE"""; args=[]
    if category: sql+=' AND LOWER(pr.category)=LOWER(%s)'; args.append(category)
    if area: sql+=' AND LOWER(l.name)=LOWER(%s)'; args.append(area)
    if ward_number: sql+=' AND s.ward_number=%s'; args.append(ward_number)
    if product: sql+=' AND pr.name ILIKE %s'; args.append(f'%{product}%')
    if source_type: sql+=' AND LOWER(s.shop_type)=LOWER(%s)'; args.append(source_type)
    if not include_expired: sql+=' AND p.expires_at>=NOW()'
    sql+=' ORDER BY s.id,pr.id,p.observed_at DESC'
    with db() as con: rows=con.execute(sql,args).fetchall()
    rows.sort(key=lambda x: float(x['price']),reverse=(sort=='high'))
    for r in rows:
        r['price']=float(r['price']); r['observed_at']=iso(r['observed_at']); r['expires_at']=iso(r['expires_at'])
    return {'count':len(rows),'prices':rows,'categories':CATEGORIES,'updated_at':datetime.now(timezone.utc).isoformat()}

@app.get('/api/market-prices')
def market_prices(commodity:Optional[str]=None,limit:int=Query(300,ge=1,le=1000)):
    sql='SELECT * FROM mandi_prices'; args=[]
    if commodity: sql+=' WHERE commodity ILIKE %s'; args.append(f'%{commodity}%')
    sql+=' ORDER BY arrival_date DESC,market LIMIT %s'; args.append(limit)
    with db() as con: rows=con.execute(sql,args).fetchall()
    for r in rows:
        for k in ('min_price','max_price','modal_price'):
            if r[k] is not None:r[k]=float(r[k])
        r['fetched_at']=iso(r['fetched_at'])
    return {'count':len(rows),'prices':rows,'unit':'₹ per quintal','source':'AGMARKNET / data.gov.in; wholesale mandi prices'}

@app.get('/api/official-retail-prices')
def official_retail_prices():
    with db() as con: rows=con.execute("""SELECT commodity,unit,price,report_date,geographic_scope,source,fetched_at
        FROM government_retail_benchmarks ORDER BY commodity""").fetchall()
    for r in rows: r['price']=float(r['price']); r['fetched_at']=iso(r['fetched_at'])
    return {'count':len(rows),'prices':rows,'note':'Department of Consumer Affairs All-India average retail benchmark; not Vijayawada or shop-specific.'}

@app.get('/api/offers')
def offers(area:Optional[str]=None):
    sql="""SELECT o.id,o.title,o.description,o.valid_until,s.name AS shop,s.shop_type,l.name AS area,s.ward_number
        FROM offers o LEFT JOIN shops s ON s.id=o.shop_id LEFT JOIN localities l ON l.id=s.locality_id
        WHERE o.active=TRUE AND (o.valid_until IS NULL OR o.valid_until>NOW())"""; args=[]
    if area:sql+=' AND LOWER(l.name)=LOWER(%s)';args.append(area)
    sql+=' ORDER BY o.created_at DESC'
    with db() as con: rows=con.execute(sql,args).fetchall()
    for r in rows:r['valid_until']=iso(r['valid_until'])
    return {'count':len(rows),'offers':rows}

@app.get('/api/status')
def status():
    with db() as con:
        counts={t:con.execute(f'SELECT COUNT(*) AS n FROM {t}').fetchone()['n'] for t in
                ('wards','localities','shops','products','prices','mandi_prices','government_retail_benchmarks','offers')}
        sync=con.execute('SELECT source_key,last_attempt,last_success,status,message FROM source_sync ORDER BY source_key').fetchall()
    for s in sync:s['last_attempt']=iso(s['last_attempt']);s['last_success']=iso(s['last_success'])
    return {'version':APP_VERSION,'counts':counts,'sources':sync,'page_refresh_minutes':5}

@app.post('/api/shop/price-update')
def shop_price_update(payload:dict,x_shop_token:str=Header(default='')):
    if not SHOP_OWNER_TOKEN or x_shop_token!=SHOP_OWNER_TOKEN:raise HTTPException(401,'Invalid SHOP_OWNER_TOKEN')
    required=('shop_name','shop_type','area','ward_number','product','category','unit','price','confirmed')
    missing=[k for k in required if k not in payload or payload[k] in (None,'')]
    if missing:raise HTTPException(400,'Missing: '+', '.join(missing))
    if payload['confirmed'] is not True:raise HTTPException(400,'Confirm the current price before submitting')
    try: price=float(payload['price']); ward=int(payload['ward_number'])
    except Exception:raise HTTPException(400,'Price and ward number must be valid numbers')
    if price<=0 or not 1<=ward<=86:raise HTTPException(400,'Price must be positive and ward must be 1–86')
    shop_type=str(payload['shop_type']).strip()
    if shop_type not in ('Supermarket','Rythu Bazaar','Local Shop'):raise HTTPException(400,'Invalid shop type')
    category=str(payload['category']).strip()
    if category not in CATEGORIES:raise HTTPException(400,'Choose one of the 50 categories')
    area=str(payload['area']).strip(); shop_name=str(payload['shop_name']).strip(); product=str(payload['product']).strip(); unit=str(payload['unit']).strip()
    if not all((area,shop_name,product,unit)):raise HTTPException(400,'Area, shop, product and unit are required')
    with db() as con:
        loc=con.execute('SELECT id FROM localities WHERE LOWER(name)=LOWER(%s) ORDER BY id LIMIT 1',(area,)).fetchone()
        if loc:
            con.execute("UPDATE localities SET ward_number=%s WHERE id=%s AND (ward_number IS NULL OR ward_number=%s)",(ward,loc['id'],ward))
        else:
            loc=con.execute("""INSERT INTO localities(name,ward_number,verified,source)
                VALUES(%s,%s,FALSE,'shop-owner submitted') RETURNING id""",(area,ward)).fetchone()
        sh=con.execute('SELECT id FROM shops WHERE LOWER(name)=LOWER(%s) AND shop_type=%s AND ward_number=%s',(shop_name,shop_type,ward)).fetchone()
        if sh:
            shop_id=sh['id'];con.execute('UPDATE shops SET locality_id=%s,address=%s,phone=%s,active=TRUE WHERE id=%s',(loc['id'],payload.get('address',''),payload.get('phone',''),shop_id))
        else:
            shop_id=con.execute("""INSERT INTO shops(name,shop_type,locality_id,ward_number,address,phone)
                VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",(shop_name,shop_type,loc['id'],ward,payload.get('address',''),payload.get('phone',''))).fetchone()['id']
        prod=con.execute('SELECT id FROM products WHERE LOWER(name)=LOWER(%s) AND LOWER(category)=LOWER(%s) AND LOWER(unit)=LOWER(%s) ORDER BY id LIMIT 1',(product,category,unit)).fetchone()
        if not prod:
            prod=con.execute('INSERT INTO products(name,category,unit) VALUES(%s,%s,%s) RETURNING id',(product,category,unit)).fetchone()
        con.execute("""INSERT INTO prices(shop_id,product_id,price,source,status,confirmed,observed_at,expires_at)
            VALUES(%s,%s,%s,'Shop-owner submitted','Owner-confirmed',TRUE,NOW(),NOW()+INTERVAL '24 hours')""",(shop_id,prod['id'],price))
        con.commit()
    return {'ok':True,'message':'Price saved as Owner-confirmed for 24 hours. Admin verification is separate.','price':price}

@app.post('/api/admin/prices/{price_id}/verify')
def verify_price(price_id:int,x_admin_token:str=Header(default='')):
    if not ADMIN_TOKEN or x_admin_token!=ADMIN_TOKEN:raise HTTPException(401,'Invalid ADMIN_TOKEN')
    with db() as con:
        cur=con.execute("UPDATE prices SET status='Verified' WHERE id=%s AND confirmed=TRUE",(price_id,))
        if cur.rowcount==0:raise HTTPException(404,'Price record not found')
        con.commit()
    return {'ok':True,'price_id':price_id,'status':'Verified'}

@app.post('/api/admin/refresh')
async def admin_refresh(x_admin_token:str=Header(default='')):
    if not ADMIN_TOKEN or x_admin_token!=ADMIN_TOKEN:raise HTTPException(401,'Invalid ADMIN_TOKEN')
    results={}
    for key,func in [('agmarknet',fetch_agmarknet),('consumer_affairs',fetch_consumer_affairs),('vmc_ward_gazette',fetch_ward_gazette)]:
        try:results[key]={'ok':True,'saved':await asyncio.to_thread(func)}
        except Exception as exc:mark_sync(key,False,str(exc));results[key]={'ok':False,'error':str(exc)}
    return results

@app.post('/api/admin/offers')
def admin_add_offer(payload:dict,x_admin_token:str=Header(default='')):
    if not ADMIN_TOKEN or x_admin_token!=ADMIN_TOKEN: raise HTTPException(401,'Invalid ADMIN_TOKEN')
    title=str(payload.get('title') or '').strip()
    if not title: raise HTTPException(400,'Offer title is required')
    shop_id=payload.get('shop_id')
    try: shop_id=int(shop_id) if shop_id not in (None,'') else None
    except Exception: raise HTTPException(400,'shop_id must be a number')
    valid_until=payload.get('valid_until') or None
    with db() as con:
        row=con.execute("""INSERT INTO offers(shop_id,title,description,valid_until,active)
            VALUES(%s,%s,%s,%s,TRUE) RETURNING id""",(shop_id,title,str(payload.get('description') or ''),valid_until)).fetchone()
        con.commit()
    return {'ok':True,'offer_id':row['id']}

@app.get('/api/official-sources')
def official_sources():
    return {'sources':[
        {'name':'Department of Consumer Affairs','type':'Daily retail benchmark','automatic':True,'scope':'All India average','note':'22+ essential commodities; benchmark only, not Vijayawada shop prices.','url':CONSUMER_AFFAIRS_URL},
        {'name':'AGMARKNET / data.gov.in','type':'Daily wholesale mandi prices','automatic':bool(DATA_GOV_IN_API_KEY),'scope':'NTR / legacy Krishna district records where returned','note':'Prices are generally ₹/quintal; not retail prices.','url':AGMARKNET_URL},
        {'name':'Supermarket prices','type':'Local retail prices','automatic':False,'scope':'Shop and area specific','note':'Requires owner-confirmed updates or a formal store feed.'},
        {'name':'Rythu Bazaar prices','type':'Local retail prices','automatic':False,'scope':'Bazaar and area specific','note':'Requires authorized bazaar/operator price updates; mandi prices are not substituted.'},
        {'name':'VMC ward boundaries','type':'Official ward delimitation','automatic':True,'scope':'86 wards','note':'Boundary descriptions are fetched from the September 14, 2026 Gazette archive transcript; actual locality-to-ward assignment must be confirmed against the boundary.'}
    ]}
