# Qwen Claude Code Local Development Environment

This repository provides a localized Docker-based setup for running the Qwen3-Coder-30B-A3B-Instruct model with Claude Code integration.

## Overview

This setup allows you to run a local LLM (Qwen3-Coder-30B-A3B-Instruct) using llama.cpp server and access it through Claude Code via litellm proxy. The environment is containerized and can be run completely offline after initial setup.

## Features

- **Local Execution**: Run code generation models entirely locally without internet access after initial download
- **Claude Code API compatible** through litellm proxy
- **Bidirectional C ↔ SysML MBSE Pipeline**: A web-based UI supporting both directions — reverse-engineer C source code into SysML YAML artifacts, and forward-generate complete C code from YAML artifacts. Fully localized with zero data leakage.

## C-to-SysML MBSE Pipeline

The pipeline reverse-engineers C source code into 11 structured SysML YAML artifacts, following the PROCESS_MANUAL workflow used in Model-Based Systems Engineering (MBSE). Each artifact is generated sequentially by the local LLM, using the corresponding template as structural guidance and previously generated artifacts as context — exactly as the process manual prescribes.

### Generated Artifacts

The pipeline produces the following files, organized by workflow phase:

| Phase | File | Purpose |
|-------|------|---------|
| 1 — Situation Awareness | `00_metadata.yaml` | Module identification, ownership, dependencies, standards compliance, code characteristics |
| 2 — Requirements Extraction | `01_requirements_diagram.yaml` | Functional, interface, performance, safety, and security requirements as SHALL statements |
| 3 — Verification Strategy | `01b_verification_requirements.yaml` | Verification method, approach, and acceptance criteria for each requirement |
| 4a — Block Definition | `02_block_definition_diagram.yaml` | Functions as blocks with operations, structs as value types, external dependencies |
| 4b — Activity Diagram | `03_activity_diagram.yaml` | Algorithm flow: nodes, edges, decision/merge, mapped to source code lines |
| 4c — State Machine | `04_state_machine_diagram.yaml` | State-based behavior (or explicit not_applicable determination with rationale) |
| 4d — Sequence Diagram | `05_sequence_diagram.yaml` | Message interactions for success and failure scenarios |
| 4e — Parametric Diagram | `06_parametric_diagram.yaml` | Mathematical constraints, sizeof calculations, performance budgets |
| 4f — Allocations | `07_allocations.yaml` | Logical-to-physical mapping: functions→CPU, data→RAM/Flash, code→ROM |
| 5 — Test Generation | `08_test_cases.yaml` | Detailed test specifications implementing the verification requirements |
| 6 — Generation Config | `09_generation_config.yaml` | Build, documentation, and output generation configuration |

### Templates and Process Manual

All templates (`TEMPLATE_00` through `TEMPLATE_09`) and the `PROCESS_MANUAL.yaml` are bundled in `webapp/templates/`. They define the exact YAML structure, mandatory/optional fields, and inter-artifact dependencies. The LLM prompt for each artifact includes:

1. The corresponding template (structural guide)
2. The input C source code
3. Context from previously generated artifacts (as required by the dependency chain in the process manual)

### How It Works

The backend (`webapp/app.py`) runs a FastAPI server that:

1. Accepts C code via paste or file upload
2. Iterates through the 11-artifact pipeline in order
3. For each artifact, constructs a prompt from the template + C code + prior artifacts
4. Streams the LLM response back to the frontend as NDJSON events (`file_start`, `chunk`, `file_done`, `pipeline_done`)
5. Saves all generated files into a per-run output directory

The frontend (`webapp/static/`) provides:

- A pipeline progress tracker showing each file's generation status in real time
- Click-to-preview tabs for inspecting any generated artifact
- A **Download All (ZIP)** button to retrieve all 11 YAML files at once
- Individual file download via the API

## SysML YAML → C Code Generation (Forward Direction)

After generating YAML artifacts (or by uploading existing ones), the pipeline can generate complete, compilable C code:

| File | Purpose |
|------|---------|
| `module.h` | Header — include guards, system/project includes, constants, struct/typedef definitions, function prototypes. Derived from metadata, BDD, parametric diagram, and generation config. |
| `module.c` | Implementation — complete function implementations following the activity diagram flow, state machine transitions, sequence diagram ordering, and parametric constraints. Satisfies all requirements from the requirements diagram. |
| `test_module.c` | Unit tests — Unity C framework test file implementing every test case from `08_test_cases.yaml` with proper assertions, test data, and traceability to verification requirements. |

### Two ways to use:

1. **From C → YAML → C roundtrip**: Generate YAML artifacts first, then click **Generate C Code from These Artifacts**
2. **From uploaded YAML**: Switch to the **SysML YAML → C Code** tab and upload a ZIP of your YAML artifacts

Each generated C file is streamed in real time with live preview. Download individually or as a ZIP.

## What does not work
- Think, Ultrathing etc. reasoning_effort is currently dropped as non-thinking model is currently used. To use a Qwen thinking/non-thinking model a litellm hook needs to be written to add ` /think`  ` /nothink` tags to requests. Or wait until litellm support Qwen API.

## Requirements

- Docker Engine (version 20.10 or higher)
- At least 18GB of free disk space for the model
- For GPU acceleration: NVIDIA GPU with CUDA support and nvidia-docker2
- Internet connection for initial model download (subsequent runs can be offline)

> Note: The current settings are targeted for a GPU with 24GB VRAM or more. The code was tested on an RTX 3090. If you have less VRAM available, you'll need to adjust the following settings in the Dockerfile:
> - `LLAMA_ARG_N_GPU_LAYERS`
> - `LLAMA_ARG_CTX_SIZE`
> - `LLAMA_ARG_N_PREDICT`

## Quick Start

### Build the Docker Image

```bash
./build.sh
```
### Run the Container

For network access:
```bash
./run.sh
```

For offline mode (no internet required):
```bash
./run_offline.sh
```

### Run with Web UI (C-to-SysML Pipeline)

```bash
./run_web.sh
```

Then open `http://localhost:8080` in your browser.

1. Paste (or upload) your C source code
2. Click **Generate SysML Artifacts**
3. Watch the pipeline generate 11 YAML files in sequence, each following a specific MBSE template
4. Click any file in the pipeline to preview its contents
5. Click **Download All (ZIP)** to download all generated artifacts

## Testing

After running, you can test the setup with:

- Claude API compatibility: `./test_anthropic.sh`
- Reasoning capabilities: `./test_anthropic_reasoning.sh`
- OpenAI API compatibility: `./test_openai.sh`

## Configuration

The Dockerfile sets various environment variables for optimal performance:

- Model parameters (context size, prediction count, GPU layers)
- Sampling parameters (temperature, top-p, top-k, presence penalty)
- API endpoint configuration

## Notes

- The first run will download the model from Hugging Face (requires internet connection)
- Subsequent runs will use the cached model
- The container will automatically shut down when you exit the terminal

## Troubleshooting

If you encounter issues:
1. Ensure Docker is installed and running
2. Check that you have sufficient disk space for the model (~18GB)
3. Verify network connectivity during initial model download
4. Make sure you're running with appropriate permissions
5. If you get connection error 500, verify if llama-server is still running by using `./view_llama_server.sh` script. Sometimes it crashes upon tool calling.
6. For C-to-SysML web UI: if generation hangs or times out, run `./view_llama_server.sh` from another terminal to inspect the llama-server and litellm panes. Ensure the model has finished loading (look for "slot" or "loaded" in llama-server output) before generating.
