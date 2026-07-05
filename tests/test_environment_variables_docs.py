from pathlib import Path


DOC_PATH = Path(__file__).resolve().parents[1] / "website" / "docs" / "reference" / "environment-variables.md"


def test_fuxi_contract_tool_env_vars_are_documented():
    text = DOC_PATH.read_text(encoding="utf-8")
    required = [
        "FUXI_CONTRACT_AUTH_MODE",
        "FUXI_CONTRACT_HMAC_SECRET_ENV",
        "FUXI_CONTRACT_JWT_ENDPOINT",
        "DIRECTOR_JWT_JWKS_URL",
        "FUXI_CONTRACT_GOAL_ID",
    ]

    missing = [name for name in required if name not in text]

    assert missing == [], f"Missing FUXI contract env vars from docs: {missing}"
