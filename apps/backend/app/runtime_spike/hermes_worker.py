"""Pinned Hermes embed process. No provider secret or default host tools.

Stdout is a bounded event transport, not an authoritative candidate/QA report.
The supervisor owns tools, input IDs, accounting and final results.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

TOOL_PARAMETERS = {
    "list_files": {},
    "read_file": {"path": {"type": "string", "description": "Project-relative path, e.g. src/cart.js. Never an absolute path or the runtime cwd."}},
    "write_file": {"path": {"type": "string", "description": "Project-relative path, e.g. src/main.jsx. Never an absolute path or the runtime cwd."}, "content": {"type": "string"}},
    "run_phase": {"phase": {"type": "string", "enum": ["install", "test", "build"]}},
    "inspect_diff": {},
    "submit_candidate": {"message": {"type": "string"}},
    "request_input": {"question": {"type": "string"}},
}


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    # HERMES_HOME must already be set before importing upstream modules.
    from run_agent import AIAgent
    from tools.registry import registry

    def emit(kind, payload):
        raw = json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False, default=str)
        if len(raw.encode()) <= 65536:
            print("DEV006_EVENT " + raw, flush=True)

    agent = None
    submitted = [False]

    def handler(name):
        def call(args, **kwargs):
            req = urllib.request.Request(config["relay_url"] + "/tools", data=json.dumps({
                "name": name, "arguments": args}).encode(), headers={
                "Authorization": "Bearer " + config["relay_token"], "Content-Type": "application/json"})
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=310) as resp:
                    raw = resp.read(65537)
            except urllib.error.HTTPError as exc:
                raw = exc.read(65537)
                if exc.code == 409:
                    agent.interrupt(tool_reason="supervisor_admission_denied")
            if len(raw) > 65536:
                raise RuntimeError("tool response too large")
            result = json.loads(raw)
            if (name == "request_input" and not result.get("error")) or result.get("status") == "waiting_input":
                agent.interrupt(tool_reason="supervisor_waiting_input")
            if result.get("submitted") is True:
                if name == config.get("completion_tool"):
                    submitted[0] = True
                agent.interrupt(tool_reason="supervisor_candidate_submitted")
            return raw.decode()
        return call

    parameters = config.get("tool_parameters", TOOL_PARAMETERS)
    prefix, toolset = config.get("tool_prefix", "spike_"), config.get("toolset", "dev006_supervisor")
    selected = {name: params for name, params in parameters.items() if name in config["tool_names"]}
    for name, params in selected.items():
        registry.register(name=prefix + name, toolset=toolset, handler=handler(name),
            schema={"description": "Supervisor-scoped " + name + "; no host shell or filesystem access.",
                    "parameters": {"type": "object", "properties": params, "required": list(params), "additionalProperties": False}})
    agent = AIAgent(model=config["model"], provider="custom", api_mode="chat_completions",
        base_url=config["relay_url"] + "/v1", api_key=config["relay_token"],
        enabled_toolsets=[toolset], max_iterations=config.get("max_iterations", 32), max_tokens=config["output_tokens"],
        quiet_mode=True, save_trajectories=False, session_id=config["session_id"],
        ephemeral_system_prompt=config["system"], cwd=os.getcwd(),
        skip_memory=True, skip_background_review=True, skip_context_files=True,
        load_soul_identity=False, checkpoints_enabled=False,
        reasoning_config={"enabled": False},
        stream_delta_callback=lambda *args: emit("assistant.delta", list(args)),
        event_callback=lambda name, payload: emit("runtime." + name, payload))
    expected = {prefix + name for name in selected}
    actual = {t["function"]["name"] for t in agent.tools}
    if expected != actual:
        raise RuntimeError("Hermes exposed unexpected tool surface: " + repr(actual))
    if agent._memory_enabled or agent._user_profile_enabled or agent._memory_manager or agent.compression_enabled:
        raise RuntimeError("Unexpected memory, profile or compression configuration")
    emit("runtime.ready", {"tools": sorted(actual), "home": os.environ["HERMES_HOME"],
                           "memory_disabled": not agent._memory_enabled,
                           "profile_disabled": not agent._user_profile_enabled,
                           "compression_disabled": not agent.compression_enabled,
                           "background_disabled": agent.skip_background_review})
    result = agent.run_conversation(config["prompt"])
    if config.get("completion_tool") and not submitted[0] and not result.get("interrupted") and not result.get("error"):
        # One correction turn, still subject to the same product reservation/time/token caps.
        required = config["tool_prefix"] + config["completion_tool"]
        result = agent.run_conversation("The supervisor did not receive the required completion tool: " + required +
            ". A prose claim does not finish this job. Inspect the actual files, complete the requested work, and call " +
            required + ". Do not claim file changes that no write tool performed.", conversation_history=result.get("messages"))
    # Persist messages privately for diagnosis, never treat this as a candidate.
    Path("conversation-result.json").write_text(json.dumps(result, default=str, indent=2))
    emit("runtime.result", {k: result.get(k) for k in ("completed", "interrupted", "error", "final_response", "exit_reason")})


if __name__ == "__main__":
    main()
