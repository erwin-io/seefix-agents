from pathlib import Path

from gradio_client import Client, handle_file


IMAGE_PATH = Path(
    r"C:\Users\Erwin\Downloads\images (3).jpeg"
)

if not IMAGE_PATH.is_file():
    raise FileNotFoundError(
        f"Test image was not found: {IMAGE_PATH}"
    )

client = Client("erwinramirez220/seefix-agents")

print(f"Uploading: {IMAGE_PATH}")

result = client.predict(
    image=handle_file(str(IMAGE_PATH)),
    api_name="/analyze",
)

print("\nSEEFIX assessment:")
print(result)