import pytest
from pydantic import SecretStr

from assistant.config import Settings
from assistant.container import create_embedder, create_redis, create_servicenow
from assistant.errors import ConfigurationError
from assistant.rag import FastEmbedEmbedder
from assistant.servicenow_client import ServiceNowClient


async def test_factories_build_clients_without_connecting() -> None:
    settings = Settings(servicenow_client_secret=SecretStr("secreto"))

    redis = create_redis(settings)
    servicenow = create_servicenow(settings)

    assert isinstance(create_embedder(settings), FastEmbedEmbedder)
    assert isinstance(servicenow, ServiceNowClient)
    await servicenow.aclose()
    await redis.aclose()


def test_servicenow_secret_is_required() -> None:
    with pytest.raises(ConfigurationError, match="SERVICENOW_CLIENT_SECRET"):
        create_servicenow(Settings())
