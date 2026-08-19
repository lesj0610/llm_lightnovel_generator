"""
character_editor.py
===================
캐릭터 풀 편집기 (TUI). 소설 생성기와 별도 프로세스로 동작한다.

한 번에 캐릭터 한 명을 편집하고, 역할(주인공/상대방)을 지정해 풀에 쌓는다.
풀에서 주인공 1명 + 상대방 1명을 골라 '활성'으로 지정하면 character.json이
만들어지고, 소설 생성기가 그 조합으로 집필한다.

실행:
    python3 character_editor.py

키:
    F2 저장(풀에 ID 부여)   F3 불러오기   F4 새 캐릭터
    F5 LLM 채우기           F6 랜덤 채우기
    F7 주인공으로 지정      F8 상대방으로 지정
    Ctrl+Q 종료

외모/성격 서술은 한국어·영어·일본어 아무 언어로나 써도 되며,
LLM이 의미를 보존해 한국어로 정규화하고 태그를 분류한다.
"""

import argparse
import json
import os
import tempfile

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Input, Label, OptionList, RadioButton,
                             RadioSet, Static, TabbedContent, TabPane, TextArea)
from textual.widgets.option_list import Option

import config
import character_gen
import character_pool

# 주인공 편집 시 보여주는 확정 태그 (표시명, config 속성)
TAG_FIELDS = [
    ("머리색", "hair_color"),
    ("헤어스타일", "hair_style"),
    ("눈 색", "eye_color"),
    ("피부색", "skin_color"),
    ("얼굴", "face_style"),
    ("성격 분류", "personality_real"),
    ("가슴", "breasts_size"),
    ("엉덩이", "hip_size"),
    ("몸매", "body_size"),
]

# body_dic 인덱스로 저장되는 필드 (표시할 땐 실제 값으로 변환)
INDEX_FIELDS = {"breasts_size", "hip_size", "body_size"}

NOTE_FIELDS = [("외모 특이사항", "appearance_note"), ("성격 상세", "personality_note")]


def _display_value(attr, candidates):
    value = getattr(config, attr, "")
    if value in ("", -1, None):
        return "(미정)"
    if attr in INDEX_FIELDS and isinstance(value, int):
        items = candidates.get(attr) or []
        if 0 <= value < len(items):
            return str(items[value])
        return f"(인덱스 {value})"
    return str(value)


class CandidateSelectScreen(ModalScreen):
    """후보 목록에서 하나를 고르는 모달 (검색 필터 포함)."""

    CSS = """
    CandidateSelectScreen { align: center middle; }
    #box { width: 80%; height: 80%; border: round $accent; background: $surface; padding: 1; }
    #cand-list { height: 1fr; }
    #hint { color: $text-muted; }
    """

    def __init__(self, title, items):
        super().__init__()
        self._title = title
        self._items = list(items)

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label(f"[b]{self._title}[/b] — 화살표 이동, Enter 선택, Esc 취소")
            yield Input(placeholder="검색어 (예: brown)", id="filter")
            yield OptionList(*self._options(self._items), id="cand-list")
            yield Label(f"총 {len(self._items)}개", id="hint")

    def _options(self, items):
        if not items:
            return [Option("(후보 없음)", id="-1")]
        return [Option(f"{self._items.index(t):>3}  {t}", id=str(self._items.index(t)))
                for t in items]

    def on_input_changed(self, event: Input.Changed) -> None:
        needle = event.value.strip().lower()
        shown = [t for t in self._items if needle in t.lower()] if needle else self._items
        option_list = self.query_one("#cand-list", OptionList)
        option_list.clear_options()
        for option in self._options(shown):
            option_list.add_option(option)
        self.query_one("#hint", Label).update(f"{len(shown)}/{len(self._items)}개 표시")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        try:
            index = int(event.option.id)
        except (TypeError, ValueError):
            index = -1
        self.dismiss(index if index >= 0 else None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class CharacterEditorApp(App):
    """캐릭터 풀 편집기."""

    CSS = """
    Screen { layout: vertical; }
    #tabs { height: 1fr; }
    .field-row { height: 3; }
    .field-label { width: 14; content-align: left middle; }
    .tag-row { height: 3; }
    .tag-label { width: 16; content-align: left middle; }
    .tag-value { width: 1fr; content-align: left middle; color: $success; }
    .desc { height: 7; border: round $primary; }
    #pool-actions { height: 3; }
    #actions { dock: bottom; height: 3; }
    #status { dock: bottom; height: 1; background: $panel; }
    """

    BINDINGS = [
        ("f2", "save", "저장"),
        ("f3", "load", "불러오기"),
        ("f4", "new_character", "새 캐릭터"),
        ("f5", "llm_fill", "LLM 채우기"),
        ("f6", "random_fill", "랜덤"),
        ("f7", "activate_protagonist", "주인공 지정"),
        ("f8", "activate_partner", "상대방 지정"),
        ("ctrl+q", "quit", "종료"),
    ]

    def __init__(self):
        super().__init__()
        self.candidates = character_gen.load_candidates()
        self.current_id = None          # 편집 중인 풀 ID (None = 새 캐릭터)
        self._busy = False

    # -----------------------------------------------------------------
    # 레이아웃
    # -----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with TabbedContent(id="tabs"):
            with TabPane("캐릭터 풀", id="tab-pool"):
                with Vertical():
                    yield Static(
                        "Enter로 불러와 편집합니다. 주인공 1명 + 상대방 1명을 지정하면 "
                        "소설 생성기가 그 조합으로 집필합니다.\n")
                    yield OptionList(id="pool-list")
                    with Horizontal(id="pool-actions"):
                        yield Button("새 캐릭터(F4)", id="pool-new")
                        yield Button("불러오기(F3)", id="pool-load", variant="primary")
                        yield Button("주인공 지정(F7)", id="pool-act-pro", variant="success")
                        yield Button("상대방 지정(F8)", id="pool-act-par", variant="success")
                        yield Button("삭제", id="pool-delete", variant="error")
                    yield Label("", id="active-info")

            with TabPane("편집", id="tab-edit"):
                with VerticalScroll():
                    yield Static("[b]역할[/b] — 주인공은 외모 태그까지 확정하고, "
                                 "상대방은 자유 서술만 씁니다.")
                    with RadioSet(id="role"):
                        yield RadioButton("주인공", value=True, id="role-pro")
                        yield RadioButton("상대방", id="role-par")
                    yield Static("\n비워두면 LLM이 나머지 설정을 근거로 채웁니다.\n")
                    with Horizontal(classes="field-row"):
                        yield Label("이름", classes="field-label")
                        yield Input(placeholder="비우면 자동 생성", id="f-name")
                    with Horizontal(classes="field-row"):
                        yield Label("성별", classes="field-label")
                        yield Input(placeholder="여자 / 남자", id="f-sex")
                    with Horizontal(classes="field-row"):
                        yield Label("나이", classes="field-label")
                        yield Input(placeholder="숫자", id="f-age")
                    with Horizontal(classes="field-row"):
                        yield Label("직업", classes="field-label")
                        yield Input(placeholder="비우면 LLM이 추론", id="f-job")
                    with Horizontal(classes="field-row"):
                        yield Label("말투", classes="field-label")
                        yield Input(placeholder="상대방일 때 사용", id="f-talking_style")
                    yield Label("외모 서술 (한/영/일 아무 언어나 — 한국어로 저장됨)")
                    yield TextArea("", id="f-appearance", classes="desc")
                    yield Label("성격 서술 (한/영/일 아무 언어나 — 한국어로 저장됨)")
                    yield TextArea("", id="f-personality", classes="desc")

            with TabPane("확정 결과", id="tab-tags"):
                with VerticalScroll():
                    yield Static("F5(LLM) 또는 F6(랜덤)으로 채운 뒤 [변경]으로 수정하세요.\n"
                                 "태그는 주인공에만 적용됩니다.\n")
                    for label, attr in TAG_FIELDS:
                        with Horizontal(classes="tag-row"):
                            yield Label(label, classes="tag-label")
                            yield Label("(미정)", id=f"tag-{attr}", classes="tag-value")
                            yield Button("변경", id=f"btn-{attr}", variant="primary")
                    yield Static("\n[b]정규화된 서술[/b] (한국어)\n")
                    for label, attr in NOTE_FIELDS:
                        with Horizontal(classes="tag-row"):
                            yield Label(label, classes="tag-label")
                            yield Label("(미정)", id=f"tag-{attr}", classes="tag-value")

            with TabPane("프리뷰", id="tab-preview"):
                yield TextArea("F5/F6로 채운 뒤 이 탭을 열면 캐릭터 시트가 표시됩니다.",
                               id="preview", read_only=True)

        with Horizontal(id="actions"):
            yield Button("F5 LLM 채우기", id="act-llm", variant="success")
            yield Button("F6 랜덤", id="act-random")
            yield Button("F2 저장", id="act-save", variant="primary")
        yield Label("준비됨", id="status")

    def on_mount(self) -> None:
        self.refresh_pool()
        self._status("새 캐릭터를 작성하거나 풀에서 불러오세요 (F2로 저장)")

    # -----------------------------------------------------------------
    # 폼 접근
    # -----------------------------------------------------------------

    def _status(self, message):
        self.query_one("#status", Label).update(message)

    def _get(self, widget_id, default=""):
        try:
            widget = self.query_one(f"#{widget_id}")
        except Exception:
            return default
        if isinstance(widget, TextArea):
            return widget.text.strip()
        return widget.value.strip()

    def _set(self, widget_id, value):
        try:
            widget = self.query_one(f"#{widget_id}")
        except Exception:
            return
        text = "" if value is None else str(value)
        if isinstance(widget, TextArea):
            widget.text = text
        else:
            widget.value = text

    @property
    def role(self):
        try:
            pressed = self.query_one("#role", RadioSet).pressed_button
            if pressed is not None and pressed.id == "role-par":
                return character_pool.ROLE_PARTNER
        except Exception:
            pass
        return character_pool.ROLE_PROTAGONIST

    def _set_role(self, role):
        try:
            radio_set = self.query_one("#role", RadioSet)
            target = "role-par" if role == character_pool.ROLE_PARTNER else "role-pro"
            for button in radio_set.query(RadioButton):
                button.value = (button.id == target)
        except Exception:
            pass

    def collect_character(self):
        def _age(raw):
            raw = raw.strip()
            if not raw:
                return None
            try:
                return int(raw)
            except ValueError:
                return None

        return {
            "name": self._get("f-name"),
            "sex": self._get("f-sex"),
            "age": _age(self._get("f-age")),
            "job": self._get("f-job"),
            "appearance": self._get("f-appearance"),
            "personality": self._get("f-personality"),
            "talking_style": self._get("f-talking_style"),
        }

    def collect_resolved(self):
        """현재 config에 확정된 값 중 이 역할에 해당하는 것만."""
        allowed = (character_pool.PROTAGONIST_RESOLVED
                   if self.role == character_pool.ROLE_PROTAGONIST
                   else character_pool.PARTNER_RESOLVED)
        resolved = {}
        for attr in allowed:
            value = getattr(config, attr, "")
            if value not in ("", -1, None):
                resolved[attr] = value
        return resolved

    def refresh_tags(self):
        for _, attr in TAG_FIELDS + NOTE_FIELDS:
            try:
                self.query_one(f"#tag-{attr}", Label).update(
                    _display_value(attr, self.candidates))
            except Exception:
                pass

    def _clear_resolved(self):
        for _, attr in TAG_FIELDS:
            setattr(config, attr, -1 if attr in INDEX_FIELDS else "")
        for _, attr in NOTE_FIELDS:
            setattr(config, attr, "")
        config.locked_fields.clear()
        self.refresh_tags()

    # -----------------------------------------------------------------
    # 풀
    # -----------------------------------------------------------------

    def refresh_pool(self):
        try:
            option_list = self.query_one("#pool-list", OptionList)
        except Exception:
            return
        items = character_pool.list_characters()
        pro_id, par_id = character_pool.get_active_ids()
        option_list.clear_options()
        if not items:
            option_list.add_option(Option("(풀이 비어 있습니다 — F2로 저장하세요)", id="-1"))
        else:
            for item in items:
                if item["id"] == pro_id:
                    mark = "★주"
                elif item["id"] == par_id:
                    mark = "★상"
                else:
                    mark = "  "
                age = item["age"] if item["age"] is not None else "?"
                done = "확정" if item["has_resolved"] else "미확정"
                option_list.add_option(Option(
                    f"{mark} [{item['id']:04d}] {character_pool.ROLE_LABEL[item['role']]}"
                    f" | {item['label']} ({item['sex'] or '?'}/{age}/"
                    f"{item['job'] or '직업미정'}) {done}",
                    id=str(item["id"])))
        try:
            info = []
            if pro_id is not None:
                info.append(f"주인공 [{pro_id:04d}]")
            if par_id is not None:
                info.append(f"상대방 [{par_id:04d}]")
            self.query_one("#active-info", Label).update(
                "현재 활성: " + (" + ".join(info) if info else "(지정 안 됨)"))
        except Exception:
            pass

    def _selected_pool_id(self):
        try:
            option_list = self.query_one("#pool-list", OptionList)
            index = option_list.highlighted
            if index is None:
                return None
            value = int(option_list.get_option_at_index(index).id)
            return value if value >= 0 else None
        except Exception:
            return None

    def _load_from_pool(self, char_id, quiet=False):
        try:
            entry = character_pool.load(char_id)
        except (FileNotFoundError, ValueError) as e:
            self._status(f"불러오기 실패: {e}")
            return
        character = entry.get("character") or {}
        self._set_role(entry.get("role") or character_pool.ROLE_PROTAGONIST)
        for key in character_pool.CHARACTER_FIELDS:
            self._set(f"f-{key}", character.get(key))
        self._clear_resolved()
        for attr, value in (entry.get("resolved") or {}).items():
            setattr(config, attr, value)
            config.lock_field(attr)
        self.refresh_tags()
        self.current_id = int(char_id)
        if not quiet:
            self._status(f"[{self.current_id:04d}] {entry.get('label','')} 불러옴")

    # -----------------------------------------------------------------
    # 액션
    # -----------------------------------------------------------------

    def action_save(self) -> None:
        character = self.collect_character()
        try:
            char_id, path = character_pool.save(
                character, self.role, resolved=self.collect_resolved(),
                char_id=self.current_id)
        except (OSError, ValueError) as e:
            self._status(f"저장 실패: {e}")
            return
        is_new = self.current_id is None
        self.current_id = char_id
        self.refresh_pool()
        self._status(f"{'새 캐릭터 저장' if is_new else '저장'} 완료 — "
                     f"[{char_id:04d}] {character_pool.ROLE_LABEL[self.role]} "
                     f"({os.path.basename(path)})")

    def action_load(self) -> None:
        char_id = self._selected_pool_id()
        if char_id is None:
            self._status("풀 탭에서 캐릭터를 선택하세요")
            return
        self._load_from_pool(char_id)

    def action_new_character(self) -> None:
        self.current_id = None
        for key in character_pool.CHARACTER_FIELDS:
            self._set(f"f-{key}", "")
        self._clear_resolved()
        self._status("새 캐릭터 — 작성 후 F2로 저장하면 새 ID가 부여됩니다")

    def _activate(self, role):
        char_id = self._selected_pool_id()
        if char_id is None:
            self._status("풀 탭에서 캐릭터를 선택하세요")
            return
        try:
            if role == character_pool.ROLE_PROTAGONIST:
                character_pool.set_active(protagonist_id=char_id)
            else:
                character_pool.set_active(partner_id=char_id)
        except (FileNotFoundError, OSError, ValueError) as e:
            self._status(f"지정 실패: {e}")
            return
        self.refresh_pool()
        self._status(f"[{char_id:04d}]을(를) {character_pool.ROLE_LABEL[role]}으로 지정")

    def action_activate_protagonist(self) -> None:
        self._activate(character_pool.ROLE_PROTAGONIST)

    def action_activate_partner(self) -> None:
        self._activate(character_pool.ROLE_PARTNER)

    def action_delete_character(self) -> None:
        char_id = self._selected_pool_id()
        if char_id is None:
            self._status("풀 탭에서 캐릭터를 선택하세요")
            return
        try:
            character_pool.delete(char_id)
        except (FileNotFoundError, OSError) as e:
            self._status(f"삭제 실패: {e}")
            return
        if self.current_id == char_id:
            self.current_id = None
        self.refresh_pool()
        self._status(f"[{char_id:04d}] 삭제됨")

    # -----------------------------------------------------------------
    # 채우기
    # -----------------------------------------------------------------

    def action_llm_fill(self) -> None:
        if self._busy:
            self._status("작업 중입니다...")
            return
        self._busy = True
        self._status("LLM에게 요청 중... (수 초 걸립니다)")
        self._worker_llm_fill(self.role, self.collect_character())

    @work(thread=True, exclusive=True, group="fill")
    def _worker_llm_fill(self, role, character) -> None:
        tmp_path = None
        try:
            # 편집 중인 캐릭터를 해당 역할 자리에 넣어 매핑을 요청한다
            if role == character_pool.ROLE_PROTAGONIST:
                spec = {"protagonist": character, "partner": {}}
            else:
                spec = {"protagonist": {}, "partner": character}
            spec["on_mapping_failure"] = "llm_auto"
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8") as f:
                json.dump(spec, f, ensure_ascii=False)
                tmp_path = f.name
            result = character_gen.apply_character_spec(path=tmp_path, force=True)
            if role == character_pool.ROLE_PARTNER:
                # 상대방 결과는 partner 속성에 담기므로 역할 중립 이름으로 옮긴다
                for src, dst in (("appearance_note2", "appearance_note"),
                                 ("personality_note2", "personality_note")):
                    value = getattr(config, src, "")
                    if value:
                        setattr(config, dst, value)
                        config.lock_field(dst)
            failed = result.get("failed") or []
            report = character_gen.format_failure_report(failed) if failed else ""
            message = (f"일부 항목 실패 ({len(failed)}개) — 확정 결과 탭에서 [변경]으로 지정하세요"
                       if failed else "LLM 채우기 완료")
            self.call_from_thread(self._after_fill, message, report)
        except Exception as e:
            self.call_from_thread(self._after_fill,
                                  f"실패: {type(e).__name__}: {e}", "")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def action_random_fill(self) -> None:
        if self._busy:
            return
        if self.role == character_pool.ROLE_PARTNER:
            self._status("상대방은 태그가 없습니다 — F5(LLM)로 서술을 정규화하세요")
            return
        filled = 0
        for _, attr in TAG_FIELDS:
            if getattr(config, attr, "") in ("", -1, None):
                setattr(config, attr, character_gen.random_choice_for(attr, self.candidates))
                config.lock_field(attr)
                filled += 1
        self.refresh_tags()
        self._status(f"랜덤으로 {filled}개 채움 (LLM 미사용)")

    def _after_fill(self, message, report):
        self._busy = False
        self.refresh_tags()
        self._status(message)
        if report:
            self.query_one("#preview", TextArea).text = report

    def _open_candidate_select(self, attr, label):
        items = self.candidates.get(attr) or []
        if not items:
            self._status(f"{label}: 후보 목록이 없습니다")
            return

        def _done(index):
            if index is None:
                return
            _applied, rejected = character_gen.apply_manual_choices({attr: index})
            if rejected:
                self._status(f"{label} 선택 실패: {rejected[0]['reason']}")
            else:
                self.refresh_tags()
                self._status(f"{label} 설정: {_display_value(attr, self.candidates)}")

        self.push_screen(CandidateSelectScreen(label, items), _done)

    # -----------------------------------------------------------------
    # 이벤트
    # -----------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        handlers = {
            "act-llm": self.action_llm_fill,
            "act-random": self.action_random_fill,
            "act-save": self.action_save,
            "pool-new": self.action_new_character,
            "pool-load": self.action_load,
            "pool-act-pro": self.action_activate_protagonist,
            "pool-act-par": self.action_activate_partner,
            "pool-delete": self.action_delete_character,
        }
        handler = handlers.get(button_id)
        if handler:
            handler()
        elif button_id.startswith("btn-"):
            attr = button_id[4:]
            label = next((l for l, a in TAG_FIELDS if a == attr), attr)
            self._open_candidate_select(attr, label)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "pool-list":
            return
        try:
            char_id = int(event.option.id)
        except (TypeError, ValueError):
            return
        if char_id >= 0:
            self._load_from_pool(char_id)

    def on_tabbed_content_tab_activated(self, event) -> None:
        if getattr(event.pane, "id", "") == "tab-preview":
            self._render_preview()

    def _render_preview(self):
        import character_setup
        try:
            character = self.collect_character()
            if self.role == character_pool.ROLE_PROTAGONIST:
                for key, attr in (("name", "name"), ("sex", "sex"),
                                  ("age", "age"), ("job", "job")):
                    if character.get(key):
                        setattr(config, attr, character[key])
                text, _ = character_setup.character_sheet(0)
            else:
                for key, attr in (("name", "name2"), ("sex", "sex2"),
                                  ("age", "age2"), ("job", "job2"),
                                  ("talking_style", "talking_style2")):
                    if character.get(key):
                        setattr(config, attr, character[key])
                if getattr(config, "personality_note", ""):
                    config.personality_note2 = config.personality_note
                if getattr(config, "appearance_note", ""):
                    config.appearance_note2 = config.appearance_note
                text = character_setup.partner_sheet()
            self.query_one("#preview", TextArea).text = text
        except Exception as e:
            self.query_one("#preview", TextArea).text = (
                f"프리뷰 생성 실패: {type(e).__name__}: {e}\n\n"
                "F5 또는 F6으로 항목을 먼저 채워주세요.")


def main():
    parser = argparse.ArgumentParser(description="캐릭터 풀 편집기")
    parser.parse_args()
    CharacterEditorApp().run()


if __name__ == "__main__":
    main()
