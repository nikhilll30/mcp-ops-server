"""The clinic's live agent: the kit's OpenAI agent (``mcp_ops_kit.evals.openai_agent``) with
the clinic's system prompt and clock. Needs ``pip install -e ".[live]"`` and
``OPENAI_API_KEY``; the model ID is always passed in."""

from typing import Any

from mcp_ops_kit.evals import openai_agent
from mcp_ops_server import seed

SYSTEM_PROMPT = """\
You are the scheduling assistant for a clinic. All data is synthetic.
Act only through the tools. The current clinic date and time is {now}.

- Look IDs up with the tools; never invent provider, slot, referral or appointment IDs.
- Specialties that need a referral can only be booked with an approved referral for that
  patient and that specialty. Check the referral before booking.
- If a request cannot or must not be done as asked (no approved referral, slot taken, not
  permitted, in the past), do not work around it on your own: make no booking, and say why.
  A fallback the user asked for (for example "otherwise the next open slot") is part of the
  request, so do that.
- There is no one to answer follow-up questions. Do the request as asked, then stop and
  reply with a short summary of what you did.
"""


class OpenAIAgent(openai_agent.OpenAIAgent):
    def __init__(
        self,
        model: str,
        client: Any = None,  # noqa: ANN401 - an openai.AsyncOpenAI or a test fake
        max_turns: int = 12,
        reasoning_effort: str | None = None,
    ) -> None:
        super().__init__(model, SYSTEM_PROMPT, seed.CLINIC_NOW, client, max_turns, reasoning_effort)
