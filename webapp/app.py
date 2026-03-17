"""
C ↔ SysML YAML Web App — Bidirectional MBSE pipeline.

Direction A (C → YAML): Reverse-engineer C source into 11 SysML YAML artifacts
Direction B (YAML → C): Forward-generate C code from SysML YAML artifacts

Fully local, no external network calls.
"""
import json
import os
import re
import uuid
import shutil
import zipfile
import io
import httpx
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="C ↔ SysML YAML", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

TEMPLATES_DIR = Path(__file__).parent / "templates"

LLM_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:24000")
LLM_BASE_URL_LITELLM = "http://127.0.0.1:4000"
LLM_API_KEY = os.environ.get("OPENAI_API_KEY", "sk-1234-miaw")
HF_MODEL = os.environ.get("HF_MODEL", "Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf")
MODEL_NAME = HF_MODEL.replace(".gguf", "") if HF_MODEL.endswith(".gguf") else HF_MODEL
MODEL_NAME_LITELLM = f"openai/{HF_MODEL}"

HTTPX_STREAM_TIMEOUT = httpx.Timeout(timeout=None, connect=300.0)

ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "/home/developer/workspace/sysml_artifacts"))
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

CODEGEN_DIR = Path(os.environ.get("CODEGEN_DIR", "/home/developer/workspace/codegen_output"))
CODEGEN_DIR.mkdir(parents=True, exist_ok=True)


# ===================================================================
# Shared LLM helpers
# ===================================================================

def _call_llm_stream(system_prompt: str, user_prompt: str, max_tokens: int = 4096):
    """Yields text chunks via SSE streaming from LLM.

    Tries LiteLLM proxy first, then falls back to direct llama-server.
    Falls back if a backend returns HTTP 200 but zero content tokens.
    """
    for base_url, model in [(LLM_BASE_URL_LITELLM, MODEL_NAME_LITELLM),
                            (LLM_BASE_URL, MODEL_NAME)]:
        url = f"{base_url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json"}
        try:
            yielded = False
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
                                    yielded = True
                                    yield part
                            except (json.JSONDecodeError, KeyError):
                                pass
            if yielded:
                return
        except Exception:
            if base_url == LLM_BASE_URL:
                raise
            continue
    raise RuntimeError("All LLM backends returned empty responses")


def _extract_fenced(text: str, lang_hint: str = "") -> str:
    """Strip markdown fences if the LLM wraps output in ```."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    out = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if not in_block and stripped.startswith("```"):
            in_block = True
            continue
        if in_block and stripped == "```":
            break
        if in_block:
            out.append(line)
    return "\n".join(out) if out else text


def _safe_path(base: Path, *parts: str) -> Path:
    """Resolve and validate that the path stays within base."""
    target = (base / Path(*parts)).resolve()
    if not target.is_relative_to(base.resolve()):
        raise HTTPException(status_code=400, detail="Invalid path")
    return target


# ===================================================================
# Direction A: C → YAML  (reverse engineering)
# ===================================================================

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


def _build_yaml_user_prompt(artifact: dict, c_code: str,
                            prior: dict[str, str]) -> str:
    parts = []
    parts.append(f"=== TEMPLATE ({artifact['filename']}) ===\n")
    parts.append(_load_template(artifact["template"]))
    parts.append("\n\n=== C SOURCE CODE ===\n")
    parts.append(c_code)
    for dep in artifact["depends_on"]:
        if dep in prior and prior[dep]:
            parts.append(f"\n\n=== CONTEXT: {dep} (previously generated) ===\n")
            content = prior[dep]
            if len(content) > 6000:
                content = content[:6000] + "\n... (truncated) ..."
            parts.append(content)
    parts.append(
        f"\n\nGenerate ONLY the YAML content for {artifact['filename']}. "
        "Output valid YAML only — no markdown fences, no explanatory text."
    )
    return "\n".join(parts)


def _generate_yaml_pipeline_stream(c_code: str, run_id: str):
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
        user_prompt = _build_yaml_user_prompt(artifact, c_code, prior)
        parts = []
        try:
            for chunk in _call_llm_stream(system_prompt, user_prompt):
                parts.append(chunk)
                yield json.dumps({"event": "chunk", "index": idx,
                                  "text": chunk}) + "\n"
        except Exception as e:
            yield json.dumps({"event": "file_error", "index": idx,
                              "error": str(e)}) + "\n"
            prior[artifact["filename"]] = ""
            continue

        raw = "".join(parts).strip()
        if not raw:
            yield json.dumps({"event": "file_error", "index": idx,
                              "error": "LLM returned empty response"}) + "\n"
            prior[artifact["filename"]] = ""
            continue

        yaml_content = _extract_fenced(raw)
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


# ===================================================================
# Direction B: YAML → C  (forward generation)
# ===================================================================

C_CODE_PIPELINE = [
    {
        "output_filename": "module.h",
        "label": "Header file (.h)",
        "uses": [
            "00_metadata.yaml",
            "02_block_definition_diagram.yaml",
            "06_parametric_diagram.yaml",
            "09_generation_config.yaml",
        ],
        "system_prompt": (
            "You are an expert embedded C developer generating a header file from "
            "SysML YAML model artifacts.\n\n"
            "Generate a COMPLETE, CORRECT, COMPILABLE C header file (.h) that precisely "
            "implements the structures and interfaces described in the YAML artifacts.\n\n"
            "The header file MUST include:\n"
            "1. File header comment with module name, description, and generation notice\n"
            "2. Include guard (#ifndef/#define/#endif)\n"
            "3. All required #include directives (system and project, from metadata dependencies)\n"
            "4. All #define constants (from metadata code_characteristics.constants and "
            "parametric diagram constraints)\n"
            "5. All typedef and struct definitions (from block_definition_diagram value_type "
            "blocks — reproduce EVERY field with correct types, offsets, and sizes)\n"
            "6. All enum definitions (if any value_type blocks define enumerations)\n"
            "7. All function prototypes (from block_definition_diagram function_block operations — "
            "match return types, parameter types, parameter names, and directions exactly)\n"
            "8. Extern declarations for any global variables\n\n"
            "Rules:\n"
            "- Use the EXACT types, names, and sizes from the YAML artifacts\n"
            "- Respect #pragma pack directives from metadata/BDD packing fields\n"
            "- Add brief Doxygen-style comments for structs and function prototypes\n"
            "- Output ONLY valid C code — no markdown fences, no explanatory text"
        ),
    },
    {
        "output_filename": "module.c",
        "label": "Implementation file (.c)",
        "uses": [
            "00_metadata.yaml",
            "01_requirements_diagram.yaml",
            "02_block_definition_diagram.yaml",
            "03_activity_diagram.yaml",
            "04_state_machine_diagram.yaml",
            "05_sequence_diagram.yaml",
            "06_parametric_diagram.yaml",
            "07_allocations.yaml",
        ],
        "system_prompt": (
            "You are an expert embedded C developer generating an implementation file "
            "from SysML YAML model artifacts.\n\n"
            "Generate a COMPLETE, CORRECT, COMPILABLE C implementation file (.c) that "
            "precisely implements the behavior described in the YAML artifacts.\n\n"
            "The implementation file MUST include:\n"
            "1. File header comment\n"
            "2. #include of the corresponding header file and any other required headers "
            "(from metadata dependencies)\n"
            "3. Static/global variable definitions (from allocations — variables in .data/.bss)\n"
            "4. EVERY function listed in block_definition_diagram function_blocks, fully "
            "implemented:\n"
            "   - Follow the algorithm flow from activity_diagram nodes and edges EXACTLY\n"
            "   - Implement decision nodes as if/else or switch statements matching guard conditions\n"
            "   - Implement call behavior actions as function calls\n"
            "   - Implement opaque actions as the described operations\n"
            "   - If state_machine_diagram is applicable, implement state transitions with "
            "switch/case or if/else matching states and transitions\n"
            "   - Follow the sequence_diagram message ordering for function call sequences\n"
            "5. Respect parametric_diagram constraints (array sizes, struct sizes, limits)\n"
            "6. Each function must satisfy the requirements listed in its block's satisfies field\n\n"
            "Rules:\n"
            "- The code must compile with a standard C99/C11 compiler\n"
            "- Use the EXACT function signatures from the block_definition_diagram\n"
            "- Implement ALL functions — do not leave stubs or TODOs\n"
            "- Add brief comments referencing requirement IDs where behavior satisfies them\n"
            "- Output ONLY valid C code — no markdown fences, no explanatory text"
        ),
    },
    {
        "output_filename": "test_module.c",
        "label": "Unit tests (test_.c)",
        "uses": [
            "00_metadata.yaml",
            "01b_verification_requirements.yaml",
            "02_block_definition_diagram.yaml",
            "08_test_cases.yaml",
        ],
        "system_prompt": (
            "You are an expert test engineer generating a Unity C test file from "
            "SysML YAML model artifacts.\n\n"
            "Generate a COMPLETE, CORRECT, COMPILABLE Unity C test file that implements "
            "ALL test cases defined in the test_cases YAML artifact.\n\n"
            "The test file MUST include:\n"
            "1. File header comment\n"
            "2. #include \"unity.h\" and the module header\n"
            "3. setUp() and tearDown() functions\n"
            "4. A TEST function for EVERY test case in 08_test_cases.yaml:\n"
            "   - Function name: test_<test_case_id> (e.g., test_TC_001)\n"
            "   - Implement each test_step as described\n"
            "   - Translate assertions to TEST_ASSERT_EQUAL, TEST_ASSERT_TRUE, "
            "TEST_ASSERT_EQUAL_HEX, etc.\n"
            "   - Add a comment with the test case ID and what it verifies\n"
            "5. A main() function that calls UNITY_BEGIN(), RUN_TEST() for every test, "
            "and UNITY_END()\n"
            "6. For code_review type test cases, generate a commented-out placeholder "
            "with the review checklist\n\n"
            "Rules:\n"
            "- Implement ALL test cases — do not skip any\n"
            "- Match test data from the YAML exactly\n"
            "- Each TEST function must reference its verification requirement ID\n"
            "- Output ONLY valid C code — no markdown fences, no explanatory text"
        ),
    },
]


def _load_artifacts(artifact_dir: Path) -> dict[str, str]:
    """Load all YAML files from a directory into a dict."""
    artifacts = {}
    if artifact_dir.is_dir():
        for f in sorted(artifact_dir.iterdir()):
            if f.is_file() and f.suffix in (".yaml", ".yml"):
                artifacts[f.name] = f.read_text(encoding="utf-8")
    return artifacts


def _build_c_user_prompt(step: dict, artifacts: dict[str, str],
                         prior_c: dict[str, str]) -> str:
    parts = []
    for yaml_name in step["uses"]:
        if yaml_name in artifacts and artifacts[yaml_name]:
            content = artifacts[yaml_name]
            if len(content) > 8000:
                content = content[:8000] + "\n... (truncated) ..."
            parts.append(f"=== {yaml_name} ===\n{content}\n")
    if not parts:
        parts.append("(No YAML artifacts provided)\n")
    if prior_c:
        for fname, code in prior_c.items():
            header = code
            if len(header) > 4000:
                header = header[:4000] + "\n/* ... truncated ... */"
            parts.append(f"\n=== Previously generated: {fname} ===\n{header}\n")
    parts.append(
        f"\nGenerate the COMPLETE C source code for {step['output_filename']}. "
        "Output ONLY valid C code — no markdown fences, no explanatory text."
    )
    return "\n".join(parts)


def _generate_c_pipeline_stream(artifacts: dict[str, str], c_run_id: str):
    """Stream NDJSON events for the C code generation pipeline."""
    out_dir = CODEGEN_DIR / c_run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    prior_c: dict[str, str] = {}
    total = len(C_CODE_PIPELINE)

    for idx, step in enumerate(C_CODE_PIPELINE):
        yield json.dumps({
            "event": "c_file_start",
            "index": idx,
            "total": total,
            "filename": step["output_filename"],
            "label": step["label"],
        }) + "\n"

        system_prompt = step["system_prompt"]
        user_prompt = _build_c_user_prompt(step, artifacts, prior_c)
        parts = []
        try:
            for chunk in _call_llm_stream(system_prompt, user_prompt,
                                          max_tokens=8192):
                parts.append(chunk)
                yield json.dumps({"event": "c_chunk", "index": idx,
                                  "text": chunk}) + "\n"
        except Exception as e:
            yield json.dumps({"event": "c_file_error", "index": idx,
                              "error": str(e)}) + "\n"
            continue

        raw = "".join(parts).strip()
        if not raw:
            yield json.dumps({"event": "c_file_error", "index": idx,
                              "error": "LLM returned empty response"}) + "\n"
            continue

        c_content = _extract_fenced(raw, "c")
        filepath = out_dir / step["output_filename"]
        filepath.write_text(c_content, encoding="utf-8")
        prior_c[step["output_filename"]] = c_content

        yield json.dumps({
            "event": "c_file_done",
            "index": idx,
            "filename": step["output_filename"],
            "code": c_content,
        }) + "\n"

    yield json.dumps({
        "event": "c_pipeline_done",
        "c_run_id": c_run_id,
        "files": [s["output_filename"] for s in C_CODE_PIPELINE],
    }) + "\n"


# ===================================================================
# Routes
# ===================================================================

class GenerateRequest(BaseModel):
    c_code: str = ""


class GenerateCRequest(BaseModel):
    run_id: str = ""


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text())


@app.get("/health")
async def health():
    return {"status": "ok"}


# --- Direction A: C → YAML ---

@app.post("/api/generate-stream")
async def generate_stream(req: GenerateRequest):
    c_code = (req.c_code or "").strip()
    if not c_code:
        raise HTTPException(status_code=400, detail="C code is required")
    run_id = str(uuid.uuid4())
    return StreamingResponse(
        _generate_yaml_pipeline_stream(c_code, run_id),
        media_type="application/x-ndjson",
    )


@app.post("/api/upload-and-generate-stream")
async def upload_and_generate_stream(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith((".c", ".h")):
        raise HTTPException(status_code=400, detail="Please upload a .c or .h file")
    c_code = (await file.read()).decode("utf-8", errors="replace")
    run_id = str(uuid.uuid4())
    return StreamingResponse(
        _generate_yaml_pipeline_stream(c_code, run_id),
        media_type="application/x-ndjson",
    )


@app.get("/api/download/{run_id}/{filename}")
async def download_file(run_id: str, filename: str):
    filepath = _safe_path(ARTIFACTS_DIR, run_id, filename)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=filepath, filename=filename,
                        media_type="application/x-yaml")


@app.get("/api/download-all/{run_id}")
async def download_all(run_id: str):
    out_dir = _safe_path(ARTIFACTS_DIR, run_id)
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
        headers={"Content-Disposition":
                 f'attachment; filename="sysml_{run_id}.zip"'},
    )


# --- Direction B: YAML → C ---

@app.post("/api/generate-c-stream")
async def generate_c_stream(req: GenerateCRequest):
    """Generate C code from previously generated YAML artifacts (by run_id)."""
    run_id = (req.run_id or "").strip()
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id is required")
    artifact_dir = _safe_path(ARTIFACTS_DIR, run_id)
    if not artifact_dir.exists():
        raise HTTPException(status_code=404, detail="YAML artifacts not found")
    artifacts = _load_artifacts(artifact_dir)
    if not artifacts:
        raise HTTPException(status_code=400, detail="No YAML files found in run")
    c_run_id = str(uuid.uuid4())
    return StreamingResponse(
        _generate_c_pipeline_stream(artifacts, c_run_id),
        media_type="application/x-ndjson",
    )


@app.post("/api/upload-yaml-generate-c-stream")
async def upload_yaml_generate_c_stream(file: UploadFile = File(...)):
    """Generate C code from uploaded YAML artifacts (ZIP file)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="File required")
    raw = await file.read()
    artifacts: dict[str, str] = {}

    if file.filename.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for name in zf.namelist():
                    basename = Path(name).name
                    if basename.endswith((".yaml", ".yml")) and not name.startswith("__"):
                        artifacts[basename] = zf.read(name).decode("utf-8",
                                                                    errors="replace")
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid ZIP file")
    elif file.filename.lower().endswith((".yaml", ".yml")):
        artifacts[file.filename] = raw.decode("utf-8", errors="replace")
    else:
        raise HTTPException(status_code=400,
                            detail="Upload a .zip of YAML files or a single .yaml file")

    if not artifacts:
        raise HTTPException(status_code=400, detail="No YAML files found in upload")

    c_run_id = str(uuid.uuid4())
    return StreamingResponse(
        _generate_c_pipeline_stream(artifacts, c_run_id),
        media_type="application/x-ndjson",
    )


@app.get("/api/download-c/{c_run_id}/{filename}")
async def download_c_file(c_run_id: str, filename: str):
    filepath = _safe_path(CODEGEN_DIR, c_run_id, filename)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=filepath, filename=filename,
                        media_type="text/x-csrc")


@app.get("/api/download-c-all/{c_run_id}")
async def download_c_all(c_run_id: str):
    out_dir = _safe_path(CODEGEN_DIR, c_run_id)
    if not out_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out_dir.iterdir()):
            if f.is_file() and f.suffix in (".c", ".h"):
                zf.write(f, f.name)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="generated_c_{c_run_id}.zip"'},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
