import os
from datetime import datetime, timedelta, timezone

import psycopg
from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.status_codes import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
)
from passlib.context import CryptContext
from psycopg.rows import dict_row
from pydantic import BaseModel

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54395/spectrum")
SECRET = os.environ.get("JWT_SECRET", "spectrum-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "calibrator": {"role": "writer", "password_hash": pwd.hash("calib123456")},
    "inspector": {"role": "reader", "password_hash": pwd.hash("insp123456")},
}

# 环境温度为必填：建表即 NOT NULL，随单写入、随单冻结。
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id serial PRIMARY KEY,
    lamp text NOT NULL,
    nominal_nm double precision NOT NULL,
    measured_nm double precision NOT NULL,
    ambient_c double precision NOT NULL,
    status text NOT NULL,
    verdict text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
"""

# 旧库迁移：补列、回填历史单、收紧为 NOT NULL。
MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ambient_c double precision",
    "UPDATE jobs SET ambient_c = 25.0 WHERE ambient_c IS NULL",
    "ALTER TABLE jobs ALTER COLUMN ambient_c SET NOT NULL",
]

# 温度随单冻结：任何事后改温表/改旧单温度的 UPDATE 一律拒绝。
FREEZE_TRIGGER = """
CREATE OR REPLACE FUNCTION freeze_ambient_c() RETURNS trigger AS $$
BEGIN
    IF NEW.ambient_c IS DISTINCT FROM OLD.ambient_c THEN
        RAISE EXCEPTION '环境温度已随单冻结，禁止修改旧单温度';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

FREEZE_TRIGGER_DROP = "DROP TRIGGER IF EXISTS trg_freeze_ambient_c ON jobs"
FREEZE_TRIGGER_CREATE = """
CREATE TRIGGER trg_freeze_ambient_c BEFORE UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION freeze_ambient_c()
"""

JOB_COLUMNS = "id, lamp, nominal_nm, measured_nm, ambient_c, status, verdict, reason, created_by"


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    lamp: str
    nominal_nm: float
    measured_nm: float
    # 可选默认 None 仅为了能在缺字段时返回明确的“缺温”拒收，而不是框架 422。
    ambient_c: float | None = None


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
            f"SELECT {JOB_COLUMNS} FROM jobs ORDER BY id DESC"
        ).fetchall()
        return list(rows)


@get("/api/jobs/{job_id:int}")
async def get_job(request: Request, job_id: int) -> dict:
    user_from_request(request)
    with connect() as conn:
        row = conn.execute(
            f"SELECT {JOB_COLUMNS} FROM jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="任务不存在")
        return dict(row)


@post("/api/jobs")
async def create_job(request: Request, data: JobIn) -> dict:
    user = user_from_request(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅校准员可提交")
    # 缺温门禁：没有环境温度的校准单一律拒收，并明确写明“缺温”。
    if data.ambient_c is None:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="缺温拒收：环境温度为必填项，请填写环境温度（℃）后再提交",
        )
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO jobs(lamp, nominal_nm, measured_nm, ambient_c, status, verdict, reason, created_by, created_at)
            VALUES (%s,%s,%s,%s,'pending','','',%s,%s) RETURNING id
            """,
            (
                data.lamp.strip(),
                data.nominal_nm,
                data.measured_nm,
                data.ambient_c,
                user["username"],
                datetime.now(timezone.utc),
            ),
        ).fetchone()
        conn.commit()
        return {"id": row["id"], "status": "pending", "ambient_c": data.ambient_c}


def on_startup() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)
        for stmt in MIGRATIONS:
            conn.execute(stmt)
        conn.execute(FREEZE_TRIGGER)
        conn.execute(FREEZE_TRIGGER_DROP)
        conn.execute(FREEZE_TRIGGER_CREATE)
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            conn.execute(
                """
                INSERT INTO jobs(lamp, nominal_nm, measured_nm, ambient_c, status, verdict, reason, created_by, created_at)
                VALUES
                ('氦灯-587', 587.56, 587.50, 24.5, 'done', '合格', '偏差 0.0600 nm 在允差内', 'seed', %s),
                ('汞灯-546', 546.07, 546.30, 24.5, 'done', '超差', '偏差 0.2300 nm 超过允差 0.08', 'seed', %s)
                """,
                (now, now),
            )
        conn.commit()


app = Litestar(route_handlers=[health, login, list_jobs, get_job, create_job], on_startup=[on_startup])
