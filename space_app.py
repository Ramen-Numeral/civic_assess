"""Hugging Face Spaces entry point."""

import os

os.environ.setdefault("GRADIO_SSR_MODE", "False")

from app.ui import create_demo, launch_demo

demo, settings = create_demo()

if __name__ == "__main__":
    launch_demo(demo, settings)
