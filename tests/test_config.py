"""Config loading and the shape of the shipped YAML files."""

from __future__ import annotations

from aicvtailor.config import get_guardrails, get_model_prefs, get_settings, get_skills


def test_settings_load_with_no_env_file():
    settings = get_settings()
    assert settings.llm_provider in {"nim", "claude_cli", "ollama"}
    # Must stay under the free tier's ~40 rpm ceiling.
    assert 1 <= settings.llm_rpm <= 40


def test_guardrails_expose_the_documented_keys():
    rails = get_guardrails()
    for key in ("forbidden_claims", "never_reword", "max_bullet_length"):
        assert key in rails, f"guardrails.yaml is missing {key}"
    assert isinstance(rails["forbidden_claims"], list)
    assert isinstance(rails["never_reword"], list)
    assert isinstance(rails["max_bullet_length"], int)


def test_skills_entries_are_well_formed():
    skills = get_skills()
    assert skills["terms"], "skills.yaml should ship a starter dictionary"

    categories = {"hard_skill", "tool", "method", "domain", "soft_skill"}
    canonicals = set()
    for entry in skills["terms"]:
        assert entry["canonical"] not in canonicals, f"duplicate {entry['canonical']}"
        canonicals.add(entry["canonical"])
        assert entry["category"] in categories, f"{entry['canonical']}: bad category"
        assert isinstance(entry.get("synonyms", []), list)


def test_synonyms_are_not_claimed_by_two_canonical_terms():
    """An ambiguous synonym would make normalisation order-dependent."""
    owner: dict[str, str] = {}
    for entry in get_skills()["terms"]:
        for syn in entry.get("synonyms", []):
            key = syn.lower()
            assert key not in owner, f"'{syn}' claimed by both {owner[key]} and {entry['canonical']}"
            owner[key] = entry["canonical"]


def test_every_model_role_has_a_terminal_fallback():
    """Preference lists are hints against a live catalogue and will miss. Each
    role needs a last resort that does not depend on a lucky prefix match."""
    roles = get_model_prefs()["roles"]
    assert {"extractor", "rewriter"} <= set(roles)
    for name, role in roles.items():
        assert role.get("prefer"), f"{name} has no preference list"
        assert role.get("terminal_fallback"), f"{name} has no terminal_fallback"


def test_guardrails_local_override_merges(tmp_path, monkeypatch):
    """Personal entries live in a gitignored override, so they never reach a
    committed file. Lists concatenate, scalars are replaced."""
    import yaml

    from aicvtailor import config, paths

    override = tmp_path / "guardrails.local.yaml"
    override.write_text(
        yaml.safe_dump({"forbidden_claims": ["Project Placeholder"], "max_bullet_length": 99})
    )
    monkeypatch.setattr(paths, "GUARDRAILS_LOCAL_FILE", override)
    config.get_guardrails.cache_clear()

    rails = config.get_guardrails()
    assert "Project Placeholder" in rails["forbidden_claims"]
    assert rails["max_bullet_length"] == 99
    config.get_guardrails.cache_clear()
