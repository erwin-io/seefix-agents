---
title: SEEFIX Facility Inspection Agent
emoji: 🏫
colorFrom: blue
colorTo: indigo
sdk: gradio
app_file: space_app.py
python_version: 3.12
startup_duration_timeout: 1h
models:
  - Qwen/Qwen3-VL-2B-Instruct
preload_from_hub:
  - Qwen/Qwen3-VL-2B-Instruct
---

# SEEFIX Facility Inspection Agent

SEEFIX accepts a university facility image, validates whether it contains a
visible facility-maintenance issue, performs a preliminary vision-language
assessment when appropriate, validates the structured result, and calculates an
explainable priority score.

The same source code supports two execution profiles:

| Profile | Interface | Model runtime | Internet during inference |
| --- | --- | --- | --- |
| Local development | FastAPI | Ollama + Qwen3-VL GGUF | Not required |
| Hugging Face production demo | Gradio | Transformers + Qwen3-VL | Hosted online |

The system provides preliminary decision support. It is not a structural,
electrical, plumbing, fire-safety, or engineering certification. Critical and
uncertain findings require PPO or qualified-person review.

## Local offline development

### Requirements

- Windows 11
- Python 3.10, 3.11, or 3.12
- Ollama
- Qwen3-VL-2B Instruct Q4 model

Install the local model once:

```bat
ollama pull qwen3-vl:2b-instruct-q4_K_M
```

After the model is downloaded, the agent can analyze images without an internet
connection.

### Start on Windows

Double-click:

```text
run_windows.bat
```

The launcher creates `.venv`, installs `requirements-local.txt`, verifies
multipart support, finds Ollama, and starts FastAPI at:

```text
http://127.0.0.1:8000
```

Interactive API documentation:

```text
http://127.0.0.1:8000/docs
```

### Local configuration

Copy `.env.example` to `.env` if it does not exist:

```env
SEEFIX_RUNTIME=local
SEEFIX_PROVIDER=ollama
SEEFIX_OLLAMA_BASE_URL=http://127.0.0.1:11434
SEEFIX_OLLAMA_MODEL=qwen3-vl:2b-instruct-q4_K_M
SEEFIX_REQUEST_TIMEOUT_SECONDS=180
SEEFIX_MAX_UPLOAD_MB=10
SEEFIX_MAX_IMAGE_DIMENSION=512
SEEFIX_MODEL_CONTEXT=1536
SEEFIX_MODEL_MAX_OUTPUT_TOKENS=512
SEEFIX_MODEL_KEEP_ALIVE=15m
SEEFIX_HUGGINGFACE_MODEL=Qwen/Qwen3-VL-2B-Instruct
SEEFIX_HUGGINGFACE_MAX_OUTPUT_TOKENS=512
```

### Test the local REST API

Command Prompt:

```bat
curl.exe --location "http://127.0.0.1:8000/api/analyze" ^
  --form "image=@C:\Users\Erwin\Downloads\facility-damage.jpg"
```

Health check:

```bat
curl.exe http://127.0.0.1:8000/health
```

The response must show `qwen3-vl:2b-instruct-q4_K_M`, a 512-pixel image limit,
a 1536-token context, and a 512-token output limit. Restart the API if older
values appear.

The higher output limit is a ceiling, not a required response length. It prevents
the compact JSON assessment from being cut off. If Ollama still returns malformed
JSON, the provider repairs common comma/bracket errors locally and performs one
text-only repair attempt without reprocessing the image.

The application enforces a minimum value of 512 for this setting. This also
protects existing installations whose older `.env` file still contains `320`.

While analysis is running, check model placement in another terminal:

```bat
ollama ps
```

After every completed request, the API terminal prints a timing line similar to:

```text
[OLLAMA TIMING] total=24.8s load=2.1s prompt/image=8.3s generation=14.4s output_tokens=176 speed=12.2 tokens/s
```

If `ollama ps` reports `100% CPU`, local inference speed is limited by the
computer rather than Postman or FastAPI.

## Hugging Face deployment

This repository is configured as a Gradio Space through the YAML block at the
top of this file.

1. Create a new Hugging Face Space.
2. Select **Gradio** as the SDK.
3. Push all project files to the Space repository.
4. Open the Space **Settings** page.
5. Select **ZeroGPU** hardware.
6. Wait for model preload, dependency installation, and application startup.

Hugging Face starts `space_app.py` and automatically installs
`requirements.txt`. The Space does not install or call Ollama. It loads
`Qwen/Qwen3-VL-2B-Instruct` through Transformers and runs inference inside a
`@spaces.GPU` function.

Optional Space variables:

```env
SEEFIX_RUNTIME=huggingface
SEEFIX_HUGGINGFACE_MODEL=Qwen/Qwen3-VL-2B-Instruct
SEEFIX_HUGGINGFACE_MAX_OUTPUT_TOKENS=512
SEEFIX_MAX_UPLOAD_MB=10
SEEFIX_MAX_IMAGE_DIMENSION=512
```

These are ordinary variables, not secrets. The selected model is public and
does not require an application API token to download.

### Hugging Face API

The Analyze button exposes the Gradio API named `/analyze`. Open the deployed
Space and select **Use via API** to obtain the exact generated Python,
JavaScript, or curl example for the Space URL and installed Gradio version.

## Dependency separation

`requirements-local.txt` contains only the local FastAPI/Ollama dependencies.
`requirements.txt` contains the Hugging Face Gradio/Transformers dependencies.
This prevents the Windows launcher from downloading PyTorch and Transformers
when local inference already runs through Ollama.

## Run tests

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Output

Both profiles return the same validated result structure. Every successful image
request includes:

- `analysis_status`: `Assessed` or `No Assessment`
- `scope_validation.decision`: `Facility Issue`,
  `No Visible Maintenance Issue`, `Out of Scope`, or `Insufficient Image`
- A concise scope reason and detected subject labels

When the decision is `Facility Issue`, the response also includes:

- Facility category
- Summary and observed evidence
- Possible causes separated from visible observations
- Safety indicators and risk flags
- Recommended urgency and reasons
- Preliminary repair-duration range
- Analysis certainty
- Human-review requirement
- Follow-up questions and limitations
- Explainable priority-score breakdown

For all other scope decisions, `assessment` and `priority` are `null`. This is a
successful HTTP `200` response because the agent completed the image review; it
does not create a fabricated low-priority maintenance assessment.

Example out-of-scope response:

```json
{
  "analysis_status": "No Assessment",
  "scope_validation": {
    "decision": "Out of Scope",
    "should_analyze": false,
    "reason": "The image shows a dog in an outdoor area and no visible facility issue.",
    "detected_subjects": ["dog", "grass"]
  },
  "assessment": null,
  "priority": null,
  "provider": "ollama",
  "model_id": "qwen3-vl:2b-instruct-q4_K_M",
  "prompt_version": "facility-inspection-v5-scope-validation",
  "original_width": 1280,
  "original_height": 720,
  "processed_width": 512,
  "processed_height": 288,
  "processing_time_ms": 8421
}
```

## Operational limitations

- A single photograph cannot confirm hidden damage.
- Poor lighting, distance, obstruction, or low resolution reduces reliability.
- The image-only agent does not accept reporter notes or location hints.
- Duplicate and recurrence detection require report history and are outside this
  standalone image-analysis POC.
- ZeroGPU is quota-based and is appropriate for production demonstration and
  evaluation, not a guaranteed always-available institutional backend.
