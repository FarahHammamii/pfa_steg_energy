"""
STEG Observatory — Complete REST API with FastAPI
Two dashboards: Citizens / Supervisors (Workers removed)

Run:
    pip install fastapi uvicorn psycopg2-binary python-multipart bcrypt python-jose
    export NEON_DATABASE_URL="postgresql://..."
    export SMTP_USER="your-email@gmail.com"
    export SMTP_PASSWORD="your-app-password"
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
from collections import Counter
from enum import Enum

from fastapi import FastAPI, Depends, HTTPException, status, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, EmailStr
from jose import JWTError, jwt
from passlib.context import CryptContext

# ── Path setup ────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import get_cursor
from utils.logger import get_logger
from agents.cut_fairness_coordinator import run_coordinator
from agents.monthly_feedback_agent import run_monthly_feedback_loop

logger = get_logger(__name__)

# ── Environment ────────────
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

# Groq setup for satisfaction analysis
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
if GROQ_AVAILABLE and GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
    GROQ_MODEL = "llama-3.3-70b-versatile"
    logger.info("Groq client ready for satisfaction analysis")
else:
    groq_client = None
    logger.warning("Groq not available. Satisfaction analysis disabled.")

# ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="STEG Observatory API",
    description="Citizen and Supervisor dashboards for grid cut management",
    version="3.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Authentication ─────────────────────────────────────
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)

# ── JSON encoder ───────────────────────────────────────
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
    return json.loads(json.dumps(obj, cls=SafeEncoder))

# ── Database helpers ─────────────────────────────────────────
def _normalize_region(region: str) -> str:
    return region.strip().lower() if region and isinstance(region, str) else region

def _get_season(month: int) -> str:
    return {12: "winter", 1: "winter", 2: "winter",
            3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer",
            9: "autumn", 10: "autumn", 11: "autumn"}.get(month, "unknown")

# ── Authentication Models ────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: Optional[str] = None
    demo_mode: bool = True

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    full_name: str
    role: str

class SupervisorCreate(BaseModel):
    username: str
    full_name: str
    email: EmailStr
    password: Optional[str] = None

# ── Authentication Functions ─────────────────────────────────
def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, username, full_name, role, is_active
                FROM public.supervisors
                WHERE username = %s AND is_active = TRUE
            """, (username,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=401, detail="User not found or inactive")
        
        return dict(user)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

def require_role(allowed_roles: List[str]):
    async def dependency(user: dict = Depends(get_current_user)):
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user['role']}' not allowed. Required: {allowed_roles}"
            )
        return user
    return dependency

async def optional_auth(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials:
        try:
            return await get_current_user(credentials)
        except HTTPException:
            return None
    return None

# ── Helper functions ────────────────────────────────────────
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
        
        part1 = MIMEText(body_text, "plain")
        msg.attach(part1)
        
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

def send_cut_notification(region: str, scheduled_date: date, scheduled_start: time, duration_hours: float, plan_ref: str):
    """Send cut notification to all subscribed citizens in a region"""
    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT email, lang
                FROM public.citizen_preferences
                WHERE region = %s AND notify_email = TRUE
            """, (_normalize_region(region),))
            citizens = cur.fetchall()
            
            msg_ar = (
                f"⚠️ تنبيه من STEG: سيتم قطع التيار الكهربائي في {region}\n"
                f"📅 التاريخ: {scheduled_date}\n"
                f"🕐 الوقت: {scheduled_start.strftime('%H:%M') if scheduled_start else 'غير محدد'}\n"
                f"⏱️ المدة: {duration_hours} ساعات\n"
                f"📋 المرجع: {plan_ref}\n"
                f"نعتذر عن الإزعاج."
            )
            msg_fr = (
                f"⚠️ Alerte STEG: Coupure d'électricité prévue à {region}\n"
                f"📅 Date: {scheduled_date}\n"
                f"🕐 Heure: {scheduled_start.strftime('%H:%M') if scheduled_start else 'non spécifiée'}\n"
                f"⏱️ Durée: {duration_hours} heures\n"
                f"📋 Réf: {plan_ref}\n"
                f"Nous nous excusons pour la gêne occasionnée."
            )
            
            sent_count = 0
            for citizen in citizens:
                msg = msg_fr if citizen['lang'] == 'fr' else msg_ar
                subject = f"STEG Alert: Power cut scheduled in {region}"
                if send_email_notification(citizen['email'], subject, msg, f"<pre>{msg}</pre>"):
                    sent_count += 1
            
            logger.info(f"Sent {sent_count} notifications for {region}")
            return sent_count
    except Exception as e:
        logger.error(f"Notification failed for {region}: {e}")
        return 0

def send_restoration_notification(region: str, duration_minutes: int):
    """Send notification when power is restored"""
    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT email, lang
                FROM public.citizen_preferences
                WHERE region = %s AND notify_email = TRUE
            """, (_normalize_region(region),))
            citizens = cur.fetchall()
            
            msg_fr = f"✅ STEG: L'électricité a été rétablie à {region}. Durée de la coupure: {duration_minutes} minutes."
            msg_ar = f"✅ STEG: تم إعادة التيار الكهربائي في {region}. مدة الانقطاع: {duration_minutes} دقيقة."
            
            for citizen in citizens:
                msg = msg_fr if citizen['lang'] == 'fr' else msg_ar
                send_email_notification(citizen['email'], f"Power Restored - {region}", msg)
            
            logger.info(f"Restoration notifications sent for {region}")
    except Exception as e:
        logger.error(f"Restoration notification failed: {e}")
def send_cancellation_notification(region: str, plan_ref: str, reason: str):
    """Send cancellation notification to subscribed citizens"""
    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT email, lang
                FROM public.citizen_preferences
                WHERE region = %s AND notify_email = TRUE
            """, (_normalize_region(region),))
            citizens = cur.fetchall()
            
            msg_ar = (
                f"✅ إلغاء تنبيه من STEG: تم إلغاء قطع التيار الكهربائي المخطط له في {region}\n"
                f"📋 المرجع: {plan_ref}\n"
                f"📝 السبب: {reason}\n"
                f"نعتذر عن الإزعاج."
            )
            msg_fr = (
                f"✅ Annulation STEG: La coupure d'électricité prévue à {region} a été annulée\n"
                f"📋 Réf: {plan_ref}\n"
                f"📝 Raison: {reason}\n"
                f"Nous nous excusons pour le dérangement."
            )
            
            sent_count = 0
            for citizen in citizens:
                msg = msg_fr if citizen['lang'] == 'fr' else msg_ar
                if send_email_notification(citizen['email'], f"STEG: Power cut cancelled - {region}", msg):
                    sent_count += 1
            
            logger.info(f"Sent {sent_count} cancellation notifications for {region}")
            return sent_count
    except Exception as e:
        logger.error(f"Cancellation notification failed for {region}: {e}")
        return 0
# ════════════════════════════════════════════════════════════════
# AUTHENTICATION ENDPOINTS
# ════════════════════════════════════════════════════════════════

@app.post("/api/auth/login", response_model=LoginResponse, tags=["Authentication"])
async def login(request: LoginRequest):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT id, username, full_name, role, password_hash, is_active
            FROM public.supervisors
            WHERE username = %s
        """, (request.username,))
        user = cur.fetchone()
        
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        if not user["is_active"]:
            raise HTTPException(status_code=401, detail="Account disabled")
        
        if request.demo_mode:
            pass
        elif request.password:
            if not user["password_hash"] or not verify_password(request.password, user["password_hash"]):
                raise HTTPException(status_code=401, detail="Invalid credentials")
        elif not request.demo_mode:
            raise HTTPException(status_code=401, detail="Password required")
        
        cur.execute(
            "UPDATE public.supervisors SET last_login = NOW() WHERE id = %s",
            (user["id"],)
        )
    
    access_token = create_access_token(data={"sub": user["username"], "role": user["role"]})
    
    return LoginResponse(
        access_token=access_token,
        username=user["username"],
        full_name=user["full_name"],
        role=user["role"],
    )

@app.post("/api/auth/register", tags=["Authentication"])
async def register_supervisor(
    user_data: SupervisorCreate,
    admin: dict = Depends(require_role(["admin"]))
):
    with get_cursor() as cur:
        cur.execute("SELECT id FROM public.supervisors WHERE username = %s OR email = %s",
                    (user_data.username, user_data.email))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="Username or email already exists")
        
        password_hash = hash_password(user_data.password) if user_data.password else None
        
        cur.execute("""
            INSERT INTO public.supervisors 
            (username, full_name, email, role, password_hash)
            VALUES (%s, %s, %s, 'supervisor', %s)
            RETURNING id
        """, (
            user_data.username,
            user_data.full_name,
            user_data.email,
            password_hash
        ))
        user_id = cur.fetchone()[0]
    
    return {"status": "created", "username": user_data.username, "db_id": user_id}

@app.get("/api/auth/me", tags=["Authentication"])
async def get_me(user: dict = Depends(get_current_user)):
    return json_safe(user)

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
async def citizen_grid_status(user: Optional[dict] = Depends(optional_auth)):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            WITH current AS (
                SELECT total_production_gwh, year, month
                FROM gold.monthly_production
                ORDER BY year DESC, month DESC
                LIMIT 1
            ),
            hist_avg AS (
                SELECT AVG(total_production_gwh) AS avg_gwh
                FROM gold.monthly_production
                WHERE month = (SELECT month FROM current)
                  AND year < (SELECT year FROM current)
            )
            SELECT
                c.total_production_gwh AS current_gwh,
                h.avg_gwh AS normal_gwh,
                ROUND(((h.avg_gwh - c.total_production_gwh) / NULLIF(h.avg_gwh, 0)) * 100, 2) AS deficit_pct
            FROM current c, hist_avg h
        """)
        prod = cur.fetchone()

        cur.execute("""
            SELECT alert_level, status, scheduled_date, scheduled_start, estimated_duration_hours, regions
            FROM public.cut_plans
            WHERE status IN ('approved', 'scheduled')
            ORDER BY scheduled_date ASC
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
        "alert_level": alert_level,
        "deficit_pct": deficit_pct,
        "current_gwh": float(prod["current_gwh"]) if prod else None,
        "normal_gwh": float(prod["normal_gwh"]) if prod else None,
        "next_scheduled_cut": {
            "date": plan["scheduled_date"] if plan else None,
            "start": str(plan["scheduled_start"]) if plan and plan.get("scheduled_start") else None,
            "duration_hours": float(plan["estimated_duration_hours"]) if plan else None,
            "affected_regions": plan["regions"] if plan else []
        } if plan else None,
        "message_ar": _alert_message(alert_level, "ar"),
        "message_fr": _alert_message(alert_level, "fr"),
        "message_en": _alert_message(alert_level, "en"),
    })

@app.get("/api/citizen/map", tags=["Citizen"])
async def citizen_map(user: Optional[dict] = Depends(optional_auth)):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                d.gouvernorat,
                d.region,
                AVG(d.latitude) AS lat,
                AVG(d.longitude) AS lon,
                COUNT(*) AS district_count
            FROM silver.districts d
            WHERE d.latitude IS NOT NULL
            GROUP BY d.gouvernorat, d.region
            ORDER BY d.region
        """)
        districts = cur.fetchall()

        cur.execute("""
            SELECT region,
                   COUNT(*) AS cut_count_90d,
                   MAX(cut_date) AS last_cut,
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

    result = []
    for d in districts:
        region = d["region"]
        stats = cut_stats.get(region, {})
        cuts = int(stats.get("cut_count_90d") or 0)
        ratio = cuts / avg_cuts if avg_cuts > 0 else 0

        if ratio > 1.5:
            risk = "high"
        elif ratio > 0.8:
            risk = "medium"
        else:
            risk = "low"

        fairness_score = max(0, min(100, round(100 - (ratio - 1) * 50)))

        result.append(json_safe({
            "region": region,
            "gouvernorat": d["gouvernorat"],
            "lat": float(d["lat"] or 0),
            "lon": float(d["lon"] or 0),
            "district_count": int(d["district_count"]),
            "cut_count_90d": cuts,
            "last_cut": stats.get("last_cut"),
            "total_minutes_90d": int(stats.get("total_minutes_90d") or 0),
            "risk_level": risk,
            "fairness_score": fairness_score,
        }))

    return result

@app.get("/api/citizen/my-zone", tags=["Citizen"])
async def citizen_my_zone(
    region: str = Query(..., description="Region name"),
    user: Optional[dict] = Depends(optional_auth)
):
    region = region.strip().lower()
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT id, plan_ref, scheduled_date, scheduled_start,
                   estimated_duration_hours, alert_level
            FROM public.cut_plans
            WHERE status IN ('approved', 'scheduled')
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

    my_cuts = int(fair["my_cuts"] or 0) if fair else 0
    avg_cuts = float(fair["avg_cuts"] or 1) if fair else 1
    ratio = my_cuts / avg_cuts if avg_cuts > 0 else 1
    fairness_score = max(0, min(100, round(100 - (ratio - 1) * 50)))

    return json_safe({
        "region": region,
        "fairness_score": fairness_score,
        "my_cuts_90d": my_cuts,
        "national_avg": round(avg_cuts, 1),
        "next_cut": next_cut,
        "cut_history": list(history),
        "open_reports": int(reports["open_reports"] or 0) if reports else 0,
    })

class CitizenReportRequest(BaseModel):
    region: str
    report_type: str = Field(..., description="outage, voltage_issue, billing, other")
    description: Optional[str] = None
    satisfaction_score: Optional[int] = Field(None, ge=1, le=5, description="1=Very Unsatisfied, 5=Very Satisfied")

@app.post("/api/citizen/report", status_code=201, tags=["Citizen"])
async def citizen_report(
    report: CitizenReportRequest,
    background_tasks: BackgroundTasks,
    user: Optional[dict] = Depends(optional_auth)
):
    region_norm = _normalize_region(report.region)
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO public.citizen_reports
              (region, report_type, description, satisfaction_score)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (
            region_norm,
            report.report_type,
            report.description,
            report.satisfaction_score,
        ))
        report_id = cur.fetchone()[0]
    
    # Trigger satisfaction analysis in background
    if report.satisfaction_score:
        background_tasks.add_task(analyze_report_satisfaction, report_id, report.description or "")
    
    return {"status": "created", "report_id": report_id}

class CitizenSubscribeRequest(BaseModel):
    region: str
    email: EmailStr
    lang: str = "fr"

@app.post("/api/citizen/subscribe", status_code=201, tags=["Citizen"])
async def citizen_subscribe(
    prefs: CitizenSubscribeRequest,
    user: Optional[dict] = Depends(optional_auth)
):
    region_norm = _normalize_region(prefs.region)
    with get_cursor() as cur:
        cur.execute("""
            SELECT id FROM public.citizen_preferences
            WHERE region = %s AND email = %s
        """, (region_norm, prefs.email))
        existing = cur.fetchone()
        
        if existing:
            return {"status": "already_subscribed", "message": f"Already subscribed for {region_norm}"}
        
        cur.execute("""
            INSERT INTO public.citizen_preferences
              (email, region, lang, notify_email)
            VALUES (%s, %s, %s, TRUE)
            RETURNING id
        """, (
            prefs.email,
            region_norm,
            prefs.lang,
        ))
        pref_id = cur.fetchone()[0]
    
    confirmation_msg = (
        f"✅ Vous êtes maintenant abonné aux alertes STEG pour la région {region_norm}.\n"
        f"Vous recevrez des notifications en cas de coupure planifiée."
    )
    send_email_notification(
        prefs.email,
        f"STEG Alert Subscription Confirmed - {region_norm}",
        confirmation_msg
    )
    
    return {"status": "subscribed", "preference_id": pref_id}

@app.get("/api/citizen/cut-history", tags=["Citizen"])
async def citizen_cut_history(
    region: Optional[str] = Query(None),
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: Optional[dict] = Depends(optional_auth)
):
    where = "WHERE cut_date >= NOW() - INTERVAL '%s days'"
    params = [days]
    if region:
        where += " AND region = %s"
        params.append(region.lower())

    with get_cursor(dict_cursor=True) as cur:
        cur.execute(f"""
            SELECT id, region, cut_date, duration_minutes, season
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
# SUPERVISOR ENDPOINTS - DIRECT CUT PLANNING (No Approval)
# ════════════════════════════════════════════════════════════════
@app.get("/api/supervisor/active-cut-details", tags=["Supervisor"])
async def get_active_cut_details(
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """
    Get details of currently active cuts (for restore button)
    Returns cuts that are scheduled for today and not yet completed.
    """
    today = date.today()
    
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT cp.id, cp.plan_ref, cp.regions, cp.scheduled_date, cp.scheduled_start,
                   cp.estimated_duration_hours, cp.alert_level, cp.deficit_pct,
                   ce.id as execution_id, ce.status as execution_status,
                   ce.actual_start, ce.actual_end
            FROM public.cut_plans cp
            LEFT JOIN public.cut_executions ce ON ce.cut_plan_id = cp.id
            WHERE cp.status = 'approved'
              AND cp.scheduled_date = %s
              AND (ce.status IS NULL OR ce.status != 'completed')
            ORDER BY cp.scheduled_start
        """, (today,))
        active_cuts = cur.fetchall()
        
        # Also get future scheduled cuts
        cur.execute("""
            SELECT cp.id, cp.plan_ref, cp.regions, cp.scheduled_date, cp.scheduled_start,
                   cp.estimated_duration_hours, cp.alert_level, cp.deficit_pct,
                   ce.id as execution_id, ce.status as execution_status
            FROM public.cut_plans cp
            LEFT JOIN public.cut_executions ce ON ce.cut_plan_id = cp.id
            WHERE cp.status = 'approved'
              AND cp.scheduled_date > %s
            ORDER BY cp.scheduled_date, cp.scheduled_start
        """, (today,))
        upcoming_cuts = cur.fetchall()
    
    return json_safe({
        "active_cuts_today": list(active_cuts),
        "upcoming_cuts": list(upcoming_cuts),
        "has_active_cuts": len(active_cuts) > 0
    })
class DirectCutRequest(BaseModel):
    """Direct cut planning request - no approval needed"""
    regions: List[str] = Field(..., description="List of regions to cut", min_items=1)
    scheduled_date: date = Field(..., description="Date of the cut")
    scheduled_start: str = Field(..., description="Start time in HH:MM format")
    estimated_duration_hours: float = Field(..., ge=0.5, le=24, description="Duration in hours")
    reason: Optional[str] = Field(None, description="Reason for the cut")
    deficit_pct: float = Field(0.0, description="Current production deficit percentage")

@app.post("/api/supervisor/plan-cut", tags=["Supervisor"])
async def plan_cut_direct(
    body: DirectCutRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """
    DIRECT CUT PLANNING - No approval workflow.
    This creates a cut plan, sends notifications, and records the cut.
    """
    now = datetime.now()
    
    try:
        scheduled_start_time = datetime.strptime(body.scheduled_start, "%H:%M").time()
    except ValueError:
        raise HTTPException(400, "scheduled_start must be in HH:MM format")
    
    plan_ref = f"CUT-{body.scheduled_date.strftime('%Y%m%d')}-{str(uuid.uuid4())[:6].upper()}"
    
    if body.deficit_pct > 15:
        alert_level = "RED"
    elif body.deficit_pct > 8:
        alert_level = "ORANGE"
    elif body.deficit_pct > 3:
        alert_level = "YELLOW"
    else:
        alert_level = "GREEN"
    
    with get_cursor() as cur:
        # Use 'approved' status (allowed by constraint)
        cur.execute("""
            INSERT INTO public.cut_plans
              (plan_ref, proposed_by, regions, protected_regions, status,
               scheduled_date, scheduled_start, estimated_duration_hours,
               deficit_pct, alert_level, ai_explanation, 
               approved_by, approved_at, proposed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            plan_ref,
            user["username"],
            body.regions,
            [],  # protected_regions
            'approved',  # Use 'approved' - this is allowed by constraint
            body.scheduled_date,
            scheduled_start_time,
            body.estimated_duration_hours,
            body.deficit_pct,
            alert_level,
            body.reason or f"Direct cut planned by {user['username']}",
            user["username"],  # approved_by
            now,  # approved_at
            now  # proposed_at
        ))
        plan_id = cur.fetchone()[0]
        
        # Create execution record
        cur.execute("""
            INSERT INTO public.cut_executions
              (cut_plan_id, executed_by, executed_by_name, actual_start, status)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (plan_id, user["username"], user["full_name"], now, 'pending'))
        execution_id = cur.fetchone()[0]
    
    # Send notifications in background
    for region in body.regions:
        background_tasks.add_task(
            send_cut_notification,
            region,
            body.scheduled_date,
            scheduled_start_time,
            body.estimated_duration_hours,
            plan_ref
        )
    
    # Mark notifications as sent
    with get_cursor() as cur:
        cur.execute("""
            UPDATE public.cut_plans
            SET notified_citizens = TRUE, notification_sent_at = NOW()
            WHERE id = %s
        """, (plan_id,))
    
    return {
        "status": "cut_planned",
        "plan_id": plan_id,
        "execution_id": execution_id,
        "plan_ref": plan_ref,
        "alert_level": alert_level,
        "affected_regions": body.regions,
        "scheduled_date": body.scheduled_date.isoformat(),
        "scheduled_start": body.scheduled_start,
        "estimated_duration_hours": body.estimated_duration_hours,
        "notifications_sent": True,
        "planned_by": user["username"],
        "planned_at": now.isoformat()
    }

class CompleteCutRequest(BaseModel):
    """Complete an executed cut (after power is restored)"""
    plan_id: int
    execution_id: int
    actual_end: Optional[datetime] = None
    notes: Optional[str] = None

@app.post("/api/supervisor/complete-cut", tags=["Supervisor"])
async def complete_cut(
    body: CompleteCutRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """Complete a cut after power is restored"""
    now = datetime.now()
    actual_end = body.actual_end or now
    
    with get_cursor() as cur:
        # Get execution details
        cur.execute("""
            SELECT ce.id, ce.cut_plan_id, ce.actual_start, cp.regions, cp.scheduled_date, cp.plan_ref,
                   cp.estimated_duration_hours
            FROM public.cut_executions ce
            JOIN public.cut_plans cp ON cp.id = ce.cut_plan_id
            WHERE ce.id = %s AND ce.cut_plan_id = %s AND ce.status = 'pending'
        """, (body.execution_id, body.plan_id))
        exec_row = cur.fetchone()
        
        if not exec_row:
            raise HTTPException(404, "Execution not found or already completed")
        
        start_time = exec_row[2]
        if start_time:
            duration_min = int((actual_end - start_time).total_seconds() / 60)
        else:
            duration_min = int(exec_row[6] * 60) if exec_row[6] else 60
        
        # Update execution to 'completed'
        cur.execute("""
            UPDATE public.cut_executions
            SET status = 'completed',
                actual_end = %s,
                actual_duration_minutes = %s,
                notes = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (actual_end, duration_min, body.notes, body.execution_id))
        
        # Update cut plan to 'executed' (allowed by constraint)
        cur.execute("""
            UPDATE public.cut_plans
            SET status = 'executed',
                restored_at = %s,
                restored_by = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (actual_end, user["username"], body.plan_id))
        
        regions = exec_row[3] or []
        season = _get_season(actual_end.month)
        
        # Record in cut history
        for region in regions:
            cur.execute("""
                INSERT INTO silver.cut_history
                  (region, cut_date, duration_minutes, season, executed_by, plan_ref)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (region, actual_end.date(), duration_min, season, user["username"], exec_row[5]))
        
        # Send restoration notifications
        for region in regions:
            background_tasks.add_task(send_restoration_notification, region, duration_min)
    
    return {
        "status": "cut_completed",
        "plan_id": body.plan_id,
        "execution_id": body.execution_id,
        "duration_minutes": duration_min,
        "actual_end": actual_end.isoformat(),
        "affected_regions": regions,
        "completed_by": user["username"]
    }


@app.get("/api/supervisor/active-cuts", tags=["Supervisor"])
async def get_active_cuts(
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """Get all active/upcoming cuts"""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT cp.id, cp.plan_ref, cp.regions, cp.scheduled_date, cp.scheduled_start,
                   cp.estimated_duration_hours, cp.alert_level, cp.deficit_pct,
                   ce.id as execution_id, ce.status as execution_status,
                   ce.actual_start, ce.actual_end
            FROM public.cut_plans cp
            LEFT JOIN public.cut_executions ce ON ce.cut_plan_id = cp.id
            WHERE cp.status = 'approved'
              AND cp.scheduled_date >= CURRENT_DATE
            ORDER BY cp.scheduled_date, cp.scheduled_start
        """)
        cuts = cur.fetchall()
    
    return json_safe({"active_cuts": list(cuts)})
class RestoreActiveCutRequest(BaseModel):
    """Restore power during an active cut"""
    plan_id: int
    actual_end: Optional[datetime] = None
    notes: Optional[str] = None

@app.post("/api/supervisor/restore-active-cut", tags=["Supervisor"])
async def restore_active_cut(
    body: RestoreActiveCutRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """
    RESTORE POWER DURING ACTIVE CUT
    Just provide the plan_id - execution is found automatically.
    This will mark the cut as completed and send restoration notifications.
    """
    now = datetime.now()
    actual_end = body.actual_end or now
    
    with get_cursor() as cur:
        # Get the cut plan and its execution
        cur.execute("""
            SELECT cp.id, cp.plan_ref, cp.regions, cp.scheduled_date, 
                   cp.estimated_duration_hours, cp.status as plan_status,
                   ce.id as execution_id, ce.actual_start, ce.status as exec_status
            FROM public.cut_plans cp
            LEFT JOIN public.cut_executions ce ON ce.cut_plan_id = cp.id
            WHERE cp.id = %s
        """, (body.plan_id,))
        plan = cur.fetchone()
        
        if not plan:
            raise HTTPException(404, "Cut plan not found")
        
        # Check if cut is eligible for restoration
        if plan[5] != 'approved':
            raise HTTPException(409, f"Plan status is '{plan[5]}', not eligible for restoration. Only 'approved' cuts can be restored.")
        
        if plan[8] == 'completed':
            raise HTTPException(409, "Cut already completed")
        
        # Check if cut is scheduled for today or past
        if plan[3] and plan[3] > date.today():
            raise HTTPException(409, f"Cannot restore a cut scheduled for future date ({plan[3]}). Use cancel instead.")
        
        # Calculate duration
        start_time = plan[7]
        if start_time:
            duration_min = int((actual_end - start_time).total_seconds() / 60)
        else:
            duration_min = int(plan[4] * 60) if plan[4] else 60
        
        execution_id = plan[6]
        
        # Update execution if exists, or create one
        if execution_id:
            cur.execute("""
                UPDATE public.cut_executions
                SET status = 'completed',
                    actual_end = %s,
                    actual_duration_minutes = %s,
                    notes = COALESCE(notes || E'\n' || %s, %s),
                    updated_at = NOW()
                WHERE id = %s
            """, (actual_end, duration_min, f"Restored by {user['username']}: {body.notes or ''}", body.notes, execution_id))
        else:
            # Create execution record
            cur.execute("""
                INSERT INTO public.cut_executions
                (cut_plan_id, executed_by, executed_by_name, actual_start, actual_end, 
                 actual_duration_minutes, status, notes)
                VALUES (%s, %s, %s, %s, %s, %s, 'completed', %s)
                RETURNING id
            """, (body.plan_id, user["username"], user["full_name"], start_time or now, 
                  actual_end, duration_min, f"Restored by {user['username']}: {body.notes or ''}"))
            execution_id = cur.fetchone()[0]
        
        # Update cut plan to 'executed'
        cur.execute("""
            UPDATE public.cut_plans
            SET status = 'executed',
                restored_at = %s,
                restored_by = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (actual_end, user["username"], body.plan_id))
        
        regions = plan[2] or []
        season = _get_season(actual_end.month)
        
        # Record in cut history
        for region in regions:
            cur.execute("""
                INSERT INTO silver.cut_history
                  (region, cut_date, duration_minutes, season, executed_by, plan_ref)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (region, actual_end.date(), duration_min, season, user["username"], plan[1]))
        
        # Send restoration notifications
        for region in regions:
            background_tasks.add_task(send_restoration_notification, region, duration_min)
    
    return {
        "status": "power_restored",
        "plan_id": body.plan_id,
        "plan_ref": plan[1],
        "execution_id": execution_id,
        "duration_minutes": duration_min,
        "actual_end": actual_end.isoformat(),
        "affected_regions": regions,
        "restored_by": user["username"],
        "message": f"Power restored successfully after {duration_min} minutes"
    }


class CancelCutRequest(BaseModel):
    """Cancel a scheduled cut"""
    plan_id: int
    cancellation_reason: str

@app.post("/api/supervisor/cancel-cut", tags=["Supervisor"])
async def cancel_cut(
    body: CancelCutRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """
    CANCEL A SCHEDULED CUT (Before execution date)
    Just provide the plan_id - execution is found automatically.
    This will cancel the cut, send cancellation notifications, and mark as rejected.
    """
    now = datetime.now()
    
    with get_cursor() as cur:
        # Get the cut plan
        cur.execute("""
            SELECT cp.id, cp.plan_ref, cp.regions, cp.scheduled_date, cp.status,
                   ce.id as execution_id, ce.status as exec_status
            FROM public.cut_plans cp
            LEFT JOIN public.cut_executions ce ON ce.cut_plan_id = cp.id
            WHERE cp.id = %s
        """, (body.plan_id,))
        plan = cur.fetchone()
        
        if not plan:
            raise HTTPException(404, "Cut plan not found")
        
        # Check if cut can be cancelled
        if plan[4] == 'executed':
            raise HTTPException(409, "Cannot cancel an already executed cut")
        
        if plan[4] == 'rejected':
            raise HTTPException(409, "Cut already cancelled")
        
        # Check if scheduled date is today or in the future (can't cancel past cuts)
        if plan[3] and plan[3] < date.today():
            raise HTTPException(409, f"Cannot cancel a cut from past date ({plan[3]}). The cut has already passed.")
        
        execution_id = plan[5]
        
        # Update cut plan to 'rejected'
        cur.execute("""
            UPDATE public.cut_plans
            SET status = 'rejected',
                rejected_by = %s,
                rejected_at = %s,
                rejection_reason = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (user["username"], now, body.cancellation_reason, body.plan_id))
        
        # Update execution if exists
        if execution_id:
            cur.execute("""
                UPDATE public.cut_executions
                SET status = 'cancelled',
                    notes = COALESCE(notes || E'\n' || %s, %s),
                    updated_at = NOW()
                WHERE id = %s
            """, (f"Cancelled by {user['username']}: {body.cancellation_reason}", body.cancellation_reason, execution_id))
        
        regions = plan[2] or []
        
        # Send cancellation notifications
        for region in regions:
            background_tasks.add_task(send_cancellation_notification, region, plan[1], body.cancellation_reason)
    
    return {
        "status": "cut_cancelled",
        "plan_id": body.plan_id,
        "plan_ref": plan[1],
        "affected_regions": regions,
        "cancellation_reason": body.cancellation_reason,
        "cancelled_by": user["username"],
        "cancelled_at": now.isoformat(),
        "message": f"Cut for {', '.join(regions)} has been cancelled"
    }


@app.get("/api/supervisor/active-cut-details", tags=["Supervisor"])
async def get_active_cut_details(
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """
    Get details of currently active cuts (for restore button)
    Returns cuts that are scheduled for today and not yet completed.
    """
    today = date.today()
    
    with get_cursor(dict_cursor=True) as cur:
        # Active cuts for today (can be restored)
        cur.execute("""
            SELECT cp.id, cp.plan_ref, cp.regions, cp.scheduled_date, cp.scheduled_start,
                   cp.estimated_duration_hours, cp.alert_level, cp.deficit_pct,
                   ce.id as execution_id, ce.status as execution_status,
                   ce.actual_start, ce.actual_end
            FROM public.cut_plans cp
            LEFT JOIN public.cut_executions ce ON ce.cut_plan_id = cp.id
            WHERE cp.status = 'approved'
              AND cp.scheduled_date = %s
              AND (ce.status IS NULL OR ce.status NOT IN ('completed', 'cancelled'))
            ORDER BY cp.scheduled_start
        """, (today,))
        active_cuts = cur.fetchall()
        
        # Future scheduled cuts (can be cancelled)
        cur.execute("""
            SELECT cp.id, cp.plan_ref, cp.regions, cp.scheduled_date, cp.scheduled_start,
                   cp.estimated_duration_hours, cp.alert_level, cp.deficit_pct,
                   ce.id as execution_id, ce.status as execution_status
            FROM public.cut_plans cp
            LEFT JOIN public.cut_executions ce ON ce.cut_plan_id = cp.id
            WHERE cp.status = 'approved'
              AND cp.scheduled_date > %s
            ORDER BY cp.scheduled_date, cp.scheduled_start
        """, (today,))
        upcoming_cuts = cur.fetchall()
        
        # Completed cuts today (for reference)
        cur.execute("""
            SELECT cp.id, cp.plan_ref, cp.regions, cp.scheduled_date,
                   cp.estimated_duration_hours, ce.actual_duration_minutes,
                   ce.actual_end, ce.actual_start
            FROM public.cut_plans cp
            JOIN public.cut_executions ce ON ce.cut_plan_id = cp.id
            WHERE cp.status = 'executed'
              AND cp.scheduled_date = %s
            ORDER BY ce.actual_end DESC
        """, (today,))
        completed_today = cur.fetchall()
    
    return json_safe({
        "active_cuts_today": list(active_cuts),
        "upcoming_cuts": list(upcoming_cuts),
        "completed_today": list(completed_today),
        "has_active_cuts": len(active_cuts) > 0,
        "message": f"You have {len(active_cuts)} active cut(s) today that can be restored, and {len(upcoming_cuts)} upcoming cut(s) that can be cancelled."
    })

@app.get("/api/supervisor/dashboard", tags=["Supervisor"])
async def supervisor_dashboard(user: dict = Depends(require_role(["supervisor", "admin"]))):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            WITH latest AS (
                SELECT total_production_gwh, avg_renewable_share_pct,
                       growth_rate_pct, year, month
                FROM gold.monthly_production ORDER BY year DESC, month DESC LIMIT 1
            ),
            prev AS (
                SELECT total_production_gwh AS prev_gwh
                FROM gold.monthly_production ORDER BY year DESC, month DESC LIMIT 1 OFFSET 1
            )
            SELECT l.*, p.prev_gwh,
                   ROUND(((l.total_production_gwh - p.prev_gwh) / NULLIF(p.prev_gwh,0))*100,2) AS mom_change_pct
            FROM latest l, prev p
        """)
        prod = cur.fetchone()

        cur.execute("SELECT COUNT(*) AS scheduled_count FROM public.cut_plans WHERE status = 'scheduled'")
        scheduled = cur.fetchone()

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
                       SUM(n) OVER () AS total_sum
                FROM counts
            )
            SELECT ROUND(
                (2.0 * SUM(rk * n) - (MAX(total_n) + 1) * MAX(total_sum))
                / NULLIF(MAX(total_n) * MAX(total_sum), 0), 3) AS gini
            FROM ranked
        """)
        gini_row = cur.fetchone()
        gini = float(gini_row["gini"] or 0) if gini_row else 0

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE incident_date >= NOW() - INTERVAL '30 days') AS incidents_30d,
                ROUND(AVG(duration_minutes)::numeric, 0) AS avg_duration_min
            FROM bronze.incidents
        """)
        incidents = cur.fetchone()

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                COUNT(*) FILTER (WHERE status = 'scheduled') AS scheduled,
                COUNT(*) FILTER (WHERE status = 'rejected') AS rejected,
                COUNT(*) AS total
            FROM public.cut_plans
            WHERE created_at >= NOW() - INTERVAL '30 days'
        """)
        plans_kpi = cur.fetchone()

        cur.execute("SELECT alert_level FROM public.cut_plans ORDER BY created_at DESC LIMIT 1")
        alert_row = cur.fetchone()

    return json_safe({
        "alert_level": alert_row["alert_level"] if alert_row else "GREEN",
        "production": {
            "current_gwh": float(prod["total_production_gwh"] or 0) if prod else 0,
            "prev_gwh": float(prod["prev_gwh"] or 0) if prod else 0,
            "mom_change_pct": float(prod["mom_change_pct"] or 0) if prod else 0,
            "renewable_share": float(prod["avg_renewable_share_pct"] or 0) if prod else 0,
            "growth_rate": float(prod["growth_rate_pct"] or 0) if prod else 0,
        },
        "fairness": {
            "gini_coefficient": gini,
            "gini_label": "fair" if gini < 0.3 else "moderate" if gini < 0.5 else "unfair",
        },
        "plans": {
            "scheduled": int(scheduled["scheduled_count"] or 0) if scheduled else 0,
            "completed_30d": int(plans_kpi["completed"] or 0) if plans_kpi else 0,
            "scheduled_30d": int(plans_kpi["scheduled"] or 0) if plans_kpi else 0,
            "total_30d": int(plans_kpi["total"] or 0) if plans_kpi else 0,
        },
        "incidents": {
            "last_30d": int(incidents["incidents_30d"] or 0) if incidents else 0,
            "avg_duration_min": float(incidents["avg_duration_min"] or 0) if incidents else 0,
        },
    })

@app.get("/api/supervisor/cut-history-summary", tags=["Supervisor"])
async def supervisor_cut_history_summary(
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """Get summary statistics of cut history"""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT 
                COUNT(*) AS total_cuts,
                COUNT(DISTINCT region) AS regions_affected,
                SUM(duration_minutes) AS total_minutes,
                ROUND(AVG(duration_minutes)::numeric, 1) AS avg_duration_min,
                MIN(cut_date) AS first_cut,
                MAX(cut_date) AS last_cut
            FROM silver.cut_history
        """)
        summary = cur.fetchone()
        
        cur.execute("""
            SELECT region, COUNT(*) AS cut_count, SUM(duration_minutes) AS total_minutes
            FROM silver.cut_history
            GROUP BY region
            ORDER BY cut_count DESC
            LIMIT 10
        """)
        top_regions = cur.fetchall()
        
        cur.execute("""
            SELECT 
                DATE_TRUNC('month', cut_date) AS month,
                COUNT(*) AS cuts,
                SUM(duration_minutes) AS total_minutes
            FROM silver.cut_history
            WHERE cut_date >= NOW() - INTERVAL '12 months'
            GROUP BY DATE_TRUNC('month', cut_date)
            ORDER BY month DESC
        """)
        monthly_trend = cur.fetchall()
    
    return json_safe({
        "summary": dict(summary) if summary else {},
        "top_affected_regions": list(top_regions),
        "monthly_trend": list(monthly_trend)
    })

@app.get("/api/supervisor/fairness-heatmap", tags=["Supervisor"])
async def supervisor_fairness_heatmap(
    days: int = Query(90, ge=1, le=365),
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            WITH cut_counts AS (
                SELECT
                    ch.region,
                    COUNT(*) AS cut_count,
                    SUM(ch.duration_minutes) AS total_minutes,
                    ROUND(AVG(ch.duration_minutes)::numeric, 1) AS avg_minutes,
                    MAX(ch.cut_date) AS last_cut
                FROM silver.cut_history ch
                WHERE ch.cut_date >= NOW() - INTERVAL '1 day' * %s
                GROUP BY ch.region
            ),
            stats AS (
                SELECT AVG(cut_count) AS avg_n, STDDEV(cut_count) AS std_n FROM cut_counts
            ),
            solar AS (
                SELECT region, SUM(total_installations) AS solar_installs
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
                CASE
                    WHEN cc.cut_count > st.avg_n + st.std_n THEN 'over_cut'
                    WHEN cc.cut_count < st.avg_n - st.std_n THEN 'under_cut'
                    ELSE 'normal'
                END AS fairness_flag,
                ROUND(st.avg_n::numeric, 1) AS national_avg
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
                / NULLIF(MAX(total_n) * MAX(total_sum), 0), 3) AS gini 
            FROM ranked
        """, (days,))
        gini_row = cur.fetchone()
    
    return json_safe({
        "days": days,
        "gini": float(gini_row["gini"] or 0) if gini_row else 0,
        "regions": list(rows),
    })

@app.get("/api/supervisor/production-forecast", tags=["Supervisor"])
async def supervisor_production_forecast(
    months: int = Query(6, ge=1, le=12),
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """Get production forecast from saved predictions"""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT forecast_date, year, month, month_name, predicted_gwh, 
                   confidence_lower, confidence_upper, created_at
            FROM gold.production_forecast
            WHERE forecast_date >= CURRENT_DATE
            ORDER BY forecast_date
            LIMIT %s
        """, (months,))
        forecasts = cur.fetchall()
        
        cur.execute("""
            SELECT year, month, month_name, total_production_gwh, 
                   avg_renewable_share_pct, growth_rate_pct
            FROM gold.monthly_production
            ORDER BY year DESC, month DESC
            LIMIT 12
        """)
        historical = cur.fetchall()
    
    return json_safe({
        "forecasts": list(forecasts),
        "historical": list(historical),
        "forecast_method": "Seasonal + Trend Model with ML (SGD)",
        "as_of": datetime.now().isoformat()
    })

@app.get("/api/supervisor/citizen-reports", tags=["Supervisor"])
async def supervisor_citizen_reports(
    status: str = Query("open", regex="^(open|closed|all)$"),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    with get_cursor(dict_cursor=True) as cur:
        where = "" if status == "all" else "WHERE status = %s"
        params = [] if status == "all" else [status]
        params.append(limit)
        cur.execute(f"""
            SELECT id, region, report_type, description, satisfaction_score,
                   reported_at, status, analyzed_sentiment
            FROM public.citizen_reports
            {where}
            ORDER BY reported_at DESC
            LIMIT %s
        """, params)
        reports = cur.fetchall()
        
        cur.execute("""
            SELECT report_type, COUNT(*) AS cnt, AVG(satisfaction_score) AS avg_score
            FROM public.citizen_reports
            GROUP BY report_type
            ORDER BY cnt DESC
        """)
        breakdown = cur.fetchall()
    
    return json_safe({"reports": list(reports), "breakdown": list(breakdown)})

# ════════════════════════════════════════════════════════════════
# SATISFACTION ANALYSIS ENDPOINTS
# ════════════════════════════════════════════════════════════════

class SatisfactionAnalysisResponse(BaseModel):
    total_reports: int
    satisfied_count: int
    unsatisfied_count: int
    satisfaction_rate: float
    breakdown_by_type: Dict[str, Dict[str, int]]
    explanation: str

def analyze_report_satisfaction(report_id: int, description: str):
    """Analyze a single report's satisfaction using Groq"""
    if not groq_client or not description:
        return
    
    prompt = f"""
    Analyze this citizen report about power issues and classify if the citizen is SATISFIED or UNSATISFIED.
    
    Report: "{description}"
    
    Reply ONLY with JSON: {{"sentiment": "satisfied" or "unsatisfied", "confidence": 0.0-1.0, "reason": "short explanation"}}
    """
    
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200
        )
        result = json.loads(response.choices[0].message.content.strip())
        
        with get_cursor() as cur:
            cur.execute("""
                UPDATE public.citizen_reports
                SET analyzed_sentiment = %s, sentiment_confidence = %s
                WHERE id = %s
            """, (result.get("sentiment"), result.get("confidence"), report_id))
    except Exception as e:
        logger.error(f"Satisfaction analysis failed for report {report_id}: {e}")

@app.get("/api/supervisor/satisfaction-analysis", tags=["Supervisor"])
async def get_satisfaction_analysis(
    days: int = Query(30, ge=1, le=365),
    user: dict = Depends(require_role(["supervisor", "admin"]))
) -> SatisfactionAnalysisResponse:
    """Get satisfaction analysis of citizen reports using Groq AI"""
    
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT id, report_type, satisfaction_score, description, analyzed_sentiment
            FROM public.citizen_reports
            WHERE reported_at >= NOW() - INTERVAL '%s days'
            ORDER BY reported_at DESC
        """, (days,))
        reports = cur.fetchall()
    
    total = len(reports)
    
    satisfied_by_score = sum(1 for r in reports if r.get("satisfaction_score") and r["satisfaction_score"] >= 4)
    unsatisfied_by_score = sum(1 for r in reports if r.get("satisfaction_score") and r["satisfaction_score"] <= 3)
    
    satisfied_by_ai = sum(1 for r in reports if r.get("analyzed_sentiment") == "satisfied")
    unsatisfied_by_ai = sum(1 for r in reports if r.get("analyzed_sentiment") == "unsatisfied")
    
    if satisfied_by_ai + unsatisfied_by_ai > 0:
        satisfied_count = satisfied_by_ai
        unsatisfied_count = unsatisfied_by_ai
        analysis_method = "AI Sentiment Analysis"
    else:
        satisfied_count = satisfied_by_score
        unsatisfied_count = unsatisfied_by_score
        analysis_method = "Score-based (4-5=Satisfied, 1-3=Unsatisfied)"
    
    satisfaction_rate = (satisfied_count / total * 100) if total > 0 else 0
    
    breakdown_by_type = {}
    for report in reports:
        rtype = report.get("report_type", "other")
        if rtype not in breakdown_by_type:
            breakdown_by_type[rtype] = {"total": 0, "satisfied": 0, "unsatisfied": 0}
        breakdown_by_type[rtype]["total"] += 1
        
        if report.get("analyzed_sentiment") == "satisfied" or (report.get("satisfaction_score") and report["satisfaction_score"] >= 4):
            breakdown_by_type[rtype]["satisfied"] += 1
        elif report.get("analyzed_sentiment") == "unsatisfied" or (report.get("satisfaction_score") and report["satisfaction_score"] <= 3):
            breakdown_by_type[rtype]["unsatisfied"] += 1
    
    explanation = ""
    if groq_client and total > 0:
        context = f"""
        Satisfaction Analysis Results ({analysis_method}):
        - Total reports: {total}
        - Satisfied: {satisfied_count} ({satisfaction_rate:.1f}%)
        - Unsatisfied: {unsatisfied_count}
        - Breakdown by type: {json.dumps(breakdown_by_type)}
        """
        
        prompt = f"""
        You are STEG's customer satisfaction analyst.
        
        Based on the following citizen report analysis:
        {context}
        
        Provide a brief explanation (2-3 sentences) about:
        1. Overall citizen satisfaction level
        2. Which type of issues cause most dissatisfaction
        3. Recommendations for improvement
        
        Be professional and data-driven.
        """
        
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300
            )
            explanation = response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Groq explanation failed: {e}")
            explanation = f"Based on {total} reports over {days} days, {satisfaction_rate:.1f}% of citizens reported satisfaction. {analysis_method} was used."
    else:
        explanation = f"Based on {total} reports over {days} days, {satisfaction_rate:.1f}% of citizens reported satisfaction. {analysis_method} was used."
    
    return SatisfactionAnalysisResponse(
        total_reports=total,
        satisfied_count=satisfied_count,
        unsatisfied_count=unsatisfied_count,
        satisfaction_rate=round(satisfaction_rate, 1),
        breakdown_by_type=breakdown_by_type,
        explanation=explanation
    )

@app.post("/api/supervisor/analyze-reports", tags=["Supervisor"])
async def analyze_all_reports(
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """Trigger AI analysis for all unanalyzed reports"""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT id, description
            FROM public.citizen_reports
            WHERE analyzed_sentiment IS NULL AND description IS NOT NULL
            LIMIT 100
        """)
        unanalyzed = cur.fetchall()
    
    for report in unanalyzed:
        background_tasks.add_task(
            analyze_report_satisfaction,
            report["id"],
            report.get("description", "")
        )
    
    return {
        "status": "analysis_started",
        "reports_to_analyze": len(unanalyzed),
        "message": f"Started analysis for {len(unanalyzed)} reports in background"
    }

# ════════════════════════════════════════════════════════════════
# TRIGGER AGENTS ENDPOINT
# ════════════════════════════════════════════════════════════════

class TriggerAgentsRequest(BaseModel):
    generate_synthetic: bool = False

@app.post("/api/supervisor/trigger-agents", tags=["Supervisor"])
async def supervisor_trigger_agents(
    body: TriggerAgentsRequest = TriggerAgentsRequest(),
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """Trigger CutAdvisor + FairnessAgent to generate cut proposal"""
    run_id = str(uuid.uuid4())
    started = datetime.now()
    
    try:
        if body.generate_synthetic:
            run_monthly_feedback_loop()
        
        coordinated_result = run_coordinator()
        final_result = coordinated_result.get("final_result", coordinated_result)
        
        cut_list = final_result.get("cut_priority_list", [])
        protect_list = final_result.get("protected_regions", [])
        explanation = final_result.get("reasoning", "")
        deficit_pct = final_result.get("current_production_status", {}).get("deficit_percentage", 0)
        fairness_review = final_result.get("fairness_review", {})
        
        production_status = final_result.get("current_production_status", {})
        need_cuts = production_status.get("need_cuts", False)
        
        if deficit_pct > 15:
            alert_level = "RED"
        elif deficit_pct > 8 or need_cuts:
            alert_level = "ORANGE"
        elif deficit_pct > 3:
            alert_level = "YELLOW"
        else:
            alert_level = "GREEN"
        
        plan_id = None
        if alert_level in ("RED", "ORANGE") and cut_list:
            plan_ref = f"CUT-{datetime.now().strftime('%Y%m%d')}-{run_id[:6].upper()}"
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO public.cut_plans
                      (plan_ref, proposed_by, regions, protected_regions, status,
                       alert_level, deficit_pct, ai_explanation,
                       cut_advisor_output, fairness_output)
                    VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    plan_ref,
                    user["username"],
                    cut_list,
                    protect_list,
                    alert_level,
                    float(deficit_pct or 0),
                    explanation,
                    json.dumps(json_safe(final_result)),
                    json.dumps(json_safe(fairness_review)),
                ))
                plan_id = cur.fetchone()[0]

        finished = datetime.now()
        
        return {
            "run_id": run_id,
            "alert_level": alert_level,
            "cut_plan_id": plan_id,
            "cut_regions": cut_list,
            "protected_regions": protect_list,
            "deficit_pct": deficit_pct,
            "fairness_approved": fairness_review.get("approved", True),
            "fairness_message": fairness_review.get("message", ""),
            "duration_sec": round((finished - started).total_seconds(), 2),
        }
    except Exception as e:
        logger.exception("Agent run failed")
        raise HTTPException(500, detail=str(e))

# ════════════════════════════════════════════════════════════════
# SHARED ENDPOINTS
# ════════════════════════════════════════════════════════════════

@app.get("/api/regions", tags=["Shared"])
async def regions_list(user: Optional[dict] = Depends(optional_auth)):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT DISTINCT region, gouvernorat
            FROM silver.districts
            WHERE region IS NOT NULL
            ORDER BY region
        """)
        rows = cur.fetchall()
    return json_safe(list(rows))

@app.get("/api/gouvernorats", tags=["Shared"])
async def gouvernorats_list(user: Optional[dict] = Depends(optional_auth)):
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
# MONTHLY FEEDBACK ENDPOINT (Returns predictions)
# ════════════════════════════════════════════════════════════════

@app.post("/api/supervisor/monthly-feedback", tags=["Supervisor"])
async def run_monthly_feedback_endpoint(
    user: dict = Depends(require_role(["supervisor", "admin"]))
):
    """
    Run monthly feedback loop and return production forecast with predictions.
    This generates predictions for next 6 months using ML model.
    """
    try:
        # Import the function from your monthly_feedback_agent
        from agents.monthly_feedback_agent import run_monthly_feedback_loop as run_feedback
        
        result = run_feedback()
        
        # If result has error, try to generate synthetic data first
        if result.get("status") == "error" or not result.get("predictions"):
            # Run again with synthetic data generation
            logger.info("Retrying with synthetic data generation...")
            result = run_feedback()
        
        return json_safe(result)
    except Exception as e:
        logger.exception("Monthly feedback loop failed")
        raise HTTPException(500, detail=str(e))

# ════════════════════════════════════════════════════════════════
# RUN
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)