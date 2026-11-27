"""API 管理（AI 提供商加密/主备降级/连通测试 & 新闻 API 信源密钥加密）测试。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import respx
from fastapi.testclient import TestClient

from app.core.secrets import decrypt_secret, encrypt_secret, mask_secret
from app.models.ai import AiRun
from app.models.api_provider import AiProviderConfig
from app.models.audit import ReviewAction
from app.services import ai as ai_module


def test_secret_encrypt_decrypt_and_mask():
    raw = "sk-ws-demo-secret-key-987654321"
    enc = encrypt_secret(raw)
    assert enc is not None
    assert enc.startswith("enc:v1:")
    assert raw not in enc
    assert decrypt_secret(enc) == raw
    assert decrypt_secret("corrupted-token") == ""
    assert mask_secret(raw) == "sk-ws-****4321"
    assert mask_secret("short") == "****"
    assert mask_secret("") is None


def test_ai_provider_crud_role_switch_and_ssrf(session, client: TestClient):
    # 1. 创建主用提供商
    secret_a = "sk-primary-secret-alpha-1111"
    r1 = client.post(
        "/api/ai-providers",
        json={
            "code": "dash_main",
            "name": "阿里百炼主用",
            "vendor": "dashscope",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": secret_a,
            "model_classify": "qwen-flash",
            "model_generate": "qwen-plus",
            "daily_token_budget": 50000,
            "role": "primary",
        },
    )
    assert r1.status_code == 201
    p1 = r1.json()
    assert p1["role"] == "primary"
    assert p1["has_api_key"] is True
    assert p1["api_key_mask"] == "sk-pri****1111"
    assert secret_a not in r1.text  # 响应严禁泄露明文 Key

    # 2. 审计日志也不得泄露明文 Key
    audit_rows = session.query(ReviewAction).filter_by(action="create_ai_provider").all()
    assert audit_rows
    assert secret_a not in str(audit_rows[-1].after_json)

    # 3. SSRF 防护：非 Ollama 拒绝云元数据/内网，Ollama 允许 localhost
    r_ssrf = client.post(
        "/api/ai-providers",
        json={
            "code": "bad_ssrf",
            "name": "非法网关",
            "vendor": "custom",
            "base_url": "http://169.254.169.254/latest/meta-data",
            "api_key": "sk-test",
            "model_classify": "m1",
            "model_generate": "m2",
        },
    )
    assert r_ssrf.status_code == 400

    r_ollama = client.post(
        "/api/ai-providers",
        json={
            "code": "local_ollama",
            "name": "本地 Ollama",
            "vendor": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "model_classify": "qwen2.5:7b",
            "model_generate": "qwen2.5:14b",
            "daily_token_budget": 0,
            "role": "fallback",
        },
    )
    assert r_ollama.status_code == 201
    p2 = r_ollama.json()
    assert p2["role"] == "fallback"
    assert p2["has_api_key"] is False

    # 4. 把 p2 切为主用时，p1 自动降为 standby
    r_sw = client.post(f"/api/ai-providers/{p2['id']}/role", json={"role": "primary"})
    assert r_sw.status_code == 200
    assert r_sw.json()["role"] == "primary"

    r_list = client.get("/api/ai-providers")
    assert r_list.status_code == 200
    roles = {item["code"]: item["role"] for item in r_list.json()["items"]}
    assert roles["local_ollama"] == "primary"
    assert roles["dash_main"] == "standby"

    # 5. 编辑时不传 api_key 保持原密钥不变
    r_patch = client.patch(
        f"/api/ai-providers/{p1['id']}",
        json={"daily_token_budget": 80000},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["api_key_mask"] == "sk-pri****1111"
    assert r_patch.json()["daily_token_budget"] == 80000

    # 6. 删除提供商
    r_del = client.delete(f"/api/ai-providers/{p2['id']}")
    assert r_del.status_code == 204


@respx.mock
def test_ai_provider_connectivity_test(session, client: TestClient):
    r = client.post(
        "/api/ai-providers",
        json={
            "code": "ds_test",
            "name": "DeepSeek 测试",
            "vendor": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-deepseek-demo-9999",
            "model_classify": "deepseek-chat",
            "model_generate": "deepseek-chat",
            "role": "standby",
        },
    )
    pid = r.json()["id"]

    respx.get("https://api.deepseek.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "deepseek-chat"}]})
    )
    t_ok = client.post(f"/api/ai-providers/{pid}/test")
    assert t_ok.status_code == 200
    assert t_ok.json()["ok"] is True
    assert t_ok.json()["method"] == "GET /models"

    respx.get("https://api.deepseek.com/v1/models").mock(
        return_value=httpx.Response(401, json={"error": "invalid_api_key"})
    )
    t_fail = client.post(f"/api/ai-providers/{pid}/test")
    assert t_fail.status_code == 200
    assert t_fail.json()["ok"] is False


def test_ai_primary_to_fallback_failover(session, monkeypatch):
    # 主用提供商 + 备用提供商
    p_primary = AiProviderConfig(
        code="prov_primary",
        name="主用源",
        vendor="dashscope",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_encrypted=encrypt_secret("sk-primary-1234567890"),
        api_key_mask=mask_secret("sk-primary-1234567890"),
        model_classify="model-primary-c",
        model_generate="model-primary-g",
        daily_token_budget=1000,
        role="primary",
        enabled=True,
    )
    p_fallback = AiProviderConfig(
        code="prov_fallback",
        name="备用源",
        vendor="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key_encrypted=encrypt_secret("sk-fallback-1234567890"),
        api_key_mask=mask_secret("sk-fallback-1234567890"),
        model_classify="model-fallback-c",
        model_generate="model-fallback-g",
        daily_token_budget=50000,
        role="fallback",
        enabled=True,
    )
    session.add_all([p_primary, p_fallback])
    session.flush()

    calls: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, api_key: str, base_url: str):
            self.api_key = api_key
            self.base_url = base_url
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            calls.append((self.base_url, kwargs["model"]))
            if "dashscope" in self.base_url:
                raise RuntimeError("429 Too Many Requests")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"is_event": true}'))],
                usage=SimpleNamespace(total_tokens=42),
            )

    monkeypatch.setattr(ai_module, "OpenAI", FakeClient)
    ai_module.reset_client()

    # 场景 1：主用报 429 异常 → 自动降级至备用提供商成功
    res1 = ai_module.classify({"title": "test failover"}, session=session)
    assert res1.status == "success"
    assert res1.data == {"is_event": True}
    assert res1.provider_code == "prov_fallback"
    assert res1.model_used == "model-fallback-c"
    assert any("dashscope" in u for u, _ in calls)
    assert any("deepseek" in u for u, _ in calls)

    # 场景 2：主用已达今日预算上限 → 直接跳过主用走备用提供商
    calls.clear()
    session.add(
        AiRun(
            stage="judge",
            model="model-primary-c",
            prompt_version="v2",
            input_hash="budget-fill-hash",
            tokens=1200,
            latency_ms=10,
            status="success",
        )
    )
    session.flush()
    res2 = ai_module.classify({"title": "test budget failover"}, session=session)
    assert res2.status == "success"
    assert res2.provider_code == "prov_fallback"
    assert all("deepseek" in u for u, _ in calls)


def test_news_api_source_encrypted_key(client: TestClient):
    news_secret = "gnews-secret-token-778899"
    r = client.post(
        "/api/sources",
        json={
            "code": "gnews_api_test",
            "name": "GNews 测试接口",
            "type": "api",
            "level": "B",
            "url": "https://gnews.io/api/v4/search?q=asteroid",
            "poll_interval_min": 60,
            "api_key": news_secret,
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["key_configured"] is True
    assert data["api_key_mask"] == "gnews-****8899"
    assert news_secret not in r.text

    # 清空加密 Key
    r2 = client.patch(
        f"/api/sources/{data['id']}",
        json={"clear_api_key": True},
    )
    assert r2.status_code == 200
    assert r2.json()["key_configured"] is False
    assert r2.json()["api_key_mask"] is None
