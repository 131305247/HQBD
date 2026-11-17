# SPDX-FileCopyrightText: Custom factory wiring Claude into CVDP.

"""
Custom ModelFactory that routes Claude model requests to the Anthropic Messages API.

Wiring:
  - .env:  CUSTOM_MODEL_FACTORY=claude_factory.py
  - .env:  ANTHROPIC_API_KEY=sk-... or ANTHROPIC_AUTH_TOKEN=sk-...
  - .env:  ANTHROPIC_BASE_URL=https://sub2api.xeda.cc:8888  # optional gateway/proxy
  - .env:  DEFAULT_MODEL=claude-opus-5
  - run:   ./run_benchmark.py -f ...jsonl -l -m claude-opus-5 -p work_claude

The factory registers the prefix "claude", so any -m claude-* name resolves here.
The full model name is passed through to the API.
"""

import logging
import os
import sys
from typing import Optional, Any

# Make the repo root importable so `from src...` works regardless of cwd.
_repo_root = os.path.dirname(os.path.abspath(__file__))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

try:
    import anthropic
except ImportError as exc:
    raise ImportError(
        "Unable to import the Anthropic Python SDK. Install it with: "
        "pip install anthropic"
    ) from exc

from src.config_manager import config
from src.llm_lib.model_factory import ModelFactory
from src.model_helpers import ModelHelpers

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class Claude_Instance:
    """Model instance compatible with CVDP's OpenAI_Instance interface."""

    def __init__(self, context: Any = "You are a helpful assistant.", key=None, model=None):
        if model is None:
            model = config.get("DEFAULT_MODEL", "claude-opus-5")

        self.context = context
        self.model = model
        self.debug = False

        api_key = key or config.get("ANTHROPIC_API_KEY")
        auth_token = config.get("ANTHROPIC_AUTH_TOKEN")
        base_url = config.get("ANTHROPIC_BASE_URL")

        if api_key is None and auth_token is None:
            raise ValueError(
                "Unable to create Claude model: no auth provided "
                "(set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN)"
            )

        client_kwargs = {}
        if api_key is not None:
            client_kwargs["api_key"] = api_key
        if base_url is not None:
            client_kwargs["base_url"] = base_url

        # The Anthropic SDK reads ANTHROPIC_AUTH_TOKEN from the environment.
        self.client = anthropic.Anthropic(**client_kwargs)
        log.info(f"Created Claude model: {self.model}" + (f" @ {base_url}" if base_url else ""))
        self.set_debug(False)

    @property
    def requires_evaluation(self) -> bool:
        return True

    def set_debug(self, debug: bool = True) -> None:
        self.debug = debug
        logging.info(f"Debug mode {'enabled' if debug else 'disabled'}")

    def prompt(
        self,
        prompt,
        schema: str = None,
        prompt_log: str = "",
        files: Optional[list] = None,
        timeout: int = 60,
        category: Optional[int] = None,
    ):
        if self.client is None:
            raise ValueError("Unable to detect Claude client")

        helper = ModelHelpers()
        system_prompt = helper.create_system_prompt(self.context, schema, category)

        if timeout == 60:
            timeout = config.get("MODEL_TIMEOUT", 60)

        max_tokens = config.get("CLAUDE_MAX_TOKENS", 16000)
        expected_single_file = files and len(files) == 1 and schema is None

        if self.debug:
            logging.debug(f"Requesting prompt using the model: {self.model}")
            logging.debug(f"System prompt: {system_prompt}")
            logging.debug(f"User prompt: {prompt}")
            logging.debug(f"Request parameters: model={self.model}, timeout={timeout}, max_tokens={max_tokens}")

        if prompt_log:
            try:
                os.makedirs(os.path.dirname(prompt_log), exist_ok=True)
                temp_log = f"{prompt_log}.tmp"
                with open(temp_log, "w+") as f:
                    f.write(system_prompt + "\n\n----------------------------------------\n" + prompt)
                os.replace(temp_log, prompt_log)
            except Exception as e:
                logging.error(f"Failed to write prompt log to {prompt_log}: {str(e)}")
                raise

        try:
            with self.client.with_options(timeout=timeout).messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for _ in stream:
                    pass
                response = stream.get_final_message()

            content = "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            ).strip()

            if expected_single_file:
                pass
            elif schema is not None and content.startswith("{") and content.endswith("}"):
                content = helper.fix_json_formatting(content)

            return helper.parse_model_response(content, files, expected_single_file)

        except Exception as e:
            raise ValueError(f"Unable to get response from Claude model: {str(e)}")


class CustomModelFactory(ModelFactory):
    """Extends the base factory with Claude model support."""

    def __init__(self):
        super().__init__()
        self.model_types["claude"] = self._create_claude_instance
        log.info("CustomModelFactory initialized with Claude support")

    def _create_claude_instance(self, model_name: str, context: Any, key: Optional[str], **kwargs):
        return Claude_Instance(context=context, key=key, model=model_name)


if __name__ == "__main__":
    inst = CustomModelFactory().create_model(
        model_name=config.get("DEFAULT_MODEL", "claude-opus-5"),
        context="You are a helpful assistant.",
    )
    print(f"Created: {inst.model}")
    raw = inst.client.messages.create(
        model=inst.model,
        max_tokens=16,
        messages=[{"role": "user", "content": "Reply with exactly one word: PONG"}],
    )
    print("Response:", "".join(block.text for block in raw.content if block.type == "text"))
