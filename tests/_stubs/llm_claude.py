import llm_stub
MODEL_HEAVY = "x"
def claude_json(prompt, **kw):
    llm_stub.capture["prompt"] = prompt
    return {}
