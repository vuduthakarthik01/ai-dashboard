"""IndustrialMind AI - the assistant that answers from live plant data.

The model is never asked to remember the factory. It is given tools that read
the same tables the dashboard reads, so an answer is always grounded in the
current row rather than in the prompt author's idea of the current row.
"""
import json

from anthropic import AsyncAnthropic

from app.config import settings

MODEL = "claude-sonnet-4-6"

SYSTEM = """You are IndustrialMind AI, the operations assistant inside a factory management
system used by a small Indian manufacturer. You are talking to {name}, whose role is {role}.

Call the tools to read live plant data before answering anything factual; never guess a
number. Answer like an experienced plant manager talking to a busy owner: short
paragraphs, concrete numbers with units, a clear recommended action. Rupees are written
like Rs 12,400. Lead with the answer, then the reasoning. Under 180 words unless asked
for more. If something is dangerous - fire, smoke, a fall, a machine above its vibration
limit - say so first and say what to do in the next ten minutes. If the data does not
support an answer, say what is missing rather than inventing it. Respect the caller's
role: never expose costing or supplier pricing to an operator."""

TOOLS = [
    {"name": "get_plant_snapshot",
     "description": "Complete live state: OEE, every machine's status/health/temperature/"
                    "vibration/output, energy and tariff, quality counts, stock with days "
                    "of cover, open alerts and work orders. Call first for broad questions.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_machine",
     "description": "Live detail and recent sensor history for one machine by code.",
     "input_schema": {"type": "object",
                      "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_alerts",
     "description": "Recent alerts, newest first, optionally filtered by module or severity.",
     "input_schema": {"type": "object",
                      "properties": {"module": {"type": "string"},
                                     "severity": {"type": "string"},
                                     "limit": {"type": "integer"}}}},
    {"name": "raise_work_order",
     "description": "Create a maintenance work order. Requires the wo.create permission.",
     "input_schema": {"type": "object",
                      "properties": {"machine": {"type": "string"},
                                     "reason": {"type": "string"},
                                     "priority": {"type": "string"}},
                      "required": ["machine", "reason"]}},
]


class Assistant:
    def __init__(self, repo, user) -> None:
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.repo = repo          # data access, already scoped to the user's plant
        self.user = user

    async def _run_tool(self, name: str, args: dict):
        if name == "get_plant_snapshot":
            return await self.repo.snapshot()
        if name == "get_machine":
            return await self.repo.machine(args["code"])
        if name == "get_alerts":
            return await self.repo.alerts(module=args.get("module"),
                                          severity=args.get("severity"),
                                          limit=args.get("limit", 15))
        if name == "raise_work_order":
            return await self.repo.raise_work_order(
                machine=args["machine"], reason=args["reason"],
                priority=args.get("priority", "Normal"), by=self.user)
        raise ValueError(f"unknown tool {name}")

    async def ask(self, history: list[dict], question: str):
        """Yields text chunks. Runs tool rounds until the model writes its answer."""
        messages = history + [{"role": "user", "content": question}]
        system = SYSTEM.format(name=self.user.name, role=self.user.role.value)

        for _ in range(6):                       # bounded rounds, never a free loop
            async with self.client.messages.stream(
                model=MODEL, max_tokens=1200, system=system,
                messages=messages, tools=TOOLS,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()

            tool_uses = [b for b in final.content if b.type == "tool_use"]
            if not tool_uses:
                return

            results = []
            for block in tool_uses:
                try:
                    output = await self._run_tool(block.name, block.input or {})
                    content = json.dumps(output, default=str)[:30000]
                except Exception as exc:                       # tool errors go back to the model
                    content = f"Error: {exc}"
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": content})

            messages.append({"role": "assistant", "content": final.content})
            messages.append({"role": "user", "content": results})
