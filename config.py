"""
Application settings loaded from environment variables (and optionally a .env file).

All fields can be overridden via environment variables with the same name (case-insensitive).
Create a .env file in the project root for local development.

Required for production:
  GOOGLE_CLOUD_PROJECT   — GCP project ID used for Vertex AI and GCS access.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralised configuration for the assessment-ai-service.

    Reads values from environment variables first, then from a .env file
    in the working directory.  All fields have safe defaults so the service
    starts without a .env file, but GOOGLE_CLOUD_PROJECT must be set for
    any Vertex AI or GCS call to succeed.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Google Cloud Platform ─────────────────────────────────────────────────
    # Project and region used for all Vertex AI calls (Gemini + embeddings).
    google_cloud_project: str = ""
    google_cloud_location_gemini: str = "asia-southeast1"

    # ── LLM / Image models ────────────────────────────────────────────────────
    # Primary generation model — Gemini 2.5 Flash via Vertex AI.
    gemini_model: str = "gemini-2.5-flash"
    # image_model_api selects the image-generation API:
    #   "gemini"  → use GenerativeModel.generate_content with IMAGE modality
    #   "imagen"  → use client.models.generate_images (Imagen 4 family)
    image_model_api: str = "gemini"
    image_model: str = "gemini-3.1-flash-image-preview"
    # "global" endpoint supports all regions; avoids region-specific quota limits.
    image_model_location: str = "global"
    # Multilingual model — handles Hindi/English code-switching in CBSE content.
    embedding_model: str = "text-multilingual-embedding-002"

    # ── Qdrant vector database ────────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "pdf_chunks"
    # Leave empty for unauthenticated access (local dev / in-cluster deployment).
    qdrant_api_key: str = ""

    # ── Chunking strategy ─────────────────────────────────────────────────────
    # ~600 tokens; text-multilingual-embedding-002 supports up to 2048 tokens.
    chunk_size_chars: int = 2400
    # Seed next chunk with the last N chars to avoid context loss at boundaries.
    chunk_overlap_chars: int = 200

    # ── Retrieval ─────────────────────────────────────────────────────────────
    # Final number of chunks returned after RRF fusion of dense + keyword hits.
    retrieve_top_k: int = 5


settings = Settings()
