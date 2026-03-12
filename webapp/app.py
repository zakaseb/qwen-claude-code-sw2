"""
C-to-SysML YAML Web App — Multi-file MBSE reverse engineering pipeline.
Generates 10 YAML artifacts from C code following TEMPLATE structures
and PROCESS_MANUAL workflow phases.  Fully local, no external network calls.
"""
import json
import os
import uuid
import shutil
import zipfile
import io
import httpx
from pathlib import Path
from pydantic import BaseModel
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="C to SysML YAML", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

TEMPLATES_DIR = Path(__file__).parent / "templates"

LLM_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:24000")
LLM_BASE_URL_LITELLM = "http://127.0.0.1:4000"
LLM_API_KEY = os.environ.get("OPENAI_API_KEY", "sk-1234-miaw")
HF_MODEL = os.environ.get("HF_MODEL", "Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf")
MODEL_NAME = HF_MODEL.replace(".gguf", "") if HF_MODEL.endswith(".gguf") else HF_MODEL
MODEL_NAME_LITELLM = f"openai/{HF_MODEL}"

HTTPX_STREAM_TIMEOUT = httpx.Timeout(timeout=None, connect=30.0)

ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "/home/developer/workspace/sysml_artifacts"))
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Artifact pipeline — order follows PROCESS_MANUAL phases
# ---------------------------------------------------------------------------

ARTIFACT_PIPELINE = [
    {
        "filename": "00_metadata.yaml",
        "template": "TEMPLATE_00_metadata.yaml",
        "label": "Phase 1 — Metadata",
        "phase": 1,
        "depends_on": [],
        "system_prompt": (
            "You are an expert systems engineer performing reverse engineering.\n"
            "Analyze the C source code and generate 00_metadata.yaml.\n"
            "Focus on: model identification, ownership (owns/uses_external/context_only), "
            "dependencies (#include statements), standards compliance, code characteristics, "
            "and implementation notes.\n"
            "Fill ALL MANDATORY fields. Use the template structure EXACTLY."
        ),
    },
    {
        "filename": "01_requirements_diagram.yaml",
        "template": "TEMPLATE_01_requirements_diagram.yaml",
        "label": "Phase 2 — Requirements",
        "phase": 2,
        "depends_on": ["00_metadata.yaml"],
        "system_prompt": (
            "You are an expert requirements engineer performing reverse engineering.\n"
            "Extract requirements from the C source code and generate 01_requirements_diagram.yaml.\n"
            "Rules:\n"
            "- One function may yield multiple functional requirements\n"
            "- Each if-statement may indicate a conditional requirement\n"
            "- Data validation → interface requirement\n"
            "- Error handling → fault tolerance requirement\n"
            "- Write as SHALL statements. Assign priorities. Provide rationale.\n"
            "- Use ID format REQ-<MODULE>-<NUMBER>\n"
            "- Leave satisfiedBy/verifiedBy as empty lists (filled in later phases)\n"
            "Fill ALL MANDATORY fields. Use the template structure EXACTLY."
        ),
    },
    {
        "filename": "01b_verification_requirements.yaml",
        "template": "TEMPLATE_01b_verification_requirements.yaml",
        "label": "Phase 3 — Verification Strategy",
        "phase": 3,
        "depends_on": ["01_requirements_diagram.yaml"],
        "system_prompt": (
            "You are a test engineer defining verification strategy.\n"
            "For EACH requirement in 01_requirements_diagram.yaml, define a verification requirement.\n"
            "Rules:\n"
            "- Functional requirements → unit_test or integration_test\n"
            "- Interface requirements → code_review + unit_test\n"
            "- Performance requirements → performance_test + analysis\n"
            "- Safety requirements → multiple methods\n"
            "- Write step-by-step verification_approach\n"
            "- Define acceptance_criteria (what is PASS)\n"
            "- Use ID format VER-REQ-<NUMBER>\n"
            "Fill ALL MANDATORY fields. Use the template structure EXACTLY."
        ),
    },
    {
        "filename": "02_block_definition_diagram.yaml",
        "template": "TEMPLATE_02_block_definition_diagram.yaml",
        "label": "Phase 4a — Block Definition",
        "phase": 4,
        "depends_on": ["00_metadata.yaml", "01_requirements_diagram.yaml"],
        "system_prompt": (
            "You are a systems architect performing reverse engineering.\n"
            "Extract structure from C code and generate 02_block_definition_diagram.yaml.\n"
            "Rules:\n"
            "- Each function → one function_block with operations (BLK-<Name>)\n"
            "- Each struct/typedef → one value_type with values\n"
            "- Each #include external → one external block (signature only)\n"
            "- Global variables → context block reference\n"
            "- Every block MUST have a satisfies list linking to requirements\n"
            "Fill ALL MANDATORY fields. Use the template structure EXACTLY."
        ),
    },
    {
        "filename": "03_activity_diagram.yaml",
        "template": "TEMPLATE_03_activity_diagram.yaml",
        "label": "Phase 4b — Activity Diagram",
        "phase": 4,
        "depends_on": ["02_block_definition_diagram.yaml"],
        "system_prompt": (
            "You are a systems engineer extracting algorithm flow.\n"
            "Generate 03_activity_diagram.yaml from the C source code.\n"
            "Rules:\n"
            "- Map source lines to activity nodes\n"
            "- if/switch → DecisionNode + guards on edges\n"
            "- Function calls → CallBehaviorAction\n"
            "- Assignments/calculations → OpaqueAction\n"
            "- Loops → DecisionNode with back-edge\n"
            "- Start with InitialNode, end with ActivityFinalNode\n"
            "- Each activity references its block context (ACT-<FunctionName> → BLK-<FunctionName>)\n"
            "Fill ALL MANDATORY fields. Use the template structure EXACTLY."
        ),
    },
    {
        "filename": "04_state_machine_diagram.yaml",
        "template": "TEMPLATE_04_state_machine_diagram.yaml",
        "label": "Phase 4c — State Machine",
        "phase": 4,
        "depends_on": ["02_block_definition_diagram.yaml"],
        "system_prompt": (
            "You are a systems engineer analyzing state behavior.\n"
            "Generate 04_state_machine_diagram.yaml.\n"
            "FIRST determine if a state machine exists:\n"
            "- Look for operational modes, state variables, history-dependent behavior\n"
            "- If NO state machine: set metadata.status=not_applicable, state_machines=[], "
            "provide rationale, reference activity diagram as alternative\n"
            "- If YES: extract states, transitions, guards, events\n"
            "Fill ALL MANDATORY fields. Use the template structure EXACTLY."
        ),
    },
    {
        "filename": "05_sequence_diagram.yaml",
        "template": "TEMPLATE_05_sequence_diagram.yaml",
        "label": "Phase 4d — Sequence Diagram",
        "phase": 4,
        "depends_on": ["02_block_definition_diagram.yaml"],
        "system_prompt": (
            "You are a systems engineer extracting message interactions.\n"
            "Generate 05_sequence_diagram.yaml.\n"
            "Rules:\n"
            "- Function calls → synchronous_call messages\n"
            "- Data writes → synchronous_write messages\n"
            "- Returns → synchronous_return messages\n"
            "- Generate at least: success_path scenario and primary failure_path scenario\n"
            "- Lifelines must reference blocks from BDD\n"
            "Fill ALL MANDATORY fields. Use the template structure EXACTLY."
        ),
    },
    {
        "filename": "06_parametric_diagram.yaml",
        "template": "TEMPLATE_06_parametric_diagram.yaml",
        "label": "Phase 4e — Parametric Diagram",
        "phase": 4,
        "depends_on": ["02_block_definition_diagram.yaml"],
        "system_prompt": (
            "You are a systems engineer extracting mathematical constraints.\n"
            "Generate 06_parametric_diagram.yaml.\n"
            "Rules:\n"
            "- sizeof() calculations → constraint blocks\n"
            "- Performance timing → budgets\n"
            "- Array dimensions → constraints\n"
            "- Physical ranges/limits → constraints\n"
            "- Include equations with results\n"
            "Fill ALL MANDATORY fields. Use the template structure EXACTLY."
        ),
    },
    {
        "filename": "07_allocations.yaml",
        "template": "TEMPLATE_07_allocations.yaml",
        "label": "Phase 4f — Allocations",
        "phase": 4,
        "depends_on": ["02_block_definition_diagram.yaml"],
        "system_prompt": (
            "You are a systems engineer mapping logical to physical.\n"
            "Generate 07_allocations.yaml.\n"
            "Rules:\n"
            "- Functions → processor (usually TBD at module level)\n"
            "- Global data → RAM sections (.data/.bss)\n"
            "- Const data → ROM sections (.rodata)\n"
            "- Code → Flash/ROM (.text)\n"
            "- Local variables → stack\n"
            "- Mark system-level unknowns as TBD\n"
            "Fill ALL MANDATORY fields. Use the template structure EXACTLY."
        ),
    },
    {
        "filename": "08_test_cases.yaml",
        "template": "TEMPLATE_08_test_cases.yaml",
        "label": "Phase 5 — Test Cases",
        "phase": 5,
        "depends_on": ["01_requirements_diagram.yaml", "01b_verification_requirements.yaml"],
        "system_prompt": (
            "You are a test engineer creating detailed test specifications.\n"
            "Generate 08_test_cases.yaml.\n"
            "Rules:\n"
            "- Each verification requirement needs ≥1 test case\n"
            "- Critical requirements → multiple test cases (positive/negative)\n"
            "- Use ID format TC-<NUMBER>\n"
            "- Define clear numbered test steps\n"
            "- Specify assertions and pass criteria\n"
            "- Link: implements → VER-REQ-XXX, verifies → REQ-XXX\n"
            "- Include unit_test, code_review types as appropriate\n"
            "Fill ALL MANDATORY fields. Use the template structure EXACTLY."
        ),
    },
    {
        "filename": "09_generation_config.yaml",
        "template": "TEMPLATE_09_generation_config.yaml",
        "label": "Config — Generation",
        "phase": 6,
        "depends_on": [],
        "system_prompt": (
            "You are a build/tooling engineer.\n"
            "Generate 09_generation_config.yaml for this C module.\n"
            "Configure: C code generation paths, documentation generation, "
            "validation rules. Reference the module name from metadata.\n"
            "Fill ALL MANDATORY fields. Use the template structure EXACTLY."
        ),
    },
]


def _load_template(name: str) -> str:
    path = TEMPLATES_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _build_user_prompt(artifact: dict, c_code: str, prior: dict[str, str]) -> str:
    """Assemble the user prompt: template + C code + prior artifacts context."""
    parts = []
    parts.append(f"=== TEMPLATE ({artifact['filename']}) ===\n")
    parts.append(_load_template(artifact["template"]))
    parts.append("\n\n=== C SOURCE CODE ===\n")
    parts.append(c_code)
    for dep in artifact["depends_on"]:
        if dep in prior and prior[dep]:
            parts.append(f"\n\n=== CONTEXT: {dep} (previously generated) ===\n")
            # Truncate large prior artifacts to keep within context window
            content = prior[dep]
            if len(content) > 6000:
                content = content[:6000] + "\n... (truncated) ..."
            parts.append(content)
    parts.append(
        f"\n\nGenerate ONLY the YAML content for {artifact['filename']}. "
        "Output valid YAML only — no markdown fences, no explanatory text."
    )
    return "\n".join(parts)


def _extract_yaml(text: str) -> str:
    """Strip markdown fences if the LLM wraps output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        out = []
        in_block = False
        for line in lines:
            if line.startswith("```yaml") or line.startswith("```yml") or (line.startswith("```") and not in_block):
                in_block = True
                continue
            if in_block and line.strip() == "```":
                break
            if in_block:
                out.append(line)
        return "\n".join(out) if out else text
    return text


def _call_llm_stream(system_prompt: str, user_prompt: str):
    """Yields (chunk_text, is_done) tuples via SSE streaming from LLM."""
    for base_url, model in [(LLM_BASE_URL_LITELLM, MODEL_NAME_LITELLM), (LLM_BASE_URL, MODEL_NAME)]:
        url = f"{base_url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 4096,
            "temperature": 0.3,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=HTTPX_STREAM_TIMEOUT) as client:
                with client.stream("POST", url, json=payload, headers=headers) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line or line == "data: [DONE]":
                            continue
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                part = delta.get("content", "")
                                if part:
                                    yield part
                            except (json.JSONDecodeError, KeyError):
                                pass
            return
        except Exception:
            if base_url == LLM_BASE_URL:
                raise
            continue


def _generate_pipeline_stream(c_code: str, run_id: str):
    """Generator: streams NDJSON events for the full multi-file pipeline."""
    out_dir = ARTIFACTS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    prior: dict[str, str] = {}
    total = len(ARTIFACT_PIPELINE)

    for idx, artifact in enumerate(ARTIFACT_PIPELINE):
        yield json.dumps({
            "event": "file_start",
            "index": idx,
            "total": total,
            "filename": artifact["filename"],
            "label": artifact["label"],
        }) + "\n"

        system_prompt = artifact["system_prompt"]
        user_prompt = _build_user_prompt(artifact, c_code, prior)
        parts = []
        try:
            for chunk in _call_llm_stream(system_prompt, user_prompt):
                parts.append(chunk)
                yield json.dumps({"event": "chunk", "index": idx, "text": chunk}) + "\n"
        except Exception as e:
            yield json.dumps({"event": "file_error", "index": idx, "error": str(e)}) + "\n"
            prior[artifact["filename"]] = ""
            continue

        raw = "".join(parts).strip()
        yaml_content = _extract_yaml(raw)
        filepath = out_dir / artifact["filename"]
        filepath.write_text(yaml_content, encoding="utf-8")
        prior[artifact["filename"]] = yaml_content

        yield json.dumps({
            "event": "file_done",
            "index": idx,
            "filename": artifact["filename"],
            "yaml": yaml_content,
        }) + "\n"

    yield json.dumps({
        "event": "pipeline_done",
        "run_id": run_id,
        "files": [a["filename"] for a in ARTIFACT_PIPELINE],
    }) + "\n"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    c_code: str = ""


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text())


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/generate-stream")
async def generate_stream(req: GenerateRequest):
    c_code = (req.c_code or "").strip()
    if not c_code:
        raise HTTPException(status_code=400, detail="C code is required")
    run_id = str(uuid.uuid4())
    return StreamingResponse(
        _generate_pipeline_stream(c_code, run_id),
        media_type="application/x-ndjson",
    )


@app.post("/api/upload-and-generate-stream")
async def upload_and_generate_stream(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith((".c", ".h")):
        raise HTTPException(status_code=400, detail="Please upload a .c or .h file")
    c_code = (await file.read()).decode("utf-8", errors="replace")
    run_id = str(uuid.uuid4())
    return StreamingResponse(
        _generate_pipeline_stream(c_code, run_id),
        media_type="application/x-ndjson",
    )


def _safe_artifact_path(run_id: str, filename: str | None = None) -> Path:
    """Resolve and validate that the path stays within ARTIFACTS_DIR."""
    if filename:
        target = (ARTIFACTS_DIR / run_id / filename).resolve()
    else:
        target = (ARTIFACTS_DIR / run_id).resolve()
    if not target.is_relative_to(ARTIFACTS_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid path")
    return target


@app.get("/api/download/{run_id}/{filename}")
async def download_file(run_id: str, filename: str):
    filepath = _safe_artifact_path(run_id, filename)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=filepath, filename=filename, media_type="application/x-yaml")


@app.get("/api/download-all/{run_id}")
async def download_all(run_id: str):
    out_dir = _safe_artifact_path(run_id)
    if not out_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out_dir.iterdir()):
            if f.is_file() and f.suffix in (".yaml", ".yml"):
                zf.write(f, f.name)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="sysml_{run_id}.zip"'},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
