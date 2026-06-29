class LLMAdapterError(RuntimeError):
    pass


class LLMTimeoutError(LLMAdapterError):
    pass


class LLMProtocolError(LLMAdapterError):
    pass
