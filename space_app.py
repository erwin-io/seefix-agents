from __future__ import annotations

from io import BytesIO

import gradio as gr
import spaces
from PIL import Image

from app.agent import FacilityInspectionAgent
from app.config import settings
from app.providers.base import ProviderError
from app.providers.huggingface import HuggingFaceProvider


provider = HuggingFaceProvider(
    model=settings.huggingface_model,
    max_output_tokens=settings.huggingface_max_output_tokens,
    device="cuda",
)

agent = FacilityInspectionAgent(
    settings,
    provider=provider,
)


def health_check(message: str = "ping") -> dict[str, str]:
    """
    Lightweight endpoint for health checks and automatic probes.

    This endpoint does not run the vision model or request ZeroGPU.
    """
    return {
        "status": "ok",
        "service": "seefix-facility-inspection-agent",
        "message": message,
    }


def image_to_bytes(image: Image.Image) -> bytes:
    output = BytesIO()

    image.convert("RGB").save(
        output,
        format="JPEG",
        quality=85,
        optimize=True,
    )

    return output.getvalue()


@spaces.GPU(duration=45)
def analyze_report(
    image: Image.Image | None,
) -> dict:
    if image is None:
        raise gr.Error(
            "Select a JPEG, PNG, or WebP image first."
        )

    try:
        result = agent.analyze(
            image_to_bytes(image)
        )

        return result.model_dump(mode="json")

    except (ProviderError, ValueError) as exc:
        raise gr.Error(str(exc)) from exc


with gr.Blocks(
    title="SEEFIX Facility Inspection Agent"
) as demo:
    # Public health endpoint for uptime checks and probes.
    gr.api(
        health_check,
        api_name="health",
    )

    gr.Markdown(
        """
        # SEEFIX Facility Inspection Agent

        Upload one university facility image for a preliminary
        AI-assisted scope review and maintenance assessment.

        Out-of-scope, undamaged, and unusable images return
        **No Assessment**.

        Results require human/PPO review and are not an
        engineering certification.
        """
    )

    with gr.Row():
        with gr.Column():
            image_input = gr.Image(
                type="pil",
                label="Facility image",
                sources=["upload", "webcam"],
            )

            analyze_button = gr.Button(
                "Analyze report",
                variant="primary",
            )

        with gr.Column():
            result_output = gr.JSON(
                label="Structured assessment"
            )

    analyze_button.click(
        fn=analyze_report,
        inputs=[image_input],
        outputs=result_output,
        api_name="analyze",

        # The endpoint remains callable by Gradio clients but is
        # excluded from automatic tool discovery.
        api_visibility="undocumented",
    )


if __name__ == "__main__":
    demo.queue(
        default_concurrency_limit=1,
        max_size=20,
    ).launch()