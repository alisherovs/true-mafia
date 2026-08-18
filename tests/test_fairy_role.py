from app.enums import ActionType, Role, Team
from app.game_engine import GameEngine
from app.keyboards import ROLE_INFO_ORDER
from app.models import GamePlayer
from app.roles import SHOP_ROLE_BY_VALUE, generate_roles_for_game


def test_fairy_role_exists_and_is_shop_buyable() -> None:
    assert Role.FAIRY.value == "fairy"
    assert SHOP_ROLE_BY_VALUE["fairy"].role == Role.FAIRY


def test_fairy_appears_only_at_required_player_thresholds() -> None:
    classic_25 = generate_roles_for_game("classic", 25)
    classic_24 = generate_roles_for_game("classic", 24)
    super_15 = generate_roles_for_game("super", 15)
    super_14 = generate_roles_for_game("super", 14)
    mega_12 = generate_roles_for_game("mega", 12)
    mega_11 = generate_roles_for_game("mega", 11)

    assert Role.FAIRY in classic_25
    assert Role.FAIRY not in classic_24
    assert Role.FAIRY in super_15
    assert Role.FAIRY not in super_14
    assert Role.FAIRY in mega_12
    assert Role.FAIRY not in mega_11


def test_fairy_action_is_defined() -> None:
    assert ActionType.REVIVE.value == "revive"


def test_fairy_role_appears_in_role_info_menu() -> None:
    assert Role.FAIRY in ROLE_INFO_ORDER


def test_arsonist_selected_targets_are_visible_in_prompt() -> None:
    engine = object.__new__(GameEngine)
    actor = GamePlayer(
        telegram_id=101,
        display_name="Actor",
        role=Role.ARSONIST.value,
        team=Team.CITY.value,
        alive=True,
    )
    targets = [
        GamePlayer(telegram_id=1, display_name="A", role=Role.CITIZEN.value, team=Team.CITY.value, alive=True),
        GamePlayer(telegram_id=2, display_name="B", role=Role.CITIZEN.value, team=Team.CITY.value, alive=True),
        GamePlayer(telegram_id=3, display_name="C", role=Role.CITIZEN.value, team=Team.CITY.value, alive=True),
    ]

    text, keyboard = engine._night_prompt_for_player(1, 1, actor, targets, arson_marks={101: {2, 3}})

    assert "Tanlangan" in text
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert any("✅ B" in label or "B" in label for label in labels)
    assert any("✅ C" in label or "C" in label for label in labels)


def test_fairy_prompt_includes_dead_players_for_revive() -> None:
    engine = object.__new__(GameEngine)
    actor = GamePlayer(
        telegram_id=101,
        display_name="Actor",
        role=Role.FAIRY.value,
        team=Team.CITY.value,
        alive=True,
    )
    alive_targets = [
        GamePlayer(telegram_id=1, display_name="Alive", role=Role.CITIZEN.value, team=Team.CITY.value, alive=True),
    ]
    dead_targets = [
        GamePlayer(telegram_id=2, display_name="Dead One", role=Role.CITIZEN.value, team=Team.CITY.value, alive=False),
    ]

    text, keyboard = engine._night_prompt_for_player(
        1,
        1,
        actor,
        alive_targets,
        dead_players=dead_targets,
    )

    assert "Qaysi o'yinchini qayta tiriltirasiz?" in text
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert any("Dead One" in label for label in labels)


def test_fairy_prompt_is_hidden_after_revive_action_used() -> None:
    engine = object.__new__(GameEngine)
    actor = GamePlayer(
        telegram_id=101,
        display_name="Actor",
        role=Role.FAIRY.value,
        team=Team.CITY.value,
        alive=True,
    )
    alive_targets = [
        GamePlayer(telegram_id=1, display_name="Alive", role=Role.CITIZEN.value, team=Team.CITY.value, alive=True),
    ]
    dead_targets = [
        GamePlayer(telegram_id=2, display_name="Dead One", role=Role.CITIZEN.value, team=Team.CITY.value, alive=False),
    ]

    result = engine._night_prompt_for_player(
        1,
        1,
        actor,
        alive_targets,
        dead_players=dead_targets,
        fairy_revive_used=True,
    )

    assert result is None


def test_lone_mafia_and_killer_cannot_deadlock() -> None:
    mafia = GamePlayer(telegram_id=1, display_name="Mafia", role=Role.MAFIA.value, team=Team.MAFIA.value, alive=True)
    killer = GamePlayer(telegram_id=2, display_name="Killer", role=Role.KILLER.value, team=Team.KILLER.value, alive=True)

    assert GameEngine._winner_from_alive_snapshot([mafia, killer]) == Team.KILLER
