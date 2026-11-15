from __future__ import annotations

import ast
import glob
import os
import re
from unittest import mock

from helpers import HERE, TempCase

from claude_sync import lang
from test_cli_backends import call

ENGINE = os.path.join(os.path.dirname(HERE), "claude_sync")
HANGUL = re.compile("[가-힣]")
# Korean that is data, not a message: the Notion database's columns and values (every PC reads them)
# and commit messages (the shared history), so they stay as they are in either language.
DATA = {
    "전체본", "변경분", "요약", "종류", "묶음", "변경",
    "지침", "설정", "스킬 %s", "충돌 보류", "합치기", "실행 파일 변경 거절", "되돌림: %s", "저장소 README 합치기",
}


def _untranslated(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    paired, problems = set(), []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body \
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            paired.add(id(node.body[0].value))  # a docstring
        pair = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t" and len(node.args) == 2:
            pair = node.args
        elif isinstance(node, ast.Tuple) and len(node.elts) == 2:
            pair = node.elts
        if pair and all(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in pair) \
                and HANGUL.search(pair[0].value) and pair[0].value not in DATA:
            if HANGUL.search(pair[1].value) or not pair[1].value.strip():
                problems.append("%s:%d no English: %s" % (os.path.basename(path), pair[1].lineno, pair[1].value))
            paired.add(id(pair[0]))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and HANGUL.search(node.value) \
                and id(node) not in paired and node.value not in DATA:
            problems.append("%s:%d Korean only: %s" % (os.path.basename(path), node.lineno, node.value[:60]))
    return problems


class LanguageTest(TempCase):
    def test_every_korean_message_has_an_english_twin(self):
        problems = []
        for path in sorted(glob.glob(os.path.join(ENGINE, "*.py"))):
            problems += _untranslated(path)
        self.assertEqual(problems, [])

    def test_messages_follow_the_language_asked_for(self):
        for value, expected in (("en", "Sync now"), ("en-US", "Sync now"), ("ko", "지금 동기화"), ("", "지금 동기화")):
            with mock.patch.dict(os.environ, {"CLAUDE_SYNC_LANG": value}):
                self.assertEqual(lang.t("지금 동기화", "Sync now"), expected)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(lang.t("지금 동기화", "Sync now"), "지금 동기화")

    def test_an_engine_error_comes_in_english_when_asked(self):
        home = os.path.join(self.root, "home")
        os.makedirs(home)
        page = "0123456789abcdef0123456789abcdef"
        with mock.patch.dict(os.environ, {"CLAUDE_SYNC_LANG": "en"}):
            _, _, out = call(home, "set-remote", "--backend", "notion", "--page", page)
        self.assertEqual(out, {"error": "Pass the secret key (token) on standard input"})
        with mock.patch.dict(os.environ, {"CLAUDE_SYNC_LANG": "ko"}):
            _, _, out = call(home, "set-remote", "--backend", "notion", "--page", page)
        self.assertEqual(out, {"error": "비밀 키(토큰)를 표준 입력으로 넘겨 주세요"})
