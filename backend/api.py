import os
from datetime import datetime, timedelta, timezone

import psycopg
from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, put
from litestar.exceptions import HTTPException
from litestar.status_codes import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
)
from passlib.context import CryptContext
from psycopg.rows import dict_row
from pydantic import BaseModel, field_validator

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54395/spectrum")
SECRET = os.environ.get("JWT_SECRET", "spectrum-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "calibrator": {"role": "writer", "password_hash": pwd.hash("calib123456")},
    "inspector": {"role": "reader", "password_hash": pwd.hash("insp123456")},
}

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id serial PRIMARY KEY,
        lamp text NOT NULL,
        nominal_nm double precision NOT NULL,
        measured_nm double precision NOT NULL,
        status text NOT NULL,
        verdict text NOT NULL DEFAULT '',
        reason text NOT NULL DEFAULT '',
        created_by text NOT NULL,
        created_at timestamptz NOT NULL
    )
    """,
    # 已锁温度清单（温表）：可事后改温，但不得影响已冻结的旧单
    """
    CREATE TABLE IF NOT EXISTS temps (
        id serial PRIMARY KEY,
        ambient_c double precision NOT NULL,
        source text NOT NULL DEFAULT 'manual',
        job_id integer,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL,
        updated_at timestamptz
    )
    """,
    # 环境温度随单冻结：快照存在单据上，与温表解耦
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ambient_c double precision",
]

SEED_JOBS = [
    ("氦灯-587", 587.56, 587.50, 23.0, "合格", "偏差 0.0600 nm 在允差内"),
    ("汞灯-546", 546.07, 546.30, 21.5, "超差", "偏差 0.2300 nm 超过允差 0.08"),
]


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


class LoginIn(BaseModel):
    username: str
    password: str


def _blank_to_none(v):
    if isinstance(v, str) and not v.strip():
        return None
    return v


class JobIn(BaseModel):
    lamp: str
    nominal_nm: float
    measured_nm: float
    ambient_c: float | None = None

    @field_validator("ambient_c", mode="before")
    @classmethod
    def ambient_blank_to_none(cls, v):
        return _blank_to_none(v)


class TempIn(BaseModel):
    ambient_c: float | None = None

    @field_validator("ambient_c", mode="before")
    @classmethod
    def ambient_blank_to_none(cls, v):
        return _blank_to_none(v)


def require_ambient(value: float | None) -> float:
    if value is None:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="缺温：环境温度为必填项，请填写环境温度（°C）后再提交",
        )
    return value


def user_from_request(request: Request) -> dict:
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = jwt.decode(auth[7:], SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict) -> None:
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅校准员可提交")


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "spectrum-wavelength-desk"}


@post("/api/login")
async def login(data: LoginIn) -> dict:
    u = USERS.get(data.username)
    if not u or not pwd.verify(data.password, u["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    token = jwt.encode(
        {
            "sub": data.username,
            "role": u["role"],
            "exp": datetime.now(timezone.utc) + timedelta(hours=12),
        },
        SECRET,
        algorithm="HS256",
    )
    return {"access_token": token, "role": u["role"], "username": data.username}


@get("/api/jobs")
async def list_jobs(request: Request) -> list:
    user_from_request(request)
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, lamp, nominal_nm, measured_nm, ambient_c, status, verdict, reason, created_by FROM jobs ORDER BY id DESC"
        ).fetchall()
        return list(rows)


@get("/api/jobs/{job_id:int}")
async def get_job(request: Request, job_id: int) -> dict:
    user_from_request(request)
    with connect() as conn:
        row = conn.execute(
            "SELECT id, lamp, nominal_nm, measured_nm, ambient_c, status, verdict, reason, created_by FROM jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="任务不存在")
        return dict(row)


@post("/api/jobs")
async def create_job(request: Request, data: JobIn) -> dict:
    user = user_from_request(request)
    require_writer(user)
    ambient = require_ambient(data.ambient_c)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO jobs(lamp, nominal_nm, measured_nm, ambient_c, status, verdict, reason, created_by, created_at)
            VALUES (%s,%s,%s,%s,'pending','','',%s,%s) RETURNING id
            """,
            (data.lamp.strip(), data.nominal_nm, data.measured_nm, ambient, user["username"], now),
        ).fetchone()
        # 随单锁定：该温度同时进入已锁温度清单
        conn.execute(
            "INSERT INTO temps(ambient_c, source, job_id, created_by, created_at) VALUES (%s,'job',%s,%s,%s)",
            (ambient, row["id"], user["username"], now),
        )
        conn.commit()
        return {"id": row["id"], "status": "pending", "ambient_c": ambient}


@get("/api/temps")
async def list_temps(request: Request) -> list:
    user_from_request(request)
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, ambient_c, source, job_id, created_by, created_at, updated_at FROM temps ORDER BY id DESC"
        ).fetchall()
        return list(rows)


@post("/api/temps")
async def create_temp(request: Request, data: TempIn) -> dict:
    user = user_from_request(request)
    require_writer(user)
    ambient = require_ambient(data.ambient_c)
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO temps(ambient_c, source, job_id, created_by, created_at) VALUES (%s,'manual',NULL,%s,%s) RETURNING id",
            (ambient, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        conn.commit()
        return {"id": row["id"], "ambient_c": ambient}


@put("/api/temps/{temp_id:int}")
async def update_temp(request: Request, temp_id: int, data: TempIn) -> dict:
    user = user_from_request(request)
    require_writer(user)
    ambient = require_ambient(data.ambient_c)
    with connect() as conn:
        cur = conn.execute(
            "UPDATE temps SET ambient_c=%s, updated_at=%s WHERE id=%s",
            (ambient, datetime.now(timezone.utc), temp_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="温度记录不存在")
        conn.commit()
        # 只改温表；旧单上的 ambient_c 快照保持冻结，不被回写
        return {"id": temp_id, "ambient_c": ambient}


def on_startup() -> None:
    with connect() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            for lamp, nominal, measured, ambient, verdict, reason in SEED_JOBS:
                row = conn.execute(
                    """
                    INSERT INTO jobs(lamp, nominal_nm, measured_nm, ambient_c, status, verdict, reason, created_by, created_at)
                    VALUES (%s,%s,%s,%s,'done',%s,%s,'seed',%s) RETURNING id
                    """,
                    (lamp, nominal, measured, ambient, verdict, reason, now),
                ).fetchone()
                conn.execute(
                    "INSERT INTO temps(ambient_c, source, job_id, created_by, created_at) VALUES (%s,'seed',%s,'seed',%s)",
                    (ambient, row["id"], now),
                )
        conn.commit()


app = Litestar(
    route_handlers=[health, login, list_jobs, get_job, create_job, list_temps, create_temp, update_temp],
    on_startup=[on_startup],
)
