import importlib.util
import sys
import types


def _has_module(name: str) -> bool:
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except ValueError:
        return True


def _install_fastapi_stub() -> None:
    if _has_module("fastapi"):
        return

    fastapi_module = types.ModuleType("fastapi")
    responses_module = types.ModuleType("fastapi.responses")

    class FastAPI:
        def __init__(self, *args, **kwargs):
            self.state = types.SimpleNamespace()

        def get(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        def post(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        def put(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail=None):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    class StreamingResponse:
        def __init__(self, content, *args, **kwargs):
            self.body_iterator = content
            self.args = (content, *args)
            self.kwargs = kwargs

    class HTMLResponse(str):
        pass

    fastapi_module.FastAPI = FastAPI
    fastapi_module.HTTPException = HTTPException
    responses_module.StreamingResponse = StreamingResponse
    responses_module.HTMLResponse = HTMLResponse

    sys.modules["fastapi"] = fastapi_module
    sys.modules["fastapi.responses"] = responses_module


def _install_pydantic_stub() -> None:
    if _has_module("pydantic"):
        return

    pydantic_module = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    pydantic_module.BaseModel = BaseModel
    sys.modules["pydantic"] = pydantic_module


def _install_agentscope_stub() -> None:
    if _has_module("agentscope"):
        return

    agentscope_module = types.ModuleType("agentscope")
    agent_module = types.ModuleType("agentscope.agent")
    formatter_module = types.ModuleType("agentscope.formatter")
    memory_module = types.ModuleType("agentscope.memory")
    message_module = types.ModuleType("agentscope.message")
    model_module = types.ModuleType("agentscope.model")

    class ReActAgent:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def set_console_output_enabled(self, enabled=False):
            self.console_output_enabled = enabled

        async def __call__(self, msg):
            return msg

    class OpenAIChatFormatter:
        pass

    class InMemoryMemory:
        pass

    class Msg:
        def __init__(self, name: str, role: str, content):
            self.name = name
            self.role = role
            self.content = content

        def get_text_content(self):
            return str(self.content)

        def __str__(self):
            return str(self.content)

    class OpenAIChatModel:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class OpenAIResponseModel(OpenAIChatModel):
        pass

    agent_module.ReActAgent = ReActAgent
    formatter_module.OpenAIChatFormatter = OpenAIChatFormatter
    memory_module.InMemoryMemory = InMemoryMemory
    message_module.Msg = Msg
    model_module.OpenAIChatModel = OpenAIChatModel
    model_module.OpenAIResponseModel = OpenAIResponseModel

    sys.modules["agentscope"] = agentscope_module
    sys.modules["agentscope.agent"] = agent_module
    sys.modules["agentscope.formatter"] = formatter_module
    sys.modules["agentscope.memory"] = memory_module
    sys.modules["agentscope.message"] = message_module
    sys.modules["agentscope.model"] = model_module


def _install_rank_bm25_stub() -> None:
    if _has_module("rank_bm25"):
        return

    rank_bm25_module = types.ModuleType("rank_bm25")

    class BM25Okapi:
        def __init__(self, corpus):
            self.corpus = corpus

        def get_scores(self, query_tokens):
            return [0.0 for _ in self.corpus]

    rank_bm25_module.BM25Okapi = BM25Okapi
    sys.modules["rank_bm25"] = rank_bm25_module


def _install_qdrant_stub() -> None:
    if _has_module("qdrant_client"):
        return

    qdrant_module = types.ModuleType("qdrant_client")
    qdrant_models_module = types.ModuleType("qdrant_client.models")

    class QdrantClient:
        def __init__(self, *args, **kwargs):
            self.collections = set()

        def collection_exists(self, name):
            return name in self.collections

        def delete_collection(self, name):
            self.collections.discard(name)

        def create_collection(self, collection_name, vectors_config):
            self.collections.add(collection_name)

        def upsert(self, collection_name, points):
            return None

        def query_points(self, collection_name, query, limit, with_payload):
            return types.SimpleNamespace(points=[])

    class Distance:
        COSINE = "cosine"

    class VectorParams:
        def __init__(self, size, distance):
            self.size = size
            self.distance = distance

    class PointStruct:
        def __init__(self, id, vector, payload):
            self.id = id
            self.vector = vector
            self.payload = payload

    qdrant_module.QdrantClient = QdrantClient
    qdrant_models_module.Distance = Distance
    qdrant_models_module.VectorParams = VectorParams
    qdrant_models_module.PointStruct = PointStruct

    sys.modules["qdrant_client"] = qdrant_module
    sys.modules["qdrant_client.models"] = qdrant_models_module


def _install_requests_stub() -> None:
    if _has_module("requests"):
        return

    requests_module = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    def post(*args, **kwargs):
        raise RequestException("requests stub should not be used in unit tests")

    requests_module.RequestException = RequestException
    requests_module.post = post
    sys.modules["requests"] = requests_module


def install_test_stubs() -> None:
    _install_fastapi_stub()
    _install_pydantic_stub()
    _install_agentscope_stub()
    _install_rank_bm25_stub()
    _install_qdrant_stub()
    _install_requests_stub()
