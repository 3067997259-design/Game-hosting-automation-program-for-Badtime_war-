"""GA I0：N0装配、拒绝错误参数、严格读取与快照隔离；不依赖可变手册正文。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import warnings

import pytest

from engine import balance
from engine.balance import BalanceConfigError
from engine.ga.config import GaConfigError, load_ga_config, parameter_specs, validate_ga_config


ROOT = Path(__file__).resolve().parents[1]
BALANCE_FILE = ROOT / "data" / "balance.json"
N0 = json.loads(BALANCE_FILE.read_text(encoding="utf-8"))["ga"]


def _leaves(value: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """从实际配置枚举叶子，与校验器schema的实现独立。"""
    if isinstance(value, dict):
        return [path for key, child in value.items() for path in _leaves(child, (*prefix, key))]
    if isinstance(value, list):
        return [path for index, child in enumerate(value)
                for path in _leaves(child, (*prefix, index))]
    return [prefix]


def _path(keys: tuple[Any, ...]) -> str:
    """把夹具定位路径转换为面向调用者的参数路径。"""
    return "ga" + "".join(f"[{key}]" if isinstance(key, int) else f".{key}" for key in keys)


def _parent(data: dict[str, Any], keys: tuple[Any, ...]) -> Any:
    """在测试的独立副本内定位修改处，不修改生产配置或缓存。"""
    value: Any = data
    for key in keys[:-1]:
        value = value[key]
    return value


@pytest.fixture(autouse=True)
def isolated_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    """每例使用独立加载器缓存；退出恢复调用者原缓存和文件路径。"""
    monkeypatch.setattr(balance, "_balance", None)
    monkeypatch.setattr(balance, "_BALANCE_PATH", str(BALANCE_FILE))


@pytest.fixture
def raw_ga() -> dict[str, Any]:
    """独立N0副本，错误注入不写真实文件。"""
    return deepcopy(N0)


def test_repository_n0_matches_approved_scale() -> None:
    """装配关键锚点与独立数值算例，避免误继承旧M9的量纲。"""
    cfg = load_ga_config()
    assert (cfg.get("start", "hp"), cfg.get("start", "credits"), cfg.get("start", "sp")) == (
        20, 4, 1
    )
    assert (cfg.get("start", "arrows"), cfg.get("start", "arrow_cap")) == (5, 10)
    assert cfg.get("weapons", "bow", "damage") == 4
    assert [cfg.get("armor", key, "durability")
            for key in ("shield", "ceramic", "magic_shield", "at_field")] == [6, 10, 8, 10]
    assert cfg.get("talents", "G7", "shot_distribution") == (2, 1, 1)
    assert cfg.get("talents", "G7", "nap_repair") == 2
    assert cfg.get("talents", "G7", "halo_combat_recovery") == 0
    assert cfg.get("talents", "G6", "self_forfeit_laugh") == 0
    assert cfg.get("talents", "G3", "magic_cap") + cfg.get(
        "talents", "G3", "public_temp_magic"
    ) - sum(cfg.get("talents", "G3", "chain_costs")) == 3
    assert cfg.get("scoring", "kill_weight") == cfg.get("scoring", "crown") == 6
    assert cfg.get("clock", "max_rounds") == 40


def test_all_fourteen_talents_and_numeric_sources_are_present() -> None:
    """正式参数覆盖所有槽位与13节初值；没有T5占位或无来源字段。"""
    cfg = load_ga_config()
    assert set(cfg.get("talents")) == {"T1", "T2", "T3", "T4", "T6", "T7",
                                        "G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"}
    specs = parameter_specs()
    assert set(specs) == {_path(path) for path in _leaves(N0)}
    sections = {spec.source_section.split(";")[0] for spec in specs.values()}
    assert {f"17.{index}" for index in range(1, 14)} <= sections
    assert all(spec.unit and spec.rounding for spec in specs.values())
    assert specs["ga.talents.G0.drone_hp_ratio"].rounding == "ceil_hp_cost_then_discount"
    assert specs["ga.talents.G0.breath_lifesteal_ratio"].rounding == "floor_root_hp_total"


@pytest.mark.parametrize("keys", _leaves(N0), ids=_path)
def test_every_required_leaf_fails_closed_when_removed(
    raw_ga: dict[str, Any], keys: tuple[Any, ...]
) -> None:
    """逐个删除真实输入叶子，验证没有字段落回旧版默认值。"""
    del _parent(raw_ga, keys)[keys[-1]]
    with pytest.raises(GaConfigError) as caught:
        validate_ga_config(raw_ga)
    expected = _path(keys[:-1]) if isinstance(keys[-1], int) else _path(keys)
    assert caught.value.path == expected


@pytest.mark.parametrize("group", list(N0))
def test_missing_parameter_group_reports_its_path(raw_ga: dict[str, Any], group: str) -> None:
    del raw_ga[group]
    with pytest.raises(GaConfigError) as caught:
        validate_ga_config(raw_ga)
    assert caught.value.path == f"ga.{group}"


@pytest.mark.parametrize("keys,value", [
    (("start", "hp"), True),
    (("start", "hp"), 20.0),
    (("start", "hp"), "20"),
    (("start", "hp"), None),
    (("start", "hp"), -1),
    (("hospital", "heal_price"), -1),
    (("accuracy", "move_evasion"), True),
    (("accuracy", "move_evasion"), 15),
    (("accuracy", "move_evasion"), -0.01),
    (("accuracy", "move_evasion"), float("nan")),
    (("accuracy", "move_evasion"), float("inf")),
    (("accuracy", "move_evasion"), 10**500),
    (("scoring", "damage_divisor"), 0),
    (("talents", "G7", "shot_distribution"), [2, 1]),
    (("talents", "G7", "shot_distribution"), [2, True, 1]),
    (("talents", "G7", "shot_distribution"), "2,1,1"),
    (("_meta", "ruleset"), "m9-rfc"),
    (("_meta", "schema_version"), True),
    (("_meta", "source_sha256"), "not-a-source-digest"),
    (("talents", "G7"), []),
])
def test_rejects_bad_types_ranges_and_nonfinite_values(
    raw_ga: dict[str, Any], keys: tuple[Any, ...], value: Any
) -> None:
    _parent(raw_ga, keys)[keys[-1]] = value
    with pytest.raises(GaConfigError) as caught:
        validate_ga_config(raw_ga)
    assert caught.value.path.startswith(_path(keys))


@pytest.mark.parametrize("keys,value", [
    (("start", "hp"), 21),
    (("start", "hp"), 19),
    (("start", "sp"), 3),
    (("start", "arrows"), 11),
    (("start", "max_players"), 7),
    (("action", "t0_per_turn"), 2),
    (("action", "extra_turns_per_round"), 2),
    (("accuracy", "maximum"), 0.9),
    (("modules", "infinite", "occupied_slots"), 1),
    (("modules", "pierce", "double_coefficient"), 0.75),
    (("hospital", "syringe_hold_cap"), 3),
    (("weapons", "gauss_rifle", "initial_ammo"), 9),
    (("clock", "dusk_start"), 5),
    (("clock", "max_rounds"), 10),
    (("talents", "G0", "initial_ammo"), 31),
    (("talents", "G0", "initial_ammo"), 14),
    (("talents", "G1", "entropy_threshold"), 13),
    (("talents", "G2", "loneliness_initial"), 5),
    (("talents", "G3", "magic_initial"), 9),
    (("talents", "G3", "chain_costs"), [2, 4, 3]),
    (("talents", "G3", "chain_costs"), [1, 3, 4]),
    (("talents", "G4", "lethal_threshold"), 13),
    (("talents", "G5", "ascension_memory"), 13),
    (("talents", "G6", "laugh_initial"), 4),
    (("talents", "G6", "gift_face_probability"), 0.2),
    (("talents", "G6", "gift_faces"), 5),
    (("talents", "G6", "self_forfeit_laugh"), 1),
    (("talents", "G7", "halo_initial_layers"), 4),
    (("talents", "G7", "initial_loaded_ammo"), 9),
    (("talents", "G7", "macro_costs", "shoot"), 0),
    (("talents", "G7", "macro_costs", "shoot"), 7),
    (("talents", "G7", "shot_pool"), 5),
    (("talents", "G7", "free_shot_distribution"), [2, 1, 1]),
    (("talents", "G7", "automatic_repair"), 1),
    (("talents", "G7", "terror_hp_floor"), 21),
    (("poems", "law_roster"), 4),
    (("poems", "shore_knife_damage"), 99),
    (("combat", "min_damage_ratio"), 0),
    (("talents", "T7", "revive_hp_ratio"), 0),
    (("talents", "T7", "revive_sp"), 3),
    (("talents", "T7", "poem_insurance_uses"), 3),
])
def test_rejects_valid_individual_fields_with_invalid_combinations(
    raw_ga: dict[str, Any], keys: tuple[Any, ...], value: Any
) -> None:
    """单字段类型合法，组合却超容量、破坏守恒或违背已决额度时拒绝。"""
    _parent(raw_ga, keys)[keys[-1]] = value
    with pytest.raises(GaConfigError):
        validate_ga_config(raw_ga)


@pytest.mark.parametrize("keys", [(), ("talents", "G0"), ("start",)])
def test_unknown_keys_cannot_hide_a_typo(
    raw_ga: dict[str, Any], keys: tuple[Any, ...]
) -> None:
    target = raw_ga
    for key in keys:
        target = target[key]
    target["drone_hpp_typo"] = 4
    with pytest.raises(GaConfigError) as caught:
        validate_ga_config(raw_ga)
    assert caught.value.path == _path((*keys, "drone_hpp_typo"))


def test_missing_ga_never_reads_legacy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(balance, "_balance", {"hp20": {"player_max_hp": 20}})
    with pytest.raises(BalanceConfigError, match="missing required key") as caught:
        load_ga_config()
    assert caught.value.path == "ga"


def test_required_reader_preserves_full_requested_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(balance, "_balance", {"ga": {"start": 7}})
    with pytest.raises(BalanceConfigError) as caught:
        balance.get_required("ga", "start", "hp")
    assert caught.value.path == "ga.start.hp"


@pytest.mark.parametrize("contents", ["{", "[]", "null"])
def test_strict_file_loading_rejects_malformed_json_and_nonobjects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contents: str
) -> None:
    path = tmp_path / "balance.json"
    path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(balance, "_BALANCE_PATH", str(path))
    with warnings.catch_warnings(record=True) as emitted:
        with pytest.raises(BalanceConfigError) as caught:
            load_ga_config()
    assert caught.value.path == "balance"
    assert not emitted


def test_strict_missing_file_has_no_empty_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(balance, "_BALANCE_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(BalanceConfigError, match="file not found"):
        load_ga_config()
    assert balance._balance is None


def test_strict_loading_reports_unreadable_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bad-encoding.json"
    path.write_bytes(b"\xff")
    monkeypatch.setattr(balance, "_BALANCE_PATH", str(path))
    with pytest.raises(BalanceConfigError) as caught:
        load_ga_config()
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)
    assert caught.value.path == "balance"


def test_strict_loading_reports_permission_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("test denied access")

    monkeypatch.setattr(balance, "open", denied, raising=False)
    with pytest.raises(BalanceConfigError) as caught:
        load_ga_config()
    assert isinstance(caught.value.__cause__, PermissionError)
    assert caught.value.path == "balance"


def test_legacy_optional_reader_still_has_explicit_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(balance, "_BALANCE_PATH", str(tmp_path / "missing.json"))
    with pytest.warns(UserWarning):
        assert balance.get("old", "optional", default=7) == 7
    with pytest.raises(BalanceConfigError):
        load_ga_config()


def test_snapshot_cannot_mutate_cache_or_its_nested_values(raw_ga: dict[str, Any]) -> None:
    cfg = validate_ga_config(raw_ga)
    with pytest.raises(TypeError):
        cfg.data["start"]["hp"] = 1
    with pytest.raises(TypeError):
        cfg.get("talents", "G7", "shot_distribution")[0] = 100
    raw_ga["start"]["hp"] = 2
    exported = cfg.to_dict()
    exported["start"]["hp"] = 3
    exported["talents"]["G7"]["shot_distribution"][0] = 100
    assert cfg.get("start", "hp") == 20
    assert cfg.get("talents", "G7", "shot_distribution") == (2, 1, 1)


def test_load_snapshot_and_export_do_not_mutate_shared_balance_cache() -> None:
    cfg = load_ga_config()
    cfg.to_dict()["start"]["hp"] = 1
    assert balance.get_required("ga", "start", "hp") == 20
    balance.get_required("ga", "hospital")["heal_price"] = 4
    assert cfg.get("start", "hp") == 20
    assert cfg.get("hospital", "heal_price") == 2
    assert load_ga_config().get("hospital", "heal_price") == 4


def test_refresh_loads_file_without_changing_existing_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_ga: dict[str, Any]
) -> None:
    path = tmp_path / "balance.json"
    path.write_text(json.dumps({"ga": raw_ga}), encoding="utf-8")
    monkeypatch.setattr(balance, "_BALANCE_PATH", str(path))
    first = load_ga_config()
    raw_ga["hospital"]["heal_price"] = 3
    path.write_text(json.dumps({"ga": raw_ga}), encoding="utf-8")
    assert load_ga_config().get("hospital", "heal_price") == 2
    refreshed = load_ga_config(refresh=True)
    assert refreshed.get("hospital", "heal_price") == 3
    assert first.get("hospital", "heal_price") == 2
    assert refreshed.fingerprint != first.fingerprint


def test_tuning_and_serialization_have_stable_semantic_fingerprint(raw_ga: dict[str, Any]) -> None:
    first = validate_ga_config(raw_ga)
    reordered = json.loads(json.dumps(raw_ga, sort_keys=True))
    assert validate_ga_config(reordered).fingerprint == first.fingerprint
    reordered["accuracy"]["base"] = 1
    assert validate_ga_config(reordered).fingerprint == first.fingerprint
    raw_ga["_meta"]["source_sha256"] = "b" * 64
    assert validate_ga_config(raw_ga).fingerprint == first.fingerprint
    raw_ga["hospital"]["heal_price"] = 3
    assert validate_ga_config(raw_ga).fingerprint != first.fingerprint
    assert validate_ga_config(first.to_dict()).fingerprint == first.fingerprint


def test_importing_ga_does_not_load_balance_or_switch_profile() -> None:
    """独立进程验证候选组件不接管当前运行入口，也不加载模型依赖。"""
    script = (
        "from engine import balance, experiments; "
        "assert balance._balance is None; "
        "profile = experiments.current_profile(); "
        "import engine.ga.config; "
        "assert balance._balance is None; "
        "assert experiments.current_profile() == profile; "
        "import sys; assert 'torch' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=ROOT, capture_output=True, text=True,
        timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_n0_source_is_recorded_without_requiring_the_live_manual() -> None:
    """用户可并行修改手册，配置加载依赖捕获的来源标识而非当前文档内容。"""
    cfg = load_ga_config()
    assert cfg.get("_meta", "source").endswith("manual_draft_v0.1.md#17")
    assert len(cfg.get("_meta", "source_sha256")) == 64


@pytest.mark.parametrize("keys", [(), ("",), (1,)])
def test_required_readers_reject_ambiguous_paths(keys: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError):
        balance.get_required(*keys)
    with pytest.raises(ValueError):
        load_ga_config().get(*keys)
