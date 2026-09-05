# Deploying SEEFIX to Hugging Face Spaces

This guide deploys the hosted SEEFIX image-analysis POC with Gradio, Transformers,
Qwen3-VL-2B-Instruct, and ZeroGPU. Local Windows development continues to use
FastAPI and Ollama and is not affected by the hosted deployment.

## 1. Understand the two runtimes

| Runtime | Entry point | Model | Endpoint style |
| --- | --- | --- | --- |
| Local Windows | `app/main.py` | Ollama GGUF | FastAPI `/api/analyze` |
| Hugging Face | `space_app.py` | Transformers BF16 | Gradio `/analyze` queue API |

ZeroGPU currently supports the Gradio SDK. Do not configure this repository as a
Docker or FastAPI Space when the goal is free ZeroGPU inference.

## 2. Confirm repository hygiene

The following must be committed:

- `README.md`
- `space_app.py`
- `requirements.txt`
- `app/` and all Python modules below it
- `tests/`
- `LICENSE`

The following must not be committed or uploaded:

- `.env`
- `.venv/`
- `__pycache__/`
- `*.pyc`
- downloaded Ollama GGUF files
- Hugging Face access tokens

The existing `.gitignore` already excludes the local environment and caches.
The public model does not require a token inside `space_app.py`.

## 3. Choose where to host the Space

For a no-cost proof of concept, create the Space under an eligible personal
Hugging Face account and select ZeroGPU. Free personal accounts in good standing
can host up to two ZeroGPU Spaces. Hosting ZeroGPU under an organization requires
a Team or Enterprise organization plan.

ZeroGPU is quota- and queue-based. Treat it as a hosted POC or staging service,
not as a guaranteed always-on production backend for many simultaneous users.

## 4. Create the Space

1. Sign in at `https://huggingface.co`.
2. Open `https://huggingface.co/new-space`.
3. Set the Space name, for example `seefix-agents`.
4. Choose **Gradio** as the SDK.
5. Choose **Public** visibility for the simplest test deployment.
6. Select **ZeroGPU** hardware during creation. If it is not available, check
   account eligibility or use paid GPU hardware.
7. Create the Space and copy its ID, for example
   `<HF_USERNAME>/seefix-agents`.

The Space must be on ZeroGPU before `space_app.py` starts because the model is
placed on CUDA during module startup, as required by ZeroGPU.

## 5. First deployment from Windows

Open Command Prompt in the GitHub repository folder:

```bat
cd C:\path\to\seefix-agents
```

Install or update the Hugging Face CLI in the existing virtual environment:

```bat
.venv\Scripts\python.exe -m pip install --upgrade huggingface_hub
```

Authenticate with a fine-grained Hugging Face token that has write access to
the new Space:

```bat
.venv\Scripts\hf.exe auth login
```

Do not paste the token into `.env`, source code, README, or Git remote URLs.

Verify the authenticated account:

```bat
.venv\Scripts\hf.exe auth whoami
```

Upload the repository. Replace `<HF_USERNAME>` with the actual namespace:

```bat
.venv\Scripts\hf.exe upload <HF_USERNAME>/seefix-agents . . --repo-type=space --exclude=".git/*" --exclude=".github/*" --exclude=".venv/*" --exclude=".env" --exclude="__pycache__/*" --exclude="*/__pycache__/*" --exclude="*.pyc" --commit-message="Initial SEEFIX deployment"
```

The upload command sends source files only. Qwen model weights are downloaded
from `Qwen/Qwen3-VL-2B-Instruct` during the Space build and are preloaded through
the `README.md` metadata.

## 6. Space variables

The code has safe defaults, so variables are optional. If they are added under
**Space Settings > Variables**, use:

```text
SEEFIX_RUNTIME=huggingface
SEEFIX_HUGGINGFACE_MODEL=Qwen/Qwen3-VL-2B-Instruct
SEEFIX_HUGGINGFACE_MAX_OUTPUT_TOKENS=512
SEEFIX_MAX_UPLOAD_MB=10
SEEFIX_MAX_IMAGE_DIMENSION=512
```

These are non-sensitive variables. Do not configure Ollama variables in the
Space. Ollama is used only by the local runtime.

## 7. Monitor the first build

The first build installs Python dependencies and prepares the model cache. Open
the Space, select **Building**, and inspect **Build logs**. Then inspect
**Container logs** when the application starts.

Expected startup behavior:

1. `requirements.txt` is installed.
2. `space_app.py` imports successfully.
3. Qwen3-VL-2B-Instruct loads in BF16 with SDPA.
4. Gradio starts and exposes the `analyze` endpoint.

The first build can take several minutes because model files and Python wheels
must be prepared. A slow build is different from slow per-image inference.

## 8. Verify the user interface

Open the **App** tab and test these images:

1. Clear visible facility damage: expected `Facility Issue` and `Assessed`.
2. Undamaged hallway or room: expected `No Visible Maintenance Issue`.
3. Dog, person-only, food, or unrelated scenery: expected `Out of Scope`.
4. Dark or severely blurred image: expected `Insufficient Image`.

All decisions except `Facility Issue` must return `assessment: null` and
`priority: null`.

## 9. Verify the hosted API

Open **Use via API** in the Space footer. Confirm that `/analyze` is listed. The
exact client examples shown there are authoritative for the deployed Gradio
version.

Python client example:

```python
from gradio_client import Client, handle_file

client = Client("<HF_USERNAME>/seefix-agents")
job = client.submit(
    image=handle_file(r"C:\Users\Erwin\Downloads\facility-damage.jpg"),
    api_name="/analyze",
)

print(job.status())
result = job.result()
print(result)
```

Install the client with:

```bat
python -m pip install --upgrade gradio_client
```

For a private Space, pass a read token to `Client`. Do not expose that token in
mobile or browser applications; call the Space from the trusted Node.js backend.

## 10. Integration with the future SEEFIX backend

The intended asynchronous flow is:

1. Node.js saves the report and image reference in PostgreSQL.
2. Node.js sets analysis status to `PENDING`.
3. A trusted backend worker submits the image to the Space queue.
4. The worker stores the returned job/event identifier.
5. The user can leave or close the application.
6. The worker waits or polls for completion independently.
7. The worker validates the returned JSON and updates the report to `COMPLETED`
   or `FAILED`.
8. The application reads the updated result from PostgreSQL and notifies the
   user.

Do not call a private Space directly from Ionic/Angular because that would expose
the Hugging Face token. The database remains the source of truth; the Space is
only the inference worker.

## 11. Optional deployment from GitHub Actions

The repository includes `.github/workflows/deploy-huggingface.yml`. It runs only
when manually started.

In GitHub repository settings, configure:

- Repository variable `HF_SPACE_ID` = `<HF_USERNAME>/seefix-agents`
- Repository secret `HF_TOKEN` = a fine-grained Hugging Face token with write
  access to that Space

Then open **Actions > Deploy Hugging Face Space > Run workflow**. The workflow
uploads the repository without `.env`, `.venv`, cache files, or the workflow
itself.

## 12. Troubleshooting

### Space starts on CPU or reports that CUDA is unavailable

Confirm that the Space hardware is ZeroGPU. Select ZeroGPU first, then use
**Factory reboot** so the application is rebuilt in the correct environment.

### `No module named spaces`

Confirm the Space SDK is Gradio and the selected hardware is ZeroGPU. The
`spaces` integration belongs to the ZeroGPU Gradio runtime.

### Model class cannot be imported

Confirm that `requirements.txt` installs `transformers>=4.57,<5`. Older
Transformers versions do not contain Qwen3-VL support.

### CUDA out of memory

Confirm the configured model is the 2B checkpoint, image resizing remains 512,
and only one request is allowed concurrently. Do not switch this Space to the 8B
model without measuring memory and quota usage.

### Request waits in a queue

This is expected on ZeroGPU. Queue delay depends on account tier, remaining
quota, current demand, and the requested GPU duration. It is not the same as
model inference time.

### Daily GPU quota is exhausted

Wait for quota reset, authenticate requests with an eligible account, add paid
credits, or move to dedicated paid GPU hardware. Routing through multiple free
accounts or tokens is not a reliable production design.

### Hosted response contains invalid JSON

Review the Space container logs. The application already applies local JSON
cleanup and Pydantic validation. Save the failure as `FAILED` in the backend and
allow a controlled retry rather than creating an unvalidated maintenance result.

## 13. Deployment acceptance checklist

- Space uses Gradio SDK.
- Hardware is ZeroGPU or an explicitly selected paid GPU.
- Build and container logs contain no errors.
- The model is `Qwen/Qwen3-VL-2B-Instruct`.
- All four scope-decision tests behave correctly.
- `/analyze` appears under **Use via API**.
- A client can submit asynchronously and receive validated JSON.
- `.env`, `.venv`, tokens, and local model files are absent from the Space repo.
- The application UI states that results require human review.
