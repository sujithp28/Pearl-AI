"""
Tests for Pearl understanding languages universally, in both senses:

* Programming languages — symbol extraction for the languages the
  Language enum declared but had no parser for (C, C++, C#, Ruby, PHP,
  Swift, Kotlin). Those files were scanned and searchable as text, but
  contributed no functions or classes to the index.
* Human languages — a greeting in any language must short-circuit
  instead of reaching the planner. The shortcut previously matched only
  English, so "namaste" was planned as engineering work and could end
  the run in an error.
"""
from __future__ import annotations

import pytest

from src.agent.conversational import needs_no_tools
from src.repository.models import EXTENSION_TO_LANGUAGE, FileInfo, Language
from src.repository.parsers import ParserRegistry, SymbolKind


@pytest.fixture(scope="module")
def registry() -> ParserRegistry:
    return ParserRegistry.default()


def _parse(registry: ParserRegistry, tmp_path, name: str, source: str):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    stat = path.stat()
    info = FileInfo(
        path=path,
        relative_path=name,
        extension=path.suffix.lower(),
        language=EXTENSION_TO_LANGUAGE.get(path.suffix.lower(), Language.UNKNOWN),
        size=stat.st_size,
        modified_at=stat.st_mtime,
        content_hash="",
    )
    result = registry.parse(info)
    assert result is not None, f"no parser registered for {name}"
    return result


# ---------------------------------------------------------------------------
# Registry coverage
# ---------------------------------------------------------------------------


class TestRegistryCoverage:
    @pytest.mark.parametrize(
        "language",
        [
            Language.PYTHON, Language.JAVASCRIPT, Language.TYPESCRIPT,
            Language.JAVA, Language.GO, Language.RUST,
            Language.C, Language.CPP, Language.CSHARP,
            Language.RUBY, Language.PHP, Language.SWIFT, Language.KOTLIN,
        ],
    )
    def test_language_has_a_parser(self, registry, language):
        assert registry.supports(language), f"no parser for {language.value}"

    def test_every_mapped_code_extension_resolves(self, registry):
        """
        A file type Pearl claims to recognise but cannot parse yields no
        symbols at all, which is invisible — the file is simply absent
        from the index rather than obviously broken.
        """
        unparsed = {
            ext
            for ext, lang in EXTENSION_TO_LANGUAGE.items()
            if lang
            in {
                Language.C, Language.CPP, Language.CSHARP, Language.RUBY,
                Language.PHP, Language.SWIFT, Language.KOTLIN,
            }
            and not registry.supports(lang)
        }
        assert not unparsed, f"mapped but unparsed extensions: {unparsed}"


# ---------------------------------------------------------------------------
# C family
# ---------------------------------------------------------------------------


class TestCFamily:
    C_SOURCE = """\
#include <stdio.h>

struct Point { int x; int y; };

int add(int a, int b) {
    return a + b;
}

int main(void) {
    if (add(1, 2) > 0) {
        printf("hi");
    }
    while (1) { break; }
    return 0;
}
"""

    def test_c_extracts_struct_and_functions(self, registry, tmp_path):
        result = _parse(registry, tmp_path, "main.c", self.C_SOURCE)
        names = {s.name for s in result.symbols}
        assert {"Point", "add", "main"} <= names

    def test_c_does_not_index_control_flow_as_functions(self, registry, tmp_path):
        """`if (x) {` has the same shape as a function definition."""
        result = _parse(registry, tmp_path, "main.c", self.C_SOURCE)
        names = {s.name for s in result.symbols}
        assert "if" not in names
        assert "while" not in names

    def test_c_extracts_includes(self, registry, tmp_path):
        result = _parse(registry, tmp_path, "main.c", self.C_SOURCE)
        assert any("stdio.h" in imp for imp in result.imports)

    def test_cpp_attributes_methods_to_their_class(self, registry, tmp_path):
        result = _parse(
            registry,
            tmp_path,
            "engine.cpp",
            "class Engine {\n"
            "public:\n"
            "    Engine(int n) : size(n) {}\n"
            "    void start() { running = true; }\n"
            "};\n",
        )
        start = next(s for s in result.symbols if s.name == "start")
        assert start.parent == "Engine"
        assert start.kind is SymbolKind.METHOD

    def test_csharp_extracts_properties(self, registry, tmp_path):
        """A C# property is a real member with no C/C++ equivalent."""
        result = _parse(
            registry,
            tmp_path,
            "Service.cs",
            "using System;\n"
            "public class UserService {\n"
            "    public string Name { get; set; }\n"
            "    public List<string> GetUsers() { return users; }\n"
            "}\n",
        )
        names = {s.name for s in result.symbols}
        assert {"UserService", "Name", "GetUsers"} <= names

    def test_csharp_uses_using_not_include(self, registry, tmp_path):
        result = _parse(
            registry, tmp_path, "Service.cs", "using System.Linq;\nclass A {}\n"
        )
        assert any("System.Linq" in imp for imp in result.imports)


# ---------------------------------------------------------------------------
# Ruby — the one keyword-delimited language
# ---------------------------------------------------------------------------


class TestRuby:
    RUBY_SOURCE = """\
require 'json'

class User
  attr_accessor :name, :email

  def initialize(name)
    @name = name
  end

  def greet
    if @name
      "hi"
    end
  end
end
"""

    def test_extracts_class_and_methods(self, registry, tmp_path):
        result = _parse(registry, tmp_path, "user.rb", self.RUBY_SOURCE)
        names = {s.name for s in result.symbols}
        assert {"User", "initialize", "greet"} <= names

    def test_methods_belong_to_the_class(self, registry, tmp_path):
        result = _parse(registry, tmp_path, "user.rb", self.RUBY_SOURCE)
        greet = next(s for s in result.symbols if s.name == "greet")
        assert greet.parent == "User"

    def test_class_does_not_end_at_a_nested_if(self, registry, tmp_path):
        """
        Ruby closes blocks with `end`, so a naive scan stops at the first
        one — here the `if` inside greet — and truncates the class.
        """
        result = _parse(registry, tmp_path, "user.rb", self.RUBY_SOURCE)
        user = next(s for s in result.symbols if s.name == "User")
        greet = next(s for s in result.symbols if s.name == "greet")
        assert user.line_end >= greet.line_end, "class ended before its own method"

    def test_attr_accessor_becomes_searchable(self, registry, tmp_path):
        result = _parse(registry, tmp_path, "user.rb", self.RUBY_SOURCE)
        names = {s.name for s in result.symbols}
        assert {"name", "email"} <= names


# ---------------------------------------------------------------------------
# PHP / Swift / Kotlin
# ---------------------------------------------------------------------------


class TestOtherLanguages:
    def test_php_separates_methods_from_free_functions(self, registry, tmp_path):
        result = _parse(
            registry,
            tmp_path,
            "Controller.php",
            "<?php\n"
            "class UserController {\n"
            "    public function index($request) { return []; }\n"
            "}\n"
            "function helper($x) { return $x; }\n",
        )
        index = next(s for s in result.symbols if s.name == "index")
        helper = next(s for s in result.symbols if s.name == "helper")
        assert index.parent == "UserController"
        assert helper.parent is None
        assert helper.kind is SymbolKind.FUNCTION

    def test_swift_extracts_init_and_methods(self, registry, tmp_path):
        result = _parse(
            registry,
            tmp_path,
            "View.swift",
            "import SwiftUI\n"
            "struct ContentView {\n"
            "    let title: String\n"
            "    init(title: String) { self.title = title }\n"
            "    func render() -> String { return title }\n"
            "}\n",
        )
        names = {s.name for s in result.symbols}
        assert {"ContentView", "init", "render", "title"} <= names

    def test_kotlin_extracts_suspend_and_top_level_functions(
        self, registry, tmp_path
    ):
        result = _parse(
            registry,
            tmp_path,
            "Main.kt",
            "import kotlinx.coroutines.*\n"
            "data class Point(val x: Int, val y: Int)\n"
            "class Repo {\n"
            "    suspend fun fetch(id: String): String { return \"x\" }\n"
            "}\n"
            "fun main() { println(\"hi\") }\n",
        )
        names = {s.name for s in result.symbols}
        assert {"Point", "Repo", "fetch", "main"} <= names
        main = next(s for s in result.symbols if s.name == "main")
        assert main.parent is None, "top-level fun must not be a method"


# ---------------------------------------------------------------------------
# Graceful degradation — the contract every parser must honour
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    @pytest.mark.parametrize(
        "name",
        ["a.c", "a.cpp", "a.cs", "a.rb", "a.php", "a.swift", "a.kt"],
    )
    def test_malformed_source_never_raises(self, registry, tmp_path, name):
        result = _parse(registry, tmp_path, name, "{{{ ][ ((( def class fun end")
        assert result.errors == []

    @pytest.mark.parametrize(
        "name",
        ["e.c", "e.cpp", "e.cs", "e.rb", "e.php", "e.swift", "e.kt"],
    )
    def test_empty_file_never_raises(self, registry, tmp_path, name):
        result = _parse(registry, tmp_path, name, "")
        assert result.symbols == []


# ---------------------------------------------------------------------------
# Human languages
# ---------------------------------------------------------------------------


class TestHumanLanguages:
    @pytest.mark.parametrize(
        "greeting",
        [
            # Romanised South Asian
            "namaste", "namaskar", "vanakkam", "shukriya", "dhanyavaad",
            "kaise ho",
            # European
            "hola", "gracias", "bonjour", "merci", "hallo", "danke",
            "ciao", "olá", "obrigado", "privet", "spasibo",
            # Middle East / East Asia
            "salam", "shukran", "merhaba",
            "konnichiwa", "arigatou", "annyeonghaseyo", "ni hao", "xiexie",
            # Native scripts
            "नमस्ते", "धन्यवाद", "வணக்கம்", "நன்றி", "నమస్కారం",
            "こんにちは", "ありがとう", "안녕하세요", "你好", "谢谢",
            "مرحبا", "привет", "спасибо",
        ],
    )
    def test_greetings_in_any_language_need_no_tools(self, greeting):
        """
        An unmatched greeting reaches the planner, which invents work for
        it and can end the run in an error. Greeting Pearl in your own
        language should not produce a crash.
        """
        assert needs_no_tools(greeting) is True, f"{greeting!r} not recognised"

    @pytest.mark.parametrize(
        "prompt",
        [
            "namaste, delete the build folder",
            "hola, fix the login bug",
            "नमस्ते, मेरी फाइल पढ़ो",
            "merci, now add a test",
        ],
    )
    def test_a_greeting_carrying_a_request_still_reaches_the_planner(
        self, prompt
    ):
        """Politeness in front of real work must not swallow the work."""
        assert needs_no_tools(prompt) is False

    def test_system_prompt_asks_for_the_users_language(self):
        from src.prompts.system import build_chat_system_prompt

        # The prompt is hard-wrapped prose, so collapse whitespace before
        # matching — otherwise this asserts on line-break placement.
        prompt = " ".join(build_chat_system_prompt("/tmp/ws").lower().split())

        assert "same language" in prompt
        # Code must survive untranslated, or the reply is useless.
        assert "must not be translated" in prompt


# ---------------------------------------------------------------------------
# Every parser, one shape
#
# The JS/TS parser shipped emitting no SymbolKind.METHOD at all: its method
# regex was written and never wired. Nothing caught it because the tests
# asserted per-language that a class and a function were found, and no
# language's test asked whether methods were.
#
# This asks the same three questions of all thirteen, so a parser that
# silently stops finding one kind of symbol fails here rather than
# degrading Pearl's index in the dark.
# ---------------------------------------------------------------------------


# (filename, source, class, method, free function or None where the
# language has no meaningful top-level function)
_LANGUAGE_SAMPLES: list[tuple[str, str, str, str, str | None]] = [
    (
        "s.py",
        "class UserService:\n"
        "    def find_user(self, id):\n"
        "        return id\n"
        "\n"
        "def standalone(x):\n"
        "    return x + 1\n",
        "UserService", "find_user", "standalone",
    ),
    (
        "s.js",
        "class UserService {\n"
        "  findUser(id) { return id; }\n"
        "}\n"
        "function standalone(x) { return x + 1; }\n",
        "UserService", "findUser", "standalone",
    ),
    (
        "s.ts",
        "export class UserService {\n"
        "  public findUser(id: string): string { return id; }\n"
        "}\n"
        "export function standalone(x: number): number { return x + 1; }\n",
        "UserService", "findUser", "standalone",
    ),
    (
        "S.java",
        "public class UserService {\n"
        "    public String findUser(String id) { return id; }\n"
        "}\n",
        "UserService", "findUser", None,
    ),
    (
        "s.go",
        "package main\n"
        "type UserService struct { db int }\n"
        "func (s *UserService) FindUser(id string) error { return nil }\n"
        "func Standalone(x int) int { return x + 1 }\n",
        "UserService", "FindUser", "Standalone",
    ),
    (
        "s.rs",
        "pub struct UserService { db: u32 }\n"
        "impl UserService {\n"
        "    pub fn find_user(&self, id: u32) -> u32 { id }\n"
        "}\n"
        "pub fn standalone(x: i32) -> i32 { x + 1 }\n",
        "UserService", "find_user", "standalone",
    ),
    (
        "s.cpp",
        "class UserService {\n"
        "public:\n"
        "    int findUser(int id) { return id; }\n"
        "};\n"
        "int standalone(int x) { return x + 1; }\n",
        "UserService", "findUser", "standalone",
    ),
    (
        "s.cs",
        "public class UserService {\n"
        "    public string FindUser(string id) { return id; }\n"
        "}\n",
        "UserService", "FindUser", None,
    ),
    (
        "s.rb",
        "class UserService\n"
        "  def find_user(id)\n"
        "    id\n"
        "  end\n"
        "end\n",
        "UserService", "find_user", None,
    ),
    (
        "s.php",
        "<?php\n"
        "class UserService {\n"
        "    public function findUser($id) { return $id; }\n"
        "}\n"
        "function standalone($x) { return $x + 1; }\n",
        "UserService", "findUser", "standalone",
    ),
    (
        "s.swift",
        "class UserService {\n"
        "    func findUser(id: String) -> String { return id }\n"
        "}\n"
        "func standalone(x: Int) -> Int { return x + 1 }\n",
        "UserService", "findUser", "standalone",
    ),
    (
        "s.kt",
        "class UserService {\n"
        "    fun findUser(id: String): String = id\n"
        "}\n"
        "fun standalone(x: Int): Int = x + 1\n",
        "UserService", "findUser", "standalone",
    ),
]

_SAMPLE_IDS = [name for name, *_ in _LANGUAGE_SAMPLES]


class TestEveryParserFindsTheSameShape:
    @pytest.mark.parametrize(
        "sample", _LANGUAGE_SAMPLES, ids=_SAMPLE_IDS
    )
    def test_class_is_found(self, registry, tmp_path, sample):
        name, source, class_name, _, _ = sample
        result = _parse(registry, tmp_path, name, source)

        assert class_name in {s.name for s in result.symbols}

    @pytest.mark.parametrize(
        "sample", _LANGUAGE_SAMPLES, ids=_SAMPLE_IDS
    )
    def test_method_is_found_and_marked_as_a_method(
        self, registry, tmp_path, sample
    ):
        """
        The exact check the JS/TS parser would have failed from the day
        it shipped.
        """
        name, source, _, method_name, _ = sample
        result = _parse(registry, tmp_path, name, source)

        methods = {s.name for s in result.symbols if s.kind is SymbolKind.METHOD}

        assert method_name in methods, (
            f"{name}: expected {method_name!r} as a METHOD, got "
            f"{[(s.name, s.kind.value) for s in result.symbols]}"
        )

    @pytest.mark.parametrize(
        "sample", _LANGUAGE_SAMPLES, ids=_SAMPLE_IDS
    )
    def test_free_function_is_found(self, registry, tmp_path, sample):
        name, source, _, _, function_name = sample

        if function_name is None:
            pytest.skip(f"{name}: no meaningful top-level function")

        result = _parse(registry, tmp_path, name, source)

        assert function_name in {s.name for s in result.symbols}

    @pytest.mark.parametrize(
        "sample", _LANGUAGE_SAMPLES, ids=_SAMPLE_IDS
    )
    def test_parsing_reports_no_errors(self, registry, tmp_path, sample):
        name, source, *_ = sample
        result = _parse(registry, tmp_path, name, source)

        assert not result.errors, f"{name}: {result.errors}"
