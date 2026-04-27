import pytest

from app.services import trader_service as trader_service_module
from app.services.trader_service import TraderService, _validate_default_traders


class TestGetTraderLlmFailFast:
    @pytest.mark.asyncio
    async def test_missing_provider_raises(self):
        svc = TraderService()
        trader = {"id": "t1", "name": "X", "llm_model": "anthropic/claude", "config": {}}
        with pytest.raises(ValueError) as exc:
            await svc.get_trader_llm(trader)
        assert "llm_provider" in str(exc.value).lower() or "provider" in str(exc.value).lower()
        assert ".env" in str(exc.value)

    @pytest.mark.asyncio
    async def test_missing_model_raises(self):
        svc = TraderService()
        trader = {"id": "t1", "name": "X", "llm_provider": "openrouter", "config": {}}
        with pytest.raises(ValueError) as exc:
            await svc.get_trader_llm(trader)
        assert "model" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_empty_string_raises(self):
        svc = TraderService()
        trader = {"id": "t1", "name": "X", "llm_provider": "", "llm_model": "", "config": {}}
        with pytest.raises(ValueError):
            await svc.get_trader_llm(trader)

    @pytest.mark.asyncio
    async def test_whitespace_only_raises(self):
        svc = TraderService()
        trader = {"id": "t1", "name": "X", "llm_provider": "  ", "llm_model": "\t\n", "config": {}}
        with pytest.raises(ValueError):
            await svc.get_trader_llm(trader)


class TestValidateDefaultTraders:
    def test_default_traders_pass_validation(self):
        _validate_default_traders()

    def test_validation_catches_missing_field(self, monkeypatch):
        bad = [{"name": "Bad", "llm_provider": "openrouter"}]
        monkeypatch.setattr(trader_service_module, "DEFAULT_TRADERS", bad)
        with pytest.raises(ValueError) as exc:
            trader_service_module._validate_default_traders()
        assert "llm_model" in str(exc.value)

    def test_validation_catches_empty_string(self, monkeypatch):
        bad = [{"name": "Bad", "llm_provider": "openrouter", "llm_model": ""}]
        monkeypatch.setattr(trader_service_module, "DEFAULT_TRADERS", bad)
        with pytest.raises(ValueError):
            trader_service_module._validate_default_traders()


class TestTraderApiValidation:
    def test_create_rejects_blank_provider(self):
        from app.api.routes.traders import TraderCreate
        with pytest.raises(Exception):
            TraderCreate(name="X", llm_provider="", llm_model="claude")

    def test_create_rejects_whitespace_model(self):
        from app.api.routes.traders import TraderCreate
        with pytest.raises(Exception):
            TraderCreate(name="X", llm_provider="openrouter", llm_model="   ")

    def test_create_requires_all_fields(self):
        from app.api.routes.traders import TraderCreate
        with pytest.raises(Exception):
            TraderCreate(name="X")

    def test_create_strips_surrounding_whitespace(self):
        from app.api.routes.traders import TraderCreate
        t = TraderCreate(
            name="  Kai  ",
            llm_provider=" openrouter ",
            llm_model=" anthropic/claude ",
        )
        assert t.name == "Kai"
        assert t.llm_provider == "openrouter"
        assert t.llm_model == "anthropic/claude"

    def test_update_allows_none_but_rejects_blank(self):
        from app.api.routes.traders import TraderUpdate
        ok = TraderUpdate(llm_model=None)
        assert ok.llm_model is None
        with pytest.raises(Exception):
            TraderUpdate(llm_model="")
