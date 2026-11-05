"""A stand-in for the fastembed package, for tests: same calls, toy vectors."""
from tests.toy_vectors import vector


class TextEmbedding:
    def __init__(self, model_name, cache_dir=None, local_files_only=False):
        if model_name == "missing/model":
            raise ValueError("Could not find model files")
        self.model_name = model_name

    def query_embed(self, texts):
        return (vector(t) for t in texts)

    def passage_embed(self, texts):
        return (vector(t) for t in texts)
