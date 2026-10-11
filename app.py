import os
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from weasyprint import HTML


APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", APP_DIR / "output"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Resume PDF Renderer", version="1.0.0")
app.mount("/files", StaticFiles(directory=str(OUTPUT_DIR)), name="files")


class RenderRequest(BaseModel):
    html: str = Field(..., description="完整 HTML 简历")
    file_name: str = Field("resume.pdf", description="下载时显示的文件名")


def safe_file_name(value: str) -> str:
    value = Path(value or "resume.pdf").name
    value = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", value).strip("._")
    if not value:
        value = "resume.pdf"
    if not value.lower().endswith(".pdf"):
        value += ".pdf"
    return value


def public_file_url(request: Request, stored_name: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/files/{stored_name}"
    return f"{str(request.base_url).rstrip('/')}/files/{stored_name}"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/render")
async def render_resume(request: Request) -> JSONResponse:
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        payload = await request.json()
    else:
        form = await request.form()
        payload = dict(form)

    html = payload.get("html")
    file_name = str(payload.get("file_name") or "resume.pdf")

    if not isinstance(html, str) or not html.strip():
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "html 不能为空"},
        )

    stored_name = f"{uuid.uuid4().hex}.pdf"
    output_path = OUTPUT_DIR / stored_name

    try:
        HTML(string=html, base_url=str(APP_DIR)).write_pdf(str(output_path))
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"PDF 渲染失败: {exc}"},
        )

    return JSONResponse(
        content={
            "success": True,
            "file_name": safe_file_name(file_name),
            "file_url": public_file_url(request, stored_name),
            "message": "PDF 已生成",
        }
    )
