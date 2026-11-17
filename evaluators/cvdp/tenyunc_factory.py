# SPDX-FileCopyrightText: Custom factory wiring tenyunc GLM (OpenAI-compatible endpoint) into CVDP.

"""
Custom ModelFactory that routes GLM requests to the tenyunc OpenAI-compatible
endpoint (https://preview.llm.tenyunc.com/v1).

Wiring:
  - .env:  CUSTOM_MODEL_FACTORY=<repo>/tenyunc_factory.py
  - .env:  TENYUNC_API_KEY=sk-...  (fallback OPENAI_USER_KEY)
  - .env:  TENYUNC_BASE_URL=https://preview.llm.tenyunc.com/v1
  - .env:  DEFAULT_MODEL=GLM-5.2
  - run:   ./run_benchmark.py -f ...jsonl -l -m glm-5.2 -p work_glm

The factory registers the prefix "glm" so any -m glm-* / -m GLM-* name resolves
here. The actual model string sent to the API is the full -m value (e.g.
"GLM-5.2"), so tenyunc sees the real model id.
"""

import logging
import os
import sys
from typing import Optional, Any

# Make the repo root importable so `from src.llm_lib...` works regardless of cwd.
_repo_root = os.path.dirname(os.path.abspath(__file__))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import openai

from src.llm_lib.model_factory import ModelFactory
from src.config_manager import config
from src.llm_lib.openai_llm import OpenAI_Instance

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class TenyuncGLM_Instance(OpenAI_Instance):
    """An OpenAI-compatible instance pointed at the tenyunc endpoint.

    Inherits prompt()/parse logic from OpenAI_Instance; only swaps the client
    for one constructed with base_url.
    """

    def __init__(self, context: str = "You are a helpful assistant.", key=None, model=None):
        if model is None:
            model = config.get("DEFAULT_MODEL", "GLM-5.2")

        self.context = context
        self.model = model
        self.debug = False

        # Key precedence: explicit arg > TENYUNC_API_KEY > OPENAI_USER_KEY
        api_key = key or config.get("TENYUNC_API_KEY") or config.get("OPENAI_USER_KEY")
        if api_key is None:
            raise ValueError(
                "Unable to create tenyunc GLM model: no API key "
                "(set TENYUNC_API_KEY or OPENAI_USER_KEY)"
            )

        base_url = config.get("TENYUNC_BASE_URL", "https://preview.llm.tenyunc.com/v1")

        self.chat = openai.OpenAI(api_key=api_key, base_url=base_url)
        log.info(
            f"Created tenyunc GLM model: {self.model} @ {base_url}"
        )
        self.set_debug(False)


class CustomModelFactory(ModelFactory):
    """Extends the base factory with the tenyunc GLM endpoint."""

    def __init__(self):
        super().__init__()
        # Prefix "glm" matches any -m glm-* / -m GLM-* model name.
        self.model_types["glm"] = self._create_glm_instance
        # Also register the exact default model id for direct selection.
        self.model_types["GLM-5.2"] = self._create_glm_instance
        log.info("CustomModelFactory initialized with tenyunc GLM support")

    def _create_glm_instance(self, model_name: str, context: Any, key: Optional[str], **kwargs):
        """Create a tenyunc GLM instance. The full model_name is passed through
        as the model id so tenyunc receives the real name (e.g. GLM-5.2)."""
        return TenyuncGLM_Instance(context=context, key=key, model=model_name)


if __name__ == "__main__":
    # Quick standalone smoke test. Bypass the CVDP helper stack and hit the
    # OpenAI-compatible endpoint directly to confirm connectivity + model id.
    inst = CustomModelFactory().create_model(model_name="GLM-5.2", context="You are a helpful assistant.")
    print(f"Created: {inst.model} @ {inst.chat.base_url}")
    raw = inst.chat.chat.completions.create(
        model=inst.model,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Reply with exactly one word: PONG"},
        ],
        timeout=60,
    )
    print("Response:", raw.choices[0].message.content)
