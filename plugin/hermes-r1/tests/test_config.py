import copy

import pytest

from hermes_r1.bridge.config import DEFAULT_CONFIG, normalize_config


def test_empty_dict_gives_defaults():
    assert normalize_config({}) == DEFAULT_CONFIG


def test_defaults_are_a_fresh_copy():
    out = normalize_config({})
    out["quick_prompts"].append({"id": "x"})
    assert len(DEFAULT_CONFIG["quick_prompts"]) == 2


def test_non_dict_raises():
    for bad in (None, [], "x", 3):
        with pytest.raises(ValueError):
            normalize_config(bad)


def test_unknown_keys_dropped():
    out = normalize_config({"nope": 1, "theme": {"preset": "nous", "extra": 2}})
    assert "nope" not in out
    assert "extra" not in out["theme"]


def test_font_scale_clamped_and_junk_defaults():
    assert normalize_config({"font_scale": 9})["font_scale"] == 1.3
    assert normalize_config({"font_scale": 0.1})["font_scale"] == 0.85
    assert normalize_config({"font_scale": "big"})["font_scale"] == 1.0
    assert normalize_config({"font_scale": True})["font_scale"] == 1.0


def test_enums_fall_back_to_defaults():
    out = normalize_config({
        "theme": {"preset": "hotpink"},
        "voice": {"input": "telepathy"},
        "buttons": {"double_click": "explode", "long_press": "talk"},
    })
    assert out["theme"]["preset"] == "nous"
    assert out["voice"]["input"] == "auto"
    assert out["buttons"]["double_click"] == "back"
    assert out["buttons"]["long_press"] == "talk"


def test_theme_overrides_filtered():
    out = normalize_config({"theme": {"overrides": {
        "bg": "#112233",
        "primary": "hsl(210 68% 58%)",
        "fg": "url(evil)//;",
        "evil": "#000000",
        "accent": 5,
    }}})
    assert out["theme"]["overrides"] == {"bg": "#112233", "primary": "hsl(210 68% 58%)"}


def test_greeting_truncated_and_bools_coerced():
    out = normalize_config({"home": {"greeting": "x" * 100, "clock": "yes"}})
    assert len(out["home"]["greeting"]) == 60
    assert out["home"]["clock"] is True


def test_lists_are_strings_and_capped():
    out = normalize_config({"roster": {"order": ["a", 1, "b"] + [f"x{i}" for i in range(200)]}})
    assert out["roster"]["order"][:3] == ["a", "b", "x0"]
    assert len(out["roster"]["order"]) == 100


def test_quick_prompts_limits_and_ids():
    raw = [{"label": "L", "text": "T", "id": "keep"}, {"label": "", "text": "no label"},
           {"label": "x" * 40, "text": "y" * 900}, "junk"]
    raw += [{"label": f"p{i}", "text": "t"} for i in range(20)]
    out = normalize_config({"quick_prompts": raw})
    assert len(out["quick_prompts"]) == 12
    assert out["quick_prompts"][0]["id"] == "keep"
    assert out["quick_prompts"][1]["id"].startswith("q")
    assert len(out["quick_prompts"][1]["label"]) == 24
    assert len(out["quick_prompts"][1]["text"]) == 500


def test_missing_quick_prompts_get_defaults_but_explicit_empty_stays_empty():
    assert normalize_config({})["quick_prompts"] == DEFAULT_CONFIG["quick_prompts"]
    assert normalize_config({"quick_prompts": []})["quick_prompts"] == []


def test_avatar_overrides_only_shape_and_color():
    out = normalize_config({"avatar_overrides": {
        "coder": {"shape": "hexagon", "color": "#ff0000", "url": "http://evil"},
        "ops": {"color": "red;}"},
        "empty": {"other": 1},
        "bad": "string",
    }})
    # "ops" has an invalid colour and no other allowed key, so it is dropped entirely
    assert out["avatar_overrides"] == {"coder": {"shape": "hexagon", "color": "#ff0000"}}


def test_normalize_is_idempotent():
    raw = copy.deepcopy(DEFAULT_CONFIG)
    raw["font_scale"] = 1.2
    once = normalize_config(raw)
    assert normalize_config(once) == once
