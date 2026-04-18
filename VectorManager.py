import config as cfg
from openai import AzureOpenAI
import openai


# -----------------------------------------------------------------------------
# DEAD CODE NOTICE
# This class is not imported or instantiated anywhere in the codebase as of
# this writing. The active embedding pipeline lives in LLMDocumentVectorEngine,
# which reads cfg.AZURE_OPENAI_DEPLOYMENT_NAME_EMBEDDING from config.py.
# The 'text-embedding-3-large' default below is NOT what the app currently
# uses. If you ever resurrect this class, change the default to read from
# cfg.AZURE_OPENAI_DEPLOYMENT_NAME_EMBEDDING so it stays consistent with the
# rest of the system.
# -----------------------------------------------------------------------------
class VectorManager:
    """Manages vector embeddings independent of storage backend.

    NOTE: currently unused (dead code). See notice above.
    """
    def __init__(self, embedding_model="azure:text-embedding-3-large"):
        self.embedding_model = embedding_model
        self.model_provider, self.model_name = embedding_model.split(':')
        self._initialize_model()
    
    def _initialize_model(self):
        if self.model_provider == "azure":
            self.client = AzureOpenAI(
                api_version="2024-12-01-preview",
                base_url=cfg.AZURE_OPENAI_BASE_URL_ALTERNATE,
                api_key=cfg.AZURE_OPENAI_API_KEY_ALTERNATE
            )
        elif self.model_provider == "openai":
            openai.api_type = "azure"
            openai.api_key = cfg.AZURE_OPENAI_API_KEY_ALTERNATE
            openai.api_base = cfg.AZURE_OPENAI_BASE_URL_ALTERNATE
            openai.base_url = cfg.AZURE_OPENAI_BASE_URL_ALTERNATE
            openai.api_version = cfg.AZURE_OPENAI_API_VERSION_ALTERNATE 
            deployment_id = self.model_name
        elif self.model_provider == "huggingface":
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(self.model_name)
    
    def generate_embedding(self, text):
        if self.model_provider in ["azure"]:
            print('Generating azure embedding:', self.model_name)
            response = self.client.embeddings.create(
                input=text,
                model=self.model_name  # must be deployment name for Azure
            )
            return response.data[0].embedding
        elif self.model_provider in ["openai"]:
            print('Generating openai embedding:', self.model_name)
            response = openai.embeddings.create(
                input=text,
                model=self.model_name  # must be deployment name for Azure
            )
            return response.data[0].embedding
        elif self.model_provider == "huggingface":
            return self.model.encode(text).tolist()
        # Add other providers as needed
    
    def batch_generate_embeddings(self, texts):
        """Generate embeddings for multiple texts"""
        embeddings = []
        for text in texts:
            embeddings.append(self.generate_embedding(text))
        return embeddings
