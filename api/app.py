"""
STEG Observatory — Complete REST API with FastAPI
Three dashboards: Citizens / Field Workers / Supervisors

Run:
    pip install fastapi uvicorn psycopg2-binary python-multipart bcrypt python-jose
    export NEON_DATABASE_URL="postgresql://..."
    uvicorn api.app_fastapi:app --reload

Swagger UI: http://localhost:8000/docs
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import uuid
import logging
import sys
import os
from datetime import datetime, date, timedelta, time
from decimal import Decimal
from typing import Optional, List, Dict, Any
from functools import wraps
from contextlib import contextmanager
import secrets

from fastapi import FastAPI, Depends, HTTPException, status, Query, Header, Request, Response, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, validator, EmailStr
from starlette.responses import JSONResponse
import bcrypt
from jose import JWTError, jwt
from passlib.context import CryptContext

# ── Path setup so imports from project root work ────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import get_cursor, get_conn
from utils.logger import get_logger
from agents.agent_orchestrator import AgentOrchestrator
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")


# ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="STEG Observatory API",
    description="Citizen, Field Worker and Supervisor dashboards for grid cut management",
    version="2.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = get_logger(__name__)

# ── Authentication setup ─────────────────────────────────────
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)

# ── JSON encoder for Decimal / date / datetime / time ───────────────
class SafeEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, time):
            return o.isoformat()
        if isinstance(o, timedelta):
            return o.total_seconds()
        return super().default(o)

def json_safe(obj: Any) -> Any:
    """Convert any object with Decimal/date/time to JSON-safe Python types."""
    return json.loads(json.dumps(obj, cls=SafeEncoder))

# ── Database helpers ─────────────────────────────────────────
_column_cache: Dict[str, bool] = {}

def _column_exists(schema: str, table: str, column: str) -> bool:
    key = f"{schema}.{table}.{column}"
    if key in _column_cache:
        return _column_cache[key]
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
            (schema, table, column),
        )
        exists = cur.fetchone() is not None
    _column_cache[key] = exists
    return exists

def _normalize_region(region: str) -> str:
    return region.strip().lower() if region and isinstance(region, str) else region

def _get_season(month: int) -> str:
    return {12: "winter", 1: "winter", 2: "winter",
            3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer",
            9: "autumn", 10: "autumn", 11: "autumn"}.get(month, "unknown")

# ── Authentication Models ────────────────────────────────────
class LoginRequest(BaseModel):
    worker_id: str
    password: Optional[str] = None
    demo_mode: bool = True

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    worker_id: str
    full_name: str
    role: str
    district: str
    gouvernorat: Optional[str] = None

class WorkerCreate(BaseModel):
    worker_id: str
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    district: str
    gouvernorat: Optional[str] = None
    role: str = "worker"
    password: Optional[str] = None

# ── Authentication Functions ─────────────────────────────────
def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

async def get_current_worker(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """Get current worker from JWT token"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        worker_id: str = payload.get("sub")
        if worker_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, worker_id, full_name, role, district, gouvernorat, is_active
                FROM public.workers
                WHERE worker_id = %s AND is_active = TRUE
            """, (worker_id,))
            worker = cur.fetchone()
            if not worker:
                raise HTTPException(status_code=401, detail="Worker not found or inactive")
        
        return dict(worker)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

def require_role(allowed_roles: List[str]):
    async def dependency(worker: dict = Depends(get_current_worker)):
        if worker["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{worker['role']}' not allowed. Required: {allowed_roles}"
            )
        return worker
    return dependency

async def optional_auth(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials:
        try:
            return await get_current_worker(credentials)
        except HTTPException:
            return None
    return None

# ── Helper for alert messages ────────────────────────────────
def _alert_message(level: str, lang: str) -> str:
    msgs = {
        "GREEN":  {"ar": "الشبكة تعمل بشكل طبيعي",
                   "fr": "Le réseau fonctionne normalement",
                   "en": "Network is operating normally"},
        "YELLOW": {"ar": "تحذير: احتمال انقطاع التيار الكهربائي",
                   "fr": "Attention : des coupures sont possibles",
                   "en": "Warning: Power cuts possible"},
        "ORANGE": {"ar": "تحذير عالي: عجز في الإنتاج",
                   "fr": "Alerte haute : déficit de production détecté",
                   "en": "High alert: Production deficit detected"},
        "RED":    {"ar": "طارئ: انقطاعات مخطط لها في بعض المناطق",
                   "fr": "Urgence : coupures planifiées dans certaines régions",
                   "en": "Emergency: Planned cuts in some regions"},
    }
    return msgs.get(level, msgs["GREEN"])[lang]

# ════════════════════════════════════════════════════════════════
# AUTHENTICATION ENDPOINTS
# ════════════════════════════════════════════════════════════════

@app.post("/api/auth/login", response_model=LoginResponse, tags=["Authentication"])
async def login(request: LoginRequest):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT id, worker_id, full_name, role, district, gouvernorat, 
                   password_hash, is_active
            FROM public.workers
            WHERE worker_id = %s
        """, (request.worker_id,))
        worker = cur.fetchone()
        
        if not worker:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        if not worker["is_active"]:
            raise HTTPException(status_code=401, detail="Account disabled")
        
        if request.demo_mode:
            pass
        elif request.password:
            if not worker["password_hash"] or not verify_password(request.password, worker["password_hash"]):
                raise HTTPException(status_code=401, detail="Invalid credentials")
        elif not request.demo_mode:
            raise HTTPException(status_code=401, detail="Password required")
        
        cur.execute(
            "UPDATE public.workers SET last_login = NOW() WHERE id = %s",
            (worker["id"],)
        )
    
    access_token = create_access_token(data={"sub": worker["worker_id"], "role": worker["role"]})
    
    return LoginResponse(
        access_token=access_token,
        worker_id=worker["worker_id"],
        full_name=worker["full_name"],
        role=worker["role"],
        district=worker["district"],
        gouvernorat=worker.get("gouvernorat")
    )

@app.post("/api/auth/logout", tags=["Authentication"])
async def logout(worker: dict = Depends(get_current_worker)):
    return {"status": "success", "message": "Logged out"}

@app.get("/api/auth/me", tags=["Authentication"])
async def get_me(worker: dict = Depends(get_current_worker)):
    return json_safe(worker)

@app.post("/api/auth/register-worker", tags=["Authentication"])
async def register_worker(
    worker_data: WorkerCreate,
    admin: dict = Depends(require_role(["admin"]))
):
    with get_cursor() as cur:
        cur.execute("SELECT id FROM public.workers WHERE worker_id = %s OR email = %s",
                    (worker_data.worker_id, worker_data.email))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="Worker ID or email already exists")
        
        password_hash = hash_password(worker_data.password) if worker_data.password else None
        
        cur.execute("""
            INSERT INTO public.workers 
            (worker_id, full_name, email, phone, district, gouvernorat, role, password_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            worker_data.worker_id,
            worker_data.full_name,
            worker_data.email,
            worker_data.phone,
            worker_data.district,
            worker_data.gouvernorat,
            worker_data.role,
            password_hash
        ))
        worker_id = cur.fetchone()[0]
    
    return {"status": "created", "worker_id": worker_data.worker_id, "db_id": worker_id}

# ════════════════════════════════════════════════════════════════
# HEALTH
# ════════════════════════════════════════════════════════════════

@app.get("/api/health", tags=["Health"])
async def health():
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1")
        return {"status": "ok", "db": "connected", "ts": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ════════════════════════════════════════════════════════════════
# CITIZEN ENDPOINTS (Public)
# ════════════════════════════════════════════════════════════════

@app.get("/api/citizen/grid-status", tags=["Citizen"])
async def citizen_grid_status(worker: Optional[dict] = Depends(optional_auth)):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            WITH current AS (
                SELECT total_production_gwh, year, month
                FROM gold.monthly_production
                ORDER BY year DESC, month DESC
                LIMIT 1
            ),
            hist_avg AS (
                SELECT AVG(total_production_gwh) AS avg_gwh,
                       (SELECT month FROM current) AS ref_month
                FROM gold.monthly_production
                WHERE month = (SELECT month FROM current)
                  AND year  < (SELECT year  FROM current)
            )
            SELECT
                c.total_production_gwh  AS current_gwh,
                h.avg_gwh               AS normal_gwh,
                ROUND(((h.avg_gwh - c.total_production_gwh) / NULLIF(h.avg_gwh, 0)) * 100, 2)
                                        AS deficit_pct,
                c.year, c.month
            FROM current c, hist_avg h
        """)
        prod = cur.fetchone()

        cur.execute("""
            SELECT alert_level, status, scheduled_date
            FROM public.cut_plans
            WHERE status IN ('approved', 'pending')
            ORDER BY created_at DESC
            LIMIT 1
        """)
        plan = cur.fetchone()

    deficit_pct = float(prod["deficit_pct"] or 0) if prod else 0
    alert_level = "GREEN"
    if plan:
        alert_level = plan["alert_level"]
    elif deficit_pct > 15:
        alert_level = "RED"
    elif deficit_pct > 8:
        alert_level = "ORANGE"
    elif deficit_pct > 3:
        alert_level = "YELLOW"

    return json_safe({
        "alert_level":       alert_level,
        "deficit_pct":       deficit_pct,
        "current_gwh":       float(prod["current_gwh"]) if prod else None,
        "normal_gwh":        float(prod["normal_gwh"]) if prod else None,
        "latest_cut_status": plan["status"] if plan else None,
        "message_ar":        _alert_message(alert_level, "ar"),
        "message_fr":        _alert_message(alert_level, "fr"),
        "message_en":        _alert_message(alert_level, "en"),
    })

@app.get("/api/citizen/map", tags=["Citizen"])
async def citizen_map(worker: Optional[dict] = Depends(optional_auth)):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                d.gouvernorat,
                d.region,
                AVG(d.latitude)  AS lat,
                AVG(d.longitude) AS lon,
                COUNT(*)         AS district_count
            FROM silver.districts d
            WHERE d.latitude IS NOT NULL
            GROUP BY d.gouvernorat, d.region
            ORDER BY d.region
        """)
        districts = cur.fetchall()

        cur.execute("""
            SELECT region,
                   COUNT(*)            AS cut_count_90d,
                   MAX(cut_date)       AS last_cut,
                   SUM(duration_minutes) AS total_minutes_90d
            FROM silver.cut_history
            WHERE cut_date >= NOW() - INTERVAL '90 days'
            GROUP BY region
        """)
        cut_stats = {r["region"]: r for r in cur.fetchall()}

        cur.execute("""
            SELECT AVG(cut_count) AS avg_cuts
            FROM (
                SELECT region, COUNT(*) AS cut_count
                FROM silver.cut_history
                WHERE cut_date >= NOW() - INTERVAL '90 days'
                GROUP BY region
            ) t
        """)
        avg_row = cur.fetchone()
        avg_cuts = float(avg_row["avg_cuts"] or 1) if avg_row else 1

        cur.execute("""
            SELECT regions, scheduled_date, scheduled_start, estimated_duration_hours, status
            FROM public.v_active_cut_plans
            WHERE scheduled_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 7
            ORDER BY scheduled_date
        """)
        planned = cur.fetchall()

    planned_regions = {}
    for p in planned:
        for reg in (p["regions"] or []):
            planned_regions[reg] = {
                "scheduled_date":  p["scheduled_date"],
                "scheduled_start": str(p["scheduled_start"]) if p["scheduled_start"] else None,
                "estimated_hours": float(p["estimated_duration_hours"] or 0),
                "status":          p["status"],
            }

    result = []
    for d in districts:
        region = d["region"]
        stats  = cut_stats.get(region, {})
        cuts   = int(stats.get("cut_count_90d") or 0)
        ratio  = cuts / avg_cuts if avg_cuts > 0 else 0

        if ratio > 1.5:
            risk = "high"
        elif ratio > 0.8:
            risk = "medium"
        else:
            risk = "low"

        fairness_score = max(0, min(100, round(100 - (ratio - 1) * 50)))

        result.append(json_safe({
            "region":           region,
            "gouvernorat":      d["gouvernorat"],
            "lat":              float(d["lat"] or 0),
            "lon":              float(d["lon"] or 0),
            "district_count":   int(d["district_count"]),
            "cut_count_90d":    cuts,
            "last_cut":         stats.get("last_cut"),
            "total_minutes_90d": int(stats.get("total_minutes_90d") or 0),
            "risk_level":       risk,
            "fairness_score":   fairness_score,
            "planned_cut":      planned_regions.get(region),
        }))

    return result

@app.get("/api/citizen/my-zone", tags=["Citizen"])
async def citizen_my_zone(
    region: str = Query(..., description="Region name (e.g., sfax)"),
    worker: Optional[dict] = Depends(optional_auth)
):
    region = region.strip().lower()
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT id, plan_ref, scheduled_date, scheduled_start,
                   estimated_duration_hours, alert_level
            FROM public.cut_plans
            WHERE status = 'approved'
              AND %s = ANY(regions)
              AND scheduled_date >= CURRENT_DATE
            ORDER BY scheduled_date
            LIMIT 1
        """, (region,))
        next_cut = cur.fetchone()

        cur.execute("""
            SELECT cut_date, duration_minutes, season
            FROM silver.cut_history
            WHERE region = %s
            ORDER BY cut_date DESC
            LIMIT 5
        """, (region,))
        history = cur.fetchall()

        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM silver.cut_history
                 WHERE region = %s AND cut_date >= NOW() - INTERVAL '90 days') AS my_cuts,
                (SELECT AVG(cnt) FROM (
                    SELECT COUNT(*) AS cnt FROM silver.cut_history
                    WHERE cut_date >= NOW() - INTERVAL '90 days'
                    GROUP BY region
                ) t) AS avg_cuts
        """, (region,))
        fair = cur.fetchone()

        cur.execute("""
            SELECT COUNT(*) AS open_reports
            FROM public.citizen_reports
            WHERE region = %s AND status = 'open'
        """, (region,))
        reports = cur.fetchone()

    my_cuts  = int(fair["my_cuts"] or 0) if fair else 0
    avg_cuts = float(fair["avg_cuts"] or 1) if fair else 1
    ratio    = my_cuts / avg_cuts if avg_cuts > 0 else 1
    fairness_score = max(0, min(100, round(100 - (ratio - 1) * 50)))

    return json_safe({
        "region":        region,
        "fairness_score": fairness_score,
        "my_cuts_90d":   my_cuts,
        "national_avg":  round(avg_cuts, 1),
        "next_cut":      next_cut,
        "cut_history":   list(history),
        "open_reports":  int(reports["open_reports"] or 0) if reports else 0,
    })

class CitizenReportRequest(BaseModel):
    region: str
    gouvernorat: Optional[str] = None
    report_type: str
    description: Optional[str] = None
    phone: Optional[str] = None

class CitizenSubscribeRequest(BaseModel):
    region: str
    gouvernorat: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    lang: str = "ar"
    notify_sms: bool = True
    notify_email: bool = False

@app.post("/api/citizen/report", status_code=201, tags=["Citizen"])
async def citizen_report(
    report: CitizenReportRequest,
    worker: Optional[dict] = Depends(optional_auth)
):
    region_norm = _normalize_region(report.region)
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO public.citizen_reports
              (region, gouvernorat, report_type, description, phone)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (
            region_norm,
            report.gouvernorat,
            report.report_type,
            report.description,
            report.phone,
        ))
        row_id = cur.fetchone()[0]
    return {"status": "created", "report_id": row_id}

@app.post("/api/citizen/subscribe", status_code=201, tags=["Citizen"])
async def citizen_subscribe(
    prefs: CitizenSubscribeRequest,
    worker: Optional[dict] = Depends(optional_auth)
):
    region_norm = _normalize_region(prefs.region)
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO public.citizen_preferences
              (phone, email, region, gouvernorat, lang, notify_sms, notify_email)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            prefs.phone,
            prefs.email,
            region_norm,
            prefs.gouvernorat,
            prefs.lang,
            prefs.notify_sms,
            prefs.notify_email,
        ))
        pref_id = cur.fetchone()[0]
    return {"status": "subscribed", "preference_id": pref_id}

@app.get("/api/citizen/cut-history", tags=["Citizen"])
async def citizen_cut_history(
    region: Optional[str] = Query(None, description="Region name"),
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    worker: Optional[dict] = Depends(optional_auth)
):
    where = "WHERE cut_date >= NOW() - INTERVAL '%s days'"
    params = [days]
    if region:
        where += " AND region = %s"
        params.append(region.lower())

    with get_cursor(dict_cursor=True) as cur:
        cur.execute(f"""
            SELECT id, region, cut_date, duration_minutes, reason, season
            FROM silver.cut_history
            {where}
            ORDER BY cut_date DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = cur.fetchall()
        cur.execute(f"SELECT COUNT(*) FROM silver.cut_history {where}", params)
        total_row = cur.fetchone()
        total = total_row['count'] if total_row else 0
    return {"total": total, "rows": json_safe(list(rows))}

# ════════════════════════════════════════════════════════════════
# WORKER ENDPOINTS
# ════════════════════════════════════════════════════════════════

@app.get("/api/worker/work-orders", tags=["Worker"])
async def worker_work_orders(
    worker: dict = Depends(require_role(["worker", "supervisor", "admin"])),
    region: Optional[str] = Query(None, description="Region name")
):
    worker_id = worker["worker_id"]
    region_filter = _normalize_region(region) if region else None

    with get_cursor(dict_cursor=True) as cur:
        if region_filter:
            cur.execute("""
                SELECT
                    cp.id, cp.plan_ref, cp.scheduled_date, cp.scheduled_start,
                    cp.estimated_duration_hours, cp.alert_level,
                    cp.ai_explanation, cp.deficit_pct,
                    ce.id               AS exec_id,
                    ce.status           AS exec_status,
                    ce.actual_start, ce.actual_end
                FROM public.cut_plans cp
                LEFT JOIN public.cut_executions ce
                    ON ce.cut_plan_id = cp.id AND ce.worker_id = %s
                WHERE cp.status = 'approved'
                  AND %s = ANY(cp.regions)
                ORDER BY cp.scheduled_date, cp.scheduled_start
            """, (worker_id, region_filter))
        else:
            cur.execute("""
                SELECT
                    cp.id, cp.plan_ref, cp.scheduled_date, cp.scheduled_start,
                    cp.estimated_duration_hours, cp.alert_level,
                    cp.ai_explanation, cp.deficit_pct,
                    ce.id               AS exec_id,
                    ce.status           AS exec_status,
                    ce.actual_start, ce.actual_end
                FROM public.cut_plans cp
                LEFT JOIN public.cut_executions ce
                    ON ce.cut_plan_id = cp.id AND ce.worker_id = %s
                WHERE cp.status = 'approved'
                ORDER BY cp.scheduled_date, cp.scheduled_start
                LIMIT 50
            """, (worker_id,))
        orders = cur.fetchall()

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE ce.status = 'pending')     AS open_count,
                COUNT(*) FILTER (WHERE ce.status = 'in_progress') AS active_count,
                COUNT(*) FILTER (WHERE ce.status = 'restored'
                                   AND ce.actual_end::date = CURRENT_DATE) AS done_today,
                ROUND(AVG(ce.actual_duration_minutes)::numeric, 0) AS avg_restore_min
            FROM public.cut_executions ce
            WHERE ce.worker_id = %s
        """, (worker_id,))
        kpi = cur.fetchone()

    return json_safe({
        "worker_id": worker_id,
        "worker_name": worker["full_name"],
        "district":  worker.get("district"),
        "orders":    list(orders),
        "kpis": {
            "open_count":       int(kpi["open_count"]    or 0) if kpi else 0,
            "active_count":     int(kpi["active_count"]  or 0) if kpi else 0,
            "done_today":       int(kpi["done_today"]    or 0) if kpi else 0,
            "avg_restore_min":  float(kpi["avg_restore_min"] or 0) if kpi else 0,
        }
    })

class WorkerExecuteRequest(BaseModel):
    cut_plan_id: int
    notes: Optional[str] = None

@app.post("/api/worker/execute", status_code=201, tags=["Worker"])
async def worker_execute(
    body: WorkerExecuteRequest,
    worker: dict = Depends(require_role(["worker", "supervisor"]))
):
    now = datetime.now()
    with get_cursor() as cur:
        cur.execute("SELECT id, status, scheduled_start, regions FROM public.cut_plans WHERE id = %s",
                    (body.cut_plan_id,))
        plan = cur.fetchone()
        if not plan:
            raise HTTPException(404, "cut_plan not found")
        if plan[1] != "approved":
            raise HTTPException(409, f"Plan is '{plan[1]}', not approved")

        plan_regions = [r.lower() for r in plan[3]] if plan[3] else []
        worker_region = _normalize_region(worker.get("district", ""))
        
        region_match = False
        for pr in plan_regions:
            if pr in worker_region or worker_region in pr:
                region_match = True
                break
        
        if not region_match:
            raise HTTPException(409, f"Worker's region '{worker_region}' not in cut plan regions")

        cur.execute("""
            INSERT INTO public.cut_executions
              (cut_plan_id, worker_id, worker_db_id, worker_name, district, region, gouvernorat,
               status, actual_start, planned_start, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'in_progress', %s, %s, %s)
            RETURNING id
        """, (
            body.cut_plan_id,
            worker["worker_id"],
            worker["id"],
            worker["full_name"],
            worker["district"],
            worker_region,
            worker.get("gouvernorat"),
            now,
            plan[2],
            body.notes,
        ))
        exec_id = cur.fetchone()[0]

    return {"status": "execution_started", "execution_id": exec_id,
            "actual_start": now.isoformat()}

class WorkerRestoreRequest(BaseModel):
    execution_id: int
    notes: Optional[str] = None

@app.post("/api/worker/restore", tags=["Worker"])
async def worker_restore(
    body: WorkerRestoreRequest,
    worker: dict = Depends(require_role(["worker", "supervisor"]))
):
    now = datetime.now()
    with get_cursor() as cur:
        cur.execute("""
            SELECT id, cut_plan_id, actual_start, region, status, worker_id
            FROM public.cut_executions WHERE id = %s
        """, (body.execution_id,))
        exec_row = cur.fetchone()
        if not exec_row:
            raise HTTPException(404, "execution not found")
        
        if exec_row[5] != worker["worker_id"] and worker["role"] != "supervisor":
            raise HTTPException(403, "Not authorized to restore this execution")
        
        if exec_row[4] == "restored":
            raise HTTPException(409, "Already restored")

        start_time = exec_row[2]
        duration_min = int((now - start_time).total_seconds() / 60) if start_time else None

        cur.execute("""
            UPDATE public.cut_executions
            SET status = 'restored',
                actual_end = %s,
                actual_duration_minutes = %s,
                notes = COALESCE(%s, notes),
                updated_at = NOW()
            WHERE id = %s
        """, (now, duration_min, body.notes, body.execution_id))

        region = exec_row[3]
        plan_id = exec_row[1]
        season = _get_season(now.month)
        
        cur.execute("""
            INSERT INTO silver.cut_history
              (region, cut_date, duration_minutes, season, worker_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (region, now.date(), duration_min, season, worker["worker_id"]))

        # Send restoration notification to citizens in the affected region
        send_restoration_notification(body.execution_id, region, duration_min)

        cur.execute("""
            SELECT COUNT(*) = 0 FROM public.cut_executions
            WHERE cut_plan_id = %s AND status != 'restored'
        """, (plan_id,))
        all_restored_row = cur.fetchone()
        all_restored = all_restored_row[0] if all_restored_row else False
        if all_restored:
            cur.execute("""
                UPDATE public.cut_plans
                SET status = 'executed', updated_at = NOW()
                WHERE id = %s AND status != 'executed'
            """, (plan_id,))

    return {"status": "restored", "duration_minutes": duration_min,
            "actual_end": now.isoformat()}

class WorkerIncidentRequest(BaseModel):
    execution_id: Optional[int] = None
    region: str
    triggering_event: str
    root_cause: Optional[str] = None
    duration_minutes: Optional[int] = None
    power_loss_mw: Optional[float] = None
    recovery_time_minutes: Optional[int] = None

@app.post("/api/worker/incident", status_code=201, tags=["Worker"])
async def worker_incident(
    body: WorkerIncidentRequest,
    worker: dict = Depends(require_role(["worker", "supervisor"]))
):
    incident_id = f"INC-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6].upper()}"
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO bronze.incidents
              (incident_id, incident_date, triggering_event, root_cause,
               duration_minutes, power_loss_mw, recovery_time_minutes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            incident_id,
            date.today(),
            body.triggering_event,
            body.root_cause,
            body.duration_minutes,
            body.power_loss_mw,
            body.recovery_time_minutes,
        ))
        inc_id = cur.fetchone()[0]

        if body.execution_id:
            cur.execute("""
                UPDATE public.cut_executions
                SET status = 'incident', notes = COALESCE(notes || ' | ', '') || %s
                WHERE id = %s
            """, (f"Incident filed: {incident_id}", body.execution_id))

    return {"status": "created", "incident_id": incident_id, "db_id": inc_id}

@app.get("/api/worker/district-map", tags=["Worker"])
async def worker_district_map(
    gouvernorat: Optional[str] = Query(None, description="Governorate name"),
    worker: dict = Depends(require_role(["worker", "supervisor", "admin"]))
):
    if not gouvernorat and worker.get("gouvernorat"):
        gouvernorat = worker["gouvernorat"]
    elif not gouvernorat:
        gouvernorat = worker.get("district", "")
    
    gouvernorat = gouvernorat.strip().lower()
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                d.district, d.gouvernorat, d.region,
                d.latitude, d.longitude, d.type,
                ce.status  AS exec_status,
                ce.actual_start, ce.actual_end
            FROM silver.districts d
            LEFT JOIN public.cut_executions ce
                ON ce.district = LOWER(d.district)
               AND ce.cut_plan_id IN (
                   SELECT id FROM public.cut_plans
                   WHERE status IN ('approved','executed')
                     AND scheduled_date = CURRENT_DATE
               )
            WHERE LOWER(d.gouvernorat) = %s
              AND d.latitude IS NOT NULL
        """, (gouvernorat,))
        pins = cur.fetchall()
    return json_safe(list(pins))

@app.get("/api/worker/my-stats", tags=["Worker"])
async def worker_my_stats(
    worker: dict = Depends(require_role(["worker", "supervisor", "admin"]))
):
    worker_id = worker["worker_id"]
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'restored')        AS total_executed,
                COUNT(*) FILTER (WHERE status = 'incident')        AS total_incidents,
                COUNT(*) FILTER (WHERE actual_start::date = CURRENT_DATE) AS today,
                ROUND(AVG(actual_duration_minutes)::numeric, 1)    AS avg_duration_min,
                MIN(actual_duration_minutes)                        AS min_duration_min,
                MAX(actual_duration_minutes)                        AS max_duration_min,
                ROUND(AVG(variance_minutes)::numeric, 1)           AS avg_variance_min
            FROM public.cut_executions
            WHERE worker_id = %s
        """, (worker_id,))
        stats = cur.fetchone()

        cur.execute("""
            SELECT ce.district, ce.region, ce.status,
                   ce.actual_start, ce.actual_end, ce.actual_duration_minutes,
                   cp.plan_ref, cp.alert_level
            FROM public.cut_executions ce
            JOIN public.cut_plans cp ON cp.id = ce.cut_plan_id
            WHERE ce.worker_id = %s
            ORDER BY ce.created_at DESC
            LIMIT 10
        """, (worker_id,))
        recent = cur.fetchall()

    return json_safe({
        "worker_id": worker_id,
        "worker_name": worker["full_name"],
        "stats":     dict(stats) if stats else {},
        "recent":    list(recent),
    })

# ════════════════════════════════════════════════════════════════
# SUPERVISOR ENDPOINTS
# ════════════════════════════════════════════════════════════════

@app.get("/api/supervisor/dashboard", tags=["Supervisor"])
async def supervisor_dashboard(worker: dict = Depends(require_role(["supervisor", "admin"]))):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            WITH latest AS (
                SELECT total_production_gwh, avg_renewable_share_pct,
                       avg_steg_share_pct, growth_rate_pct, year, month
                FROM gold.monthly_production ORDER BY year DESC, month DESC LIMIT 1
            ),
            prev AS (
                SELECT total_production_gwh AS prev_gwh
                FROM gold.monthly_production ORDER BY year DESC, month DESC LIMIT 1 OFFSET 1
            )
            SELECT l.*, p.prev_gwh,
                   ROUND(((l.total_production_gwh - p.prev_gwh) / NULLIF(p.prev_gwh,0))*100,2)
                       AS mom_change_pct
            FROM latest l, prev p
        """)
        prod = cur.fetchone()

        cur.execute("SELECT COUNT(*) AS pending_count FROM public.cut_plans WHERE status = 'pending'")
        pending = cur.fetchone()

        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM silver.cut_history
                 WHERE cut_date >= NOW() - INTERVAL '90 days') AS total_cuts_90d,
                (SELECT COUNT(DISTINCT region) FROM silver.cut_history
                 WHERE cut_date >= NOW() - INTERVAL '90 days') AS regions_affected
        """)
        fair_base = cur.fetchone()

        cur.execute("""
            WITH counts AS (
                SELECT region, COUNT(*) AS n
                FROM silver.cut_history
                WHERE cut_date >= NOW() - INTERVAL '90 days'
                GROUP BY region
            ),
            ranked AS (
                SELECT n, ROW_NUMBER() OVER (ORDER BY n) AS rk,
                       COUNT(*) OVER () AS total_n,
                       SUM(n) OVER ()  AS total_sum
                FROM counts
            )
            SELECT
                ROUND(
                    (2.0 * SUM(rk * n) - (MAX(total_n) + 1) * MAX(total_sum))
                    / NULLIF(MAX(total_n) * MAX(total_sum), 0)
                , 3) AS gini
            FROM ranked
        """)
        gini_row = cur.fetchone()
        gini = float(gini_row["gini"] or 0) if gini_row else 0

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE incident_date >= NOW() - INTERVAL '30 days')
                    AS incidents_30d,
                ROUND(AVG(duration_minutes)::numeric, 0) AS avg_duration_min,
                ROUND(AVG(power_loss_mw)::numeric, 2)    AS avg_power_loss_mw
            FROM bronze.incidents
        """)
        incidents = cur.fetchone()

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'executed')  AS executed,
                COUNT(*) FILTER (WHERE status = 'approved')  AS still_pending,
                COUNT(*) FILTER (WHERE status = 'rejected')  AS rejected,
                COUNT(*) AS total
            FROM public.cut_plans
            WHERE created_at >= NOW() - INTERVAL '30 days'
        """)
        plans_kpi = cur.fetchone()

        cur.execute("SELECT alert_level FROM public.cut_plans ORDER BY created_at DESC LIMIT 1")
        alert_row = cur.fetchone()

        cur.execute("""
            WITH counts AS (
                SELECT region, COUNT(*) AS n
                FROM silver.cut_history
                WHERE cut_date >= NOW() - INTERVAL '90 days'
                GROUP BY region
            ),
            stats AS (
                SELECT AVG(n) AS avg_n, STDDEV(n) AS std_n FROM counts
            )
            SELECT c.region, c.n,
                   CASE WHEN c.n > s.avg_n + s.std_n THEN 'over_cut'
                        WHEN c.n < s.avg_n - s.std_n THEN 'under_cut'
                        ELSE 'normal' END AS fairness_flag
            FROM counts c, stats s
            WHERE c.n > s.avg_n + s.std_n OR c.n < s.avg_n - s.std_n
            ORDER BY c.n DESC
        """)
        flagged = cur.fetchall()

    return json_safe({
        "alert_level":     (alert_row["alert_level"] if alert_row else "GREEN"),
        "production": {
            "current_gwh":     float(prod["total_production_gwh"] or 0) if prod else 0,
            "prev_gwh":        float(prod["prev_gwh"] or 0) if prod else 0,
            "mom_change_pct":  float(prod["mom_change_pct"] or 0) if prod else 0,
            "renewable_share": float(prod["avg_renewable_share_pct"] or 0) if prod else 0,
            "steg_share":      float(prod["avg_steg_share_pct"] or 0) if prod else 0,
            "growth_rate":     float(prod["growth_rate_pct"] or 0) if prod else 0,
        },
        "fairness": {
            "gini_coefficient":   gini,
            "gini_label":         "fair" if gini < 0.3 else "moderate" if gini < 0.5 else "unfair",
            "total_cuts_90d":     int(fair_base["total_cuts_90d"] or 0) if fair_base else 0,
            "regions_affected":   int(fair_base["regions_affected"] or 0) if fair_base else 0,
            "flagged_regions":    list(flagged),
        },
        "plans": {
            "pending_approval": int(pending["pending_count"] or 0) if pending else 0,
            "executed_30d":     int(plans_kpi["executed"] or 0) if plans_kpi else 0,
            "rejected_30d":     int(plans_kpi["rejected"] or 0) if plans_kpi else 0,
            "total_30d":        int(plans_kpi["total"] or 0) if plans_kpi else 0,
            "execution_rate_pct": round(
                int(plans_kpi["executed"] or 0) /
                max(int(plans_kpi["total"] or 1), 1) * 100, 1
            ) if plans_kpi else 0,
        },
        "incidents": {
            "last_30d":       int(incidents["incidents_30d"] or 0) if incidents else 0,
            "avg_duration_min": float(incidents["avg_duration_min"] or 0) if incidents else 0,
            "avg_power_loss_mw": float(incidents["avg_power_loss_mw"] or 0) if incidents else 0,
        },
    })

@app.get("/api/supervisor/proposals", tags=["Supervisor"])
async def supervisor_proposals(
    status: str = Query("pending", regex="^(pending|approved|rejected|all)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    where = "" if status == "all" else "WHERE status = %s"
    params = [] if status == "all" else [status]
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(f"""
            SELECT id, plan_ref, proposed_at, proposed_by, status,
                   regions, protected_regions, scheduled_date, scheduled_start,
                   estimated_duration_hours, deficit_pct, alert_level,
                   ai_explanation, cut_advisor_output, fairness_output, forecast_output,
                   approved_by, approved_at, rejected_by, rejection_reason,
                   notified_citizens, notification_sent_at
            FROM public.cut_plans
            {where}
            ORDER BY proposed_at DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = cur.fetchall()
        cur.execute(f"SELECT COUNT(*) FROM public.cut_plans {where}", params)
        total_row = cur.fetchone()
        total = total_row['count'] if total_row else 0
    
    # Convert time objects to strings for JSON serialization
    proposals = []
    for row in rows:
        proposal = dict(row)
        if proposal.get('scheduled_start'):
            proposal['scheduled_start'] = str(proposal['scheduled_start'])
        proposals.append(proposal)
    
    return json_safe({"total": total, "proposals": proposals})

class ApproveRequest(BaseModel):
    cut_plan_id: int
    scheduled_date: Optional[date] = None
    scheduled_start: Optional[str] = None
    estimated_duration_hours: Optional[float] = None

def send_email_notification(to_email: str, subject: str, body_text: str, body_html: str = None):
    """Send email notification"""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning("Email not configured. Skipping email send.")
        return False
    
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_email
        
        # Attach plain text version
        part1 = MIMEText(body_text, "plain")
        msg.attach(part1)
        
        # Attach HTML version if provided
        if body_html:
            part2 = MIMEText(body_html, "html")
            msg.attach(part2)
        
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"Email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Email failed to {to_email}: {e}")
        return False



def _dispatch_notifications(plan_id: int, regions: list):
    """Send actual email/SMS notifications to citizens"""
    try:
        with get_cursor(dict_cursor=True) as cur:
            # Get plan details
            cur.execute("""
                SELECT scheduled_date, scheduled_start, estimated_duration_hours, plan_ref
                FROM public.cut_plans WHERE id = %s
            """, (plan_id,))
            plan = cur.fetchone()
            if not plan:
                return
            
            total_sent = 0
            total_failed = 0
            
            for region in (regions or []):
                region_norm = _normalize_region(region)
                
                # Get all subscribed citizens for this region
                cur.execute("""
                    SELECT phone, email, lang, notify_sms, notify_email
                    FROM public.citizen_preferences
                    WHERE region = %s AND (notify_sms = TRUE OR notify_email = TRUE)
                """, (region_norm,))
                citizens = cur.fetchall()
                
                success_count = 0
                failed_count = 0
                
                for citizen in citizens:
                    # Choose language
                    lang = citizen['lang']
                    msg_ar = (
                        f"⚠️ تنبيه من STEG: سيتأثر {region_norm} بانقطاع التيار الكهربائي\n"
                        f"📅 التاريخ: {plan['scheduled_date']}\n"
                        f"🕐 الوقت: {plan['scheduled_start'] or 'غير محدد'}\n"
                        f"⏱️ المدة: {plan['estimated_duration_hours'] or '?'} ساعات\n"
                        f"📋 المرجع: {plan['plan_ref']}\n"
                        f"نعتذر عن الإزعاج."
                    )
                    msg_fr = (
                        f"⚠️ Alerte STEG: {region_norm} sera affecté par une coupure d'électricité\n"
                        f"📅 Date: {plan['scheduled_date']}\n"
                        f"🕐 Heure: {plan['scheduled_start'] or 'non spécifiée'}\n"
                        f"⏱️ Durée: {plan['estimated_duration_hours'] or '?'} heures\n"
                        f"📋 Réf: {plan['plan_ref']}\n"
                        f"Nous nous excusons pour la gêne occasionnée."
                    )
                    
                    msg_text = msg_fr if lang == 'fr' else msg_ar
                    msg_html = f"<html><body><pre>{msg_text}</pre></body></html>"
                    
                    
                    
                    # Send Email if enabled and email exists
                    if citizen.get('notify_email') and citizen.get('email'):
                        subject = f"STEG Alert: Power cut scheduled in {region_norm}"
                        if send_email_notification(citizen['email'], subject, msg_text, msg_html):
                            success_count += 1
                        else:
                            failed_count += 1
                
                total_sent += success_count
                total_failed += failed_count
                
                # Log notification attempt
                status = 'sent' if success_count > 0 else 'failed'
                cur.execute("""
                    INSERT INTO public.notification_log
                      (cut_plan_id, region, channel, recipient_count, 
                       message_ar, message_fr, status, error_detail)
                    VALUES (%s, %s, 'email+sms', %s, %s, %s, %s, %s)
                """, (
                    plan_id, region_norm, success_count, msg_ar, msg_fr, 
                    status, f"Failed: {failed_count}" if failed_count > 0 else None
                ))
            
            # Mark notifications as sent
            cur.execute("""
                UPDATE public.cut_plans
                SET notified_citizens = TRUE, notification_sent_at = NOW()
                WHERE id = %s
            """, (plan_id,))
            
        logger.info(f"Notifications sent for plan {plan_id}: {total_sent} successful, {total_failed} failed")
        
    except Exception as e:
        logger.error(f"Notification dispatch failed: {e}")
def send_restoration_notification(execution_id: int, region: str, duration_minutes: int):
    """Send notification when power is restored"""
    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT phone, email, lang, notify_sms, notify_email
                FROM public.citizen_preferences
                WHERE region = %s
            """, (region,))
            citizens = cur.fetchall()
            
            msg_fr = f"✅ STEG: L'électricité a été rétablie à {region}. Durée de la coupure: {duration_minutes} minutes."
            msg_ar = f"✅ STEG: تم إعادة التيار الكهربائي في {region}. مدة الانقطاع: {duration_minutes} دقيقة."
            
            for citizen in citizens:
                msg = msg_fr if citizen['lang'] == 'fr' else msg_ar
                if citizen.get('notify_email') and citizen.get('email'):
                    send_email_notification(citizen['email'], f"Power Restored - {region}", msg, f"<html><body><p>{msg}</p></body></html>")
            
            logger.info(f"Restoration notifications sent for region {region}")
    except Exception as e:
        logger.error(f"Restoration notification failed: {e}")

@app.post("/api/supervisor/approve", tags=["Supervisor"])
async def supervisor_approve(
    body: ApproveRequest,
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    now = datetime.now()
    with get_cursor() as cur:
        cur.execute("SELECT id, status, regions FROM public.cut_plans WHERE id = %s",
                    (body.cut_plan_id,))
        plan = cur.fetchone()
        if not plan:
            raise HTTPException(404, "Plan not found")
        if plan[1] != "pending":
            raise HTTPException(409, f"Plan is '{plan[1]}', not pending")
        
        scheduled_start_time = None
        if body.scheduled_start:
            try:
                scheduled_start_time = datetime.strptime(body.scheduled_start, "%H:%M").time()
            except ValueError:
                raise HTTPException(400, "scheduled_start must be in HH:MM format")
        
        cur.execute("""
            UPDATE public.cut_plans SET
                status           = 'approved',
                approved_by      = %s,
                approved_by_worker_id = %s,
                approved_at      = %s,
                scheduled_date   = COALESCE(%s, scheduled_date),
                scheduled_start  = COALESCE(%s, scheduled_start),
                estimated_duration_hours = COALESCE(%s, estimated_duration_hours),
                updated_at       = NOW()
            WHERE id = %s
        """, (
            worker["worker_id"],
            worker["id"],
            now,
            body.scheduled_date,
            scheduled_start_time,
            body.estimated_duration_hours,
            body.cut_plan_id,
        ))
        
        cur.execute("""
            INSERT INTO public.agent_run_log
              (run_id, triggered_by, triggered_by_worker_id, alert_level, cut_plan_id)
            VALUES (%s, %s, %s, 'approved', %s)
        """, (str(uuid.uuid4()), worker["worker_id"], worker["id"], body.cut_plan_id))
    
    _dispatch_notifications(body.cut_plan_id, plan[2])
    
    return {
        "status": "approved", 
        "approved_by": worker["worker_id"], 
        "approved_at": now.isoformat(),
        "plan_id": body.cut_plan_id
    }

class RejectRequest(BaseModel):
    cut_plan_id: int
    rejection_reason: str

@app.post("/api/supervisor/reject", tags=["Supervisor"])
async def supervisor_reject(
    body: RejectRequest,
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    now = datetime.now()
    with get_cursor() as cur:
        cur.execute("SELECT id, status FROM public.cut_plans WHERE id = %s",
                    (body.cut_plan_id,))
        plan = cur.fetchone()
        if not plan:
            raise HTTPException(404, "Plan not found")
        if plan[1] != "pending":
            raise HTTPException(409, f"Plan is '{plan[1]}', not pending")
        
        cur.execute("""
            UPDATE public.cut_plans SET
                status           = 'rejected',
                rejected_by      = %s,
                rejected_by_worker_id = %s,
                rejected_at      = %s,
                rejection_reason = %s,
                updated_at       = NOW()
            WHERE id = %s
        """, (worker["worker_id"], worker["id"], now, body.rejection_reason, body.cut_plan_id))
    
    return {
        "status": "rejected", 
        "rejected_by": worker["worker_id"], 
        "rejected_at": now.isoformat(),
        "reason": body.rejection_reason,
        "plan_id": body.cut_plan_id
    }

class TriggerAgentsRequest(BaseModel):
    generate_synthetic: bool = False

@app.post("/api/supervisor/trigger-agents", tags=["Supervisor"])
async def supervisor_trigger_agents(
    body: TriggerAgentsRequest = TriggerAgentsRequest(),
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    run_id = str(uuid.uuid4())
    started = datetime.now()
    try:
        orchestrator = AgentOrchestrator()
        results = orchestrator.run_all_agents(
            generate_synthetic=body.generate_synthetic
        )
        alert_level = results.get("alert_level", "GREEN")
        cut_list     = [r.lower() for r in results["agents"]["cut_advisor"].get("cut_priority_list", [])]
        protect_list = [r.lower() for r in results["agents"]["cut_advisor"].get("protected_regions", [])]
        explanation  = results["agents"]["cut_advisor"].get("reasoning", "")
        deficit_pct  = results["agents"]["cut_advisor"].get(
            "current_production_status", {}).get("deficit_percentage", 0)

        plan_id = None
        if alert_level in ("RED", "ORANGE") and cut_list:
            plan_ref = f"CUT-{datetime.now().strftime('%Y%m%d')}-{run_id[:6].upper()}"
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO public.cut_plans
                      (plan_ref, proposed_by, proposed_by_worker_id, regions, protected_regions, status,
                       alert_level, deficit_pct, ai_explanation,
                       cut_advisor_output, fairness_output, forecast_output)
                    VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    plan_ref,
                    worker["worker_id"],
                    worker["id"],
                    cut_list,
                    protect_list,
                    alert_level,
                    float(deficit_pct or 0),
                    explanation,
                    json.dumps(json_safe(results["agents"]["cut_advisor"])),
                    json.dumps(json_safe(results["agents"]["fairness"])),
                    json.dumps(json_safe(results["agents"]["forecast"])),
                ))
                plan_id = cur.fetchone()[0]

        finished = datetime.now()
        with get_cursor() as cur:
            cur.execute("""
                INSERT INTO public.agent_run_log
                  (run_id, triggered_by, triggered_by_worker_id, started_at, finished_at, alert_level,
                   cut_advisor_status, fairness_status, forecast_status, cut_plan_id, full_output)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                run_id,
                worker["worker_id"],
                worker["id"],
                started, finished,
                alert_level,
                results["agents"]["cut_advisor"].get("status"),
                results["agents"]["fairness"].get("status"),
                results["agents"]["forecast"].get("status"),
                plan_id,
                json.dumps(json_safe(results)),
            ))

        return {
            "run_id":       run_id,
            "alert_level":  alert_level,
            "cut_plan_id":  plan_id,
            "cut_regions":  cut_list,
            "deficit_pct":  deficit_pct,
            "duration_sec": round((finished - started).total_seconds(), 2),
            "summary":      results.get("executive_summary", ""),
        }
    except Exception as e:
        logger.exception("Agent run failed")
        raise HTTPException(500, detail=str(e))

@app.get("/api/supervisor/production-forecast", tags=["Supervisor"])
async def supervisor_production_forecast(
    years: int = Query(3, ge=1, le=10),
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT year, month, month_name, total_production_gwh,
                   avg_renewable_share_pct, avg_steg_share_pct, growth_rate_pct
            FROM gold.monthly_production
            WHERE year >= EXTRACT(YEAR FROM NOW())::INT - %s
            ORDER BY year, month
        """, (years,))
        historical = cur.fetchall()
        cur.execute("""
            SELECT full_output->>'agents' AS agents_json, started_at
            FROM public.agent_run_log
            WHERE full_output IS NOT NULL
            ORDER BY started_at DESC
            LIMIT 1
        """)
        log_row = cur.fetchone()
        cur.execute("""
            SELECT season, ROUND(AVG(total_production_gwh)::numeric, 2) AS avg_gwh,
                   ROUND(MIN(total_production_gwh)::numeric, 2) AS min_gwh,
                   ROUND(MAX(total_production_gwh)::numeric, 2) AS max_gwh
            FROM gold.seasonal_production
            GROUP BY season
        """)
        seasonal = cur.fetchall()
        cur.execute("""
            SELECT year, month,
                   ROUND(((total_production_gwh -
                    LAG(total_production_gwh) OVER (ORDER BY year, month))
                    / NULLIF(LAG(total_production_gwh) OVER (ORDER BY year, month), 0) * 100
                   )::numeric, 2) AS mom_pct
            FROM gold.monthly_production
            WHERE year >= EXTRACT(YEAR FROM NOW())::INT - 1
            ORDER BY year, month
        """)
        growth = cur.fetchall()

    forecast_data = None
    if log_row and log_row["agents_json"]:
        try:
            agents = json.loads(log_row["agents_json"])
            forecast_data = agents.get("forecast", {})
        except Exception:
            pass

    return json_safe({
        "historical":    list(historical),
        "seasonal":      list(seasonal),
        "growth_trend":  list(growth),
        "forecast":      forecast_data,
        "last_run_at":   log_row["started_at"].isoformat() if log_row else None,
    })

@app.get("/api/supervisor/fairness-heatmap", tags=["Supervisor"])
async def supervisor_fairness_heatmap(
    days: int = Query(90, ge=1, le=365),
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            WITH cut_counts AS (
                SELECT
                    ch.region,
                    COUNT(*)                          AS cut_count,
                    SUM(ch.duration_minutes)          AS total_minutes,
                    ROUND(AVG(ch.duration_minutes)::numeric, 1) AS avg_minutes,
                    MAX(ch.cut_date)                  AS last_cut
                FROM silver.cut_history ch
                WHERE ch.cut_date >= NOW() - INTERVAL '1 day' * %s
                GROUP BY ch.region
            ),
            stats AS (
                SELECT AVG(cut_count)    AS avg_n,
                       STDDEV(cut_count) AS std_n
                FROM cut_counts
            ),
            solar AS (
                SELECT region,
                       SUM(total_installations) AS solar_installs,
                       SUM(co2_saved_tons)       AS co2_saved
                FROM gold.solar_impact
                GROUP BY region
            )
            SELECT
                cc.region,
                cc.cut_count,
                cc.total_minutes,
                cc.avg_minutes,
                cc.last_cut,
                COALESCE(sol.solar_installs, 0) AS solar_installs,
                COALESCE(sol.co2_saved, 0)       AS co2_saved,
                CASE
                    WHEN cc.cut_count > st.avg_n + st.std_n THEN 'over_cut'
                    WHEN cc.cut_count < st.avg_n - st.std_n THEN 'under_cut'
                    ELSE 'normal'
                END AS fairness_flag,
                ROUND(st.avg_n::numeric, 1)  AS national_avg,
                ROUND(st.std_n::numeric, 1)  AS national_std
            FROM cut_counts cc
            CROSS JOIN stats st
            LEFT JOIN solar sol ON sol.region = cc.region
            ORDER BY cc.cut_count DESC
        """, (days,))
        rows = cur.fetchall()
        cur.execute("""
            WITH counts AS (
                SELECT region, COUNT(*) AS n
                FROM silver.cut_history
                WHERE cut_date >= NOW() - INTERVAL '1 day' * %s
                GROUP BY region
            ), ranked AS (
                SELECT n, ROW_NUMBER() OVER (ORDER BY n) AS rk,
                       COUNT(*) OVER () AS total_n, SUM(n) OVER () AS total_sum
                FROM counts
            )
            SELECT ROUND(
                (2.0 * SUM(rk * n) - (MAX(total_n)+1) * MAX(total_sum))
                / NULLIF(MAX(total_n) * MAX(total_sum), 0)
            , 3) AS gini FROM ranked
        """, (days,))
        gini_row = cur.fetchone()
    return json_safe({
        "days":   days,
        "gini":   float(gini_row["gini"] or 0) if gini_row else 0,
        "regions": list(rows),
    })

@app.get("/api/supervisor/workers", tags=["Supervisor"])
async def supervisor_workers(
    role: Optional[str] = Query(None, regex="^(worker|supervisor|admin)$"),
    active_only: bool = Query(True),
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    with get_cursor(dict_cursor=True) as cur:
        query = """
            SELECT id, worker_id, full_name, email, phone, district, gouvernorat, 
                   role, is_active, created_at, last_login
            FROM public.workers
            WHERE 1=1
        """
        params = []
        if role:
            query += " AND role = %s"
            params.append(role)
        if active_only:
            query += " AND is_active = TRUE"
        query += " ORDER BY role, full_name"
        
        cur.execute(query, params)
        workers_list = cur.fetchall()
    return json_safe(list(workers_list))

@app.get("/api/supervisor/worker-stats/{worker_id}", tags=["Supervisor"])
async def supervisor_worker_stats(
    worker_id: str,
    days: int = Query(30, ge=1, le=365),
    supervisor: dict = Depends(require_role(["supervisor", "admin"]))
):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT 
                ce.worker_id,
                w.full_name,
                COUNT(*) AS total_tasks,
                COUNT(*) FILTER (WHERE ce.status = 'restored') AS completed_tasks,
                COUNT(*) FILTER (WHERE ce.status = 'incident') AS incidents,
                ROUND(AVG(ce.actual_duration_minutes)::numeric, 1) AS avg_duration_min,
                ROUND(AVG(ce.variance_minutes)::numeric, 1) AS avg_variance_min,
                MIN(ce.actual_start) AS first_task,
                MAX(ce.actual_start) AS last_task
            FROM public.cut_executions ce
            JOIN public.workers w ON w.worker_id = ce.worker_id
            WHERE ce.worker_id = %s
              AND ce.created_at >= NOW() - INTERVAL '1 day' * %s
            GROUP BY ce.worker_id, w.full_name
        """, (worker_id, days))
        stats = cur.fetchone()
        
        cur.execute("""
            SELECT 
                ce.id, ce.cut_plan_id, cp.plan_ref, ce.status,
                ce.actual_start, ce.actual_end, ce.actual_duration_minutes,
                ce.district, ce.region, ce.notes
            FROM public.cut_executions ce
            JOIN public.cut_plans cp ON cp.id = ce.cut_plan_id
            WHERE ce.worker_id = %s
            ORDER BY ce.created_at DESC
            LIMIT 20
        """, (worker_id,))
        recent = cur.fetchall()
        
    return json_safe({
        "worker_id": worker_id,
        "stats": dict(stats) if stats else {},
        "recent_tasks": list(recent)
    })

class ProposalRequest(BaseModel):
    """Request model for submitting a cut plan proposal"""
    regions: List[str] = Field(..., description="List of regions to cut")
    protected_regions: List[str] = Field(default=[], description="Regions to protect from cuts")
    scheduled_date: Optional[date] = Field(None, description="Proposed cut date")
    scheduled_start: Optional[str] = Field(None, description="Proposed start time (HH:MM)")
    estimated_duration_hours: Optional[float] = Field(None, ge=0.5, le=24, description="Estimated duration in hours")
    reason: Optional[str] = Field(None, description="Reason for the cut proposal")
    ai_explanation: Optional[str] = Field(None, description="AI-generated explanation")

@app.post("/api/proposals/submit", status_code=201, tags=["Proposals"])
async def submit_proposal(
    proposal: ProposalRequest,
    worker: dict = Depends(require_role(["worker", "supervisor", "admin"]))
):
    plan_ref = f"CUT-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6].upper()}"
    
    deficit_pct = 0.0
    alert_level = "GREEN"
    
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO public.cut_plans (
                plan_ref, proposed_by, proposed_by_worker_id, regions, protected_regions,
                scheduled_date, scheduled_start, estimated_duration_hours,
                deficit_pct, alert_level, ai_explanation, status, proposed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', NOW())
            RETURNING id
        """, (
            plan_ref,
            worker["worker_id"],
            worker["id"],
            proposal.regions,
            proposal.protected_regions,
            proposal.scheduled_date,
            proposal.scheduled_start,
            proposal.estimated_duration_hours,
            deficit_pct,
            alert_level,
            proposal.ai_explanation or proposal.reason
        ))
        plan_id = cur.fetchone()[0]
    
    logger.info(f"Proposal {plan_ref} submitted by {worker['worker_id']}")
    
    return {
        "status": "submitted",
        "plan_id": plan_id,
        "plan_ref": plan_ref,
        "message": f"Proposal submitted successfully. Waiting for supervisor approval."
    }

@app.get("/api/proposals/my-submissions", tags=["Proposals"])
async def my_submissions(
    worker: dict = Depends(require_role(["worker", "supervisor", "admin"])),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT id, plan_ref, status, regions, protected_regions,
                   scheduled_date, scheduled_start, estimated_duration_hours,
                   deficit_pct, alert_level, ai_explanation,
                   approved_by, approved_at, rejected_by, rejection_reason,
                   proposed_at, created_at
            FROM public.cut_plans
            WHERE proposed_by_worker_id = %s
            ORDER BY proposed_at DESC
            LIMIT %s OFFSET %s
        """, (worker["id"], limit, offset))
        submissions = cur.fetchall()
        
        cur.execute("""
            SELECT COUNT(*) FROM public.cut_plans
            WHERE proposed_by_worker_id = %s
        """, (worker["id"],))
        total_row = cur.fetchone()
        total = total_row['count'] if total_row else 0
    
    return json_safe({
        "total": total,
        "submissions": list(submissions)
    })

@app.post("/api/proposals/agent-auto-submit", tags=["Proposals"])
async def agent_auto_submit(
    worker: dict = Depends(require_role(["admin"]))
):
    run_id = str(uuid.uuid4())
    started = datetime.now()
    
    try:
        orchestrator = AgentOrchestrator()
        results = orchestrator.run_all_agents(generate_synthetic=False)
        
        alert_level = results.get("alert_level", "GREEN")
        
        if alert_level not in ("RED", "ORANGE"):
            return {
                "status": "skipped",
                "alert_level": alert_level,
                "message": f"Alert level {alert_level} does not require cut proposal"
            }
        
        cut_list = [r.lower() for r in results["agents"]["cut_advisor"].get("cut_priority_list", [])]
        protect_list = [r.lower() for r in results["agents"]["cut_advisor"].get("protected_regions", [])]
        explanation = results["agents"]["cut_advisor"].get("reasoning", "")
        deficit_pct = results["agents"]["cut_advisor"].get("current_production_status", {}).get("deficit_percentage", 0)
        
        if not cut_list:
            return {
                "status": "skipped",
                "message": "No regions identified for cuts"
            }
        
        plan_ref = f"CUT-AUTO-{datetime.now().strftime('%Y%m%d')}-{run_id[:6].upper()}"
        
        with get_cursor() as cur:
            cur.execute("""
                INSERT INTO public.cut_plans (
                    plan_ref, proposed_by, proposed_by_worker_id, regions, protected_regions,
                    deficit_pct, alert_level, ai_explanation, status, 
                    cut_advisor_output, fairness_output, forecast_output, proposed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, NOW())
                RETURNING id
            """, (
                plan_ref,
                "agent_orchestrator",
                worker["id"],
                cut_list,
                protect_list,
                float(deficit_pct or 0),
                alert_level,
                explanation,
                json.dumps(json_safe(results["agents"]["cut_advisor"])),
                json.dumps(json_safe(results["agents"]["fairness"])),
                json.dumps(json_safe(results["agents"]["forecast"]))
            ))
            plan_id = cur.fetchone()[0]
        
        finished = datetime.now()
        
        with get_cursor() as cur:
            cur.execute("""
                INSERT INTO public.agent_run_log
                  (run_id, triggered_by, triggered_by_worker_id, started_at, finished_at, alert_level,
                   cut_advisor_status, fairness_status, forecast_status, cut_plan_id, full_output)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                run_id,
                worker["worker_id"],
                worker["id"],
                started, finished,
                alert_level,
                results["agents"]["cut_advisor"].get("status"),
                results["agents"]["fairness"].get("status"),
                results["agents"]["forecast"].get("status"),
                plan_id,
                json.dumps(json_safe(results))
            ))
        
        return {
            "status": "submitted",
            "run_id": run_id,
            "plan_id": plan_id,
            "plan_ref": plan_ref,
            "alert_level": alert_level,
            "cut_regions": cut_list,
            "deficit_pct": deficit_pct,
            "duration_sec": round((finished - started).total_seconds(), 2)
        }
        
    except Exception as e:
        logger.exception("Agent auto-submit failed")
        raise HTTPException(500, detail=str(e))

@app.get("/api/supervisor/cut-history", tags=["Supervisor"])
async def supervisor_cut_history(
    region: Optional[str] = Query(None),
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    where = "WHERE cut_date >= NOW() - INTERVAL '%s days'"
    params = [days]
    if region:
        where += " AND region = %s"
        params.append(region.lower())

    with get_cursor(dict_cursor=True) as cur:
        cur.execute(f"""
            SELECT id, region, cut_date, duration_minutes, reason, season
            FROM silver.cut_history
            {where}
            ORDER BY cut_date DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = cur.fetchall()
        cur.execute(f"SELECT COUNT(*) FROM silver.cut_history {where}", params)
        total_row = cur.fetchone()
        total = total_row['count'] if total_row else 0
    
    cur.execute(f"""
        SELECT 
            COUNT(*) as total_cuts,
            SUM(duration_minutes) as total_minutes,
            ROUND(AVG(duration_minutes)::numeric, 1) as avg_minutes,
            COUNT(DISTINCT region) as regions_affected
        FROM silver.cut_history
        {where}
    """, params)
    summary = cur.fetchone()
    
    return json_safe({
        "total": total,
        "summary": dict(summary) if summary else {},
        "cuts": list(rows)
    })

@app.get("/api/supervisor/cut-history-summary", tags=["Supervisor"])
async def supervisor_cut_history_summary(
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT * FROM public.v_cut_history_summary
        """)
        summary = cur.fetchall()
    return json_safe(list(summary))

@app.get("/api/supervisor/recent-cuts", tags=["Supervisor"])
async def supervisor_recent_cuts(
    limit: int = Query(100, ge=1, le=500),
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT * FROM public.v_recent_cuts
            LIMIT %s
        """, (limit,))
        cuts = cur.fetchall()
    return json_safe(list(cuts))

@app.get("/api/supervisor/notifications", tags=["Supervisor"])
async def supervisor_notifications(
    limit: int = Query(100, ge=1, le=500),
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT nl.id, nl.cut_plan_id, cp.plan_ref, nl.region,
                   nl.channel, nl.recipient_count, nl.status,
                   nl.sent_at, nl.error_detail
            FROM public.notification_log nl
            LEFT JOIN public.cut_plans cp ON cp.id = nl.cut_plan_id
            ORDER BY nl.sent_at DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        cur.execute("""
            SELECT
                COUNT(*)                                     AS total_sent,
                SUM(recipient_count)                         AS total_recipients,
                COUNT(*) FILTER (WHERE status = 'failed')   AS failed_count
            FROM public.notification_log
        """)
        stats = cur.fetchone()
    return json_safe({"stats": dict(stats) if stats else {}, "log": list(rows)})

@app.get("/api/supervisor/execution-overview", tags=["Supervisor"])
async def supervisor_execution_overview(
    days: int = Query(30, ge=1, le=365),
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                ce.region,
                ce.district,
                ce.worker_id,
                ce.worker_name,
                ce.status,
                ce.actual_start,
                ce.actual_end,
                ce.actual_duration_minutes,
                ce.variance_minutes,
                ce.notes,
                cp.plan_ref,
                cp.alert_level,
                cp.scheduled_date
            FROM public.cut_executions ce
            JOIN public.cut_plans cp ON cp.id = ce.cut_plan_id
            WHERE ce.created_at >= NOW() - INTERVAL '1 day' * %s
            ORDER BY ce.created_at DESC
            LIMIT 200
        """, (days,))
        executions = cur.fetchall()
        cur.execute("""
            SELECT
                COUNT(*)                                          AS total,
                COUNT(*) FILTER (WHERE status = 'restored')      AS completed,
                COUNT(*) FILTER (WHERE status = 'incident')      AS incidents,
                ROUND(AVG(actual_duration_minutes)::numeric, 1)  AS avg_duration,
                ROUND(AVG(ABS(variance_minutes))::numeric, 1)    AS avg_variance,
                MAX(actual_duration_minutes)                      AS max_duration
            FROM public.cut_executions
            WHERE created_at >= NOW() - INTERVAL '1 day' * %s
        """, (days,))
        agg = cur.fetchone()
        cur.execute("""
            SELECT worker_id, worker_name,
                   COUNT(*) AS total_tasks,
                   COUNT(*) FILTER (WHERE status = 'restored') AS completed,
                   ROUND(AVG(actual_duration_minutes)::numeric,1) AS avg_min
            FROM public.cut_executions
            WHERE created_at >= NOW() - INTERVAL '1 day' * %s
            GROUP BY worker_id, worker_name
            ORDER BY total_tasks DESC
            LIMIT 20
        """, (days,))
        workers = cur.fetchall()
    return json_safe({
        "days":       days,
        "summary":    dict(agg) if agg else {},
        "executions": list(executions),
        "by_worker":  list(workers),
    })

@app.get("/api/supervisor/citizen-reports", tags=["Supervisor"])
async def supervisor_citizen_reports(
    status: str = Query("open", regex="^(open|closed|all)$"),
    limit: int = Query(100, ge=1, le=500),
    worker: dict = Depends(require_role(["supervisor", "admin"]))
):
    with get_cursor(dict_cursor=True) as cur:
        where = "" if status == "all" else "WHERE status = %s"
        params = [] if status == "all" else [status]
        params.append(limit)
        cur.execute(f"""
            SELECT id, region, gouvernorat, report_type, description,
                   reported_at, phone, status
            FROM public.citizen_reports
            {where}
            ORDER BY reported_at DESC
            LIMIT %s
        """, params)
        reports = cur.fetchall()
        cur.execute("""
            SELECT region, report_type, COUNT(*) AS cnt
            FROM public.citizen_reports
            GROUP BY region, report_type
            ORDER BY cnt DESC
            LIMIT 20
        """)
        breakdown = cur.fetchall()
    return json_safe({"reports": list(reports), "breakdown": list(breakdown)})

# ════════════════════════════════════════════════════════════════
# SHARED ENDPOINTS
# ════════════════════════════════════════════════════════════════

@app.get("/api/regions", tags=["Shared"])
async def regions_list(worker: Optional[dict] = Depends(optional_auth)):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT DISTINCT region, gouvernorat
            FROM silver.districts
            WHERE region IS NOT NULL
            ORDER BY region
        """)
        rows = cur.fetchall()
    return json_safe(list(rows))

@app.get("/api/districts", tags=["Shared"])
async def districts_list(
    region: Optional[str] = Query(None),
    gouvernorat: Optional[str] = Query(None),
    worker: Optional[dict] = Depends(optional_auth)
):
    with get_cursor(dict_cursor=True) as cur:
        query = """
            SELECT district, gouvernorat, region, type, latitude, longitude
            FROM silver.districts
            WHERE 1=1
        """
        params = []
        if region:
            query += " AND LOWER(region) = %s"
            params.append(region.lower())
        if gouvernorat:
            query += " AND LOWER(gouvernorat) = %s"
            params.append(gouvernorat.lower())
        query += " ORDER BY district"
        
        cur.execute(query, params)
        rows = cur.fetchall()
    return json_safe(list(rows))

@app.get("/api/gouvernorats", tags=["Shared"])
async def gouvernorats_list(worker: Optional[dict] = Depends(optional_auth)):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT DISTINCT gouvernorat
            FROM silver.districts
            WHERE gouvernorat IS NOT NULL
            ORDER BY gouvernorat
        """)
        rows = cur.fetchall()
    return json_safe([r["gouvernorat"] for r in rows])

# ════════════════════════════════════════════════════════════════
# RUN
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)