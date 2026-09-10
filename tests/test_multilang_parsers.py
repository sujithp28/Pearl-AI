"""
Tests for JS/TS, Go, Rust, and Java regex-based parsers.
Verifies graceful degradation: parsers never raise, always return ParseResult.
"""
from __future__ import annotations

from pathlib import Path

from src.repository.models import Language
from src.repository.parsers import ParseResult, ParserRegistry, SymbolKind


def _make_file_info(tmp_path: Path, name: str, content: str):
    """Write content to a temp file and return a FileInfo for it."""
    from src.repository.models import EXTENSION_TO_LANGUAGE, FileInfo
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    ext = Path(name).suffix.lower()
    lang = EXTENSION_TO_LANGUAGE.get(ext, Language.PYTHON)
    stat = p.stat()
    return FileInfo(
        path=p,
        relative_path=name,
        extension=ext,
        language=lang,
        size=stat.st_size,
        modified_at=stat.st_mtime,
        content_hash="",
    )


# ---------------------------------------------------------------------------
# Registry registration
# ---------------------------------------------------------------------------

class TestParserRegistryDefault:
    def test_all_languages_registered(self):
        reg = ParserRegistry.default()
        for lang in (Language.PYTHON, Language.JAVASCRIPT, Language.TYPESCRIPT,
                     Language.GO, Language.RUST, Language.JAVA):
            assert reg.supports(lang), f"Missing parser for {lang}"

    def test_registry_has_no_duplicate_registrations(self):
        """
        This asserted an exact count of six, which broke the moment a
        language was added — a count is not what the test cares about.
        What matters is that every registered parser claims a distinct
        language, since registering two for the same one silently
        replaces the first.
        """
        reg = ParserRegistry.default()

        assert len(reg) == len(reg.supported_languages())
        assert len(reg) >= 6, "built-in parsers went missing"


# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------

class TestJavaScriptParser:
    JS_SOURCE = """\
import { foo } from './foo';
const bar = require('bar');

export class MyClass {
  constructor() {}
  greet(name) { return 'hi'; }
}

export async function doStuff() {}

export const arrowFn = async () => {};
"""

    def test_js_parses_class(self, tmp_path):
        fi = _make_file_info(tmp_path, "app.js", self.JS_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert result is not None
        names = [s.name for s in result.symbols]
        assert "MyClass" in names

    def test_js_parses_function(self, tmp_path):
        fi = _make_file_info(tmp_path, "app.js", self.JS_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        names = [s.name for s in result.symbols]
        assert "doStuff" in names

    def test_js_parses_arrow_function(self, tmp_path):
        fi = _make_file_info(tmp_path, "app.js", self.JS_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        names = [s.name for s in result.symbols]
        assert "arrowFn" in names

    def test_js_parses_imports(self, tmp_path):
        fi = _make_file_info(tmp_path, "app.js", self.JS_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert len(result.imports) >= 1

    def test_js_no_errors_on_valid_source(self, tmp_path):
        fi = _make_file_info(tmp_path, "app.js", self.JS_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert result.errors == []

    def test_js_graceful_on_empty_file(self, tmp_path):
        fi = _make_file_info(tmp_path, "empty.js", "")
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert isinstance(result, ParseResult)
        assert result.errors == []


# ---------------------------------------------------------------------------
# TypeScript
# ---------------------------------------------------------------------------

class TestTypeScriptParser:
    TS_SOURCE = """\
import { Component } from '@angular/core';

export interface IService {
  getData(): string[];
}

export abstract class BaseService implements IService {
  abstract getData(): string[];

  protected helper(): void {}
}

export const factory = (): IService => ({ getData: () => [] });
"""

    def test_ts_parses_class(self, tmp_path):
        fi = _make_file_info(tmp_path, "svc.ts", self.TS_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert result is not None
        names = [s.name for s in result.symbols]
        assert "BaseService" in names

    def test_ts_parses_const_arrow(self, tmp_path):
        fi = _make_file_info(tmp_path, "svc.ts", self.TS_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        names = [s.name for s in result.symbols]
        assert "factory" in names

    def test_tsx_extension_handled(self, tmp_path):
        fi = _make_file_info(tmp_path, "comp.tsx", "export const X = () => <div/>;")
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert isinstance(result, ParseResult)


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

class TestGoParser:
    GO_SOURCE = """\
package main

import (
    "fmt"
    "os"
)

type Server struct {
    port int
}

func NewServer(port int) *Server {
    return &Server{port: port}
}

func (s *Server) Start() error {
    fmt.Println(s.port)
    return nil
}

func main() {
    os.Exit(0)
}
"""

    def test_go_parses_struct(self, tmp_path):
        fi = _make_file_info(tmp_path, "main.go", self.GO_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert result is not None
        names = [s.name for s in result.symbols]
        assert "Server" in names

    def test_go_parses_function(self, tmp_path):
        fi = _make_file_info(tmp_path, "main.go", self.GO_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        names = [s.name for s in result.symbols]
        assert "NewServer" in names
        assert "main" in names

    def test_go_parses_method_with_receiver(self, tmp_path):
        fi = _make_file_info(tmp_path, "main.go", self.GO_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        methods = [s for s in result.symbols if s.kind == SymbolKind.METHOD]
        assert any(s.name == "Start" for s in methods)

    def test_go_method_has_parent(self, tmp_path):
        fi = _make_file_info(tmp_path, "main.go", self.GO_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        start = next(s for s in result.symbols if s.name == "Start")
        assert start.parent == "Server"

    def test_go_parses_imports(self, tmp_path):
        fi = _make_file_info(tmp_path, "main.go", self.GO_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert any("fmt" in imp for imp in result.imports)

    def test_go_graceful_on_empty_file(self, tmp_path):
        fi = _make_file_info(tmp_path, "empty.go", "")
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert isinstance(result, ParseResult)


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

class TestRustParser:
    RUST_SOURCE = """\
use std::io::{self, Read};
use std::collections::HashMap;

pub struct Config {
    pub port: u16,
}

pub enum Status {
    Ok,
    Err(String),
}

impl Config {
    pub fn new(port: u16) -> Self {
        Config { port }
    }

    pub async fn load() -> Result<Self, io::Error> {
        Ok(Config { port: 8080 })
    }
}

pub fn run(cfg: Config) -> Status {
    Status::Ok
}
"""

    def test_rust_parses_struct(self, tmp_path):
        fi = _make_file_info(tmp_path, "lib.rs", self.RUST_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert result is not None
        names = [s.name for s in result.symbols]
        assert "Config" in names

    def test_rust_parses_free_function(self, tmp_path):
        fi = _make_file_info(tmp_path, "lib.rs", self.RUST_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        names = [s.name for s in result.symbols]
        assert "run" in names

    def test_rust_parses_impl_methods(self, tmp_path):
        fi = _make_file_info(tmp_path, "lib.rs", self.RUST_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        names = [s.name for s in result.symbols]
        assert "new" in names

    def test_rust_async_fn_detected(self, tmp_path):
        fi = _make_file_info(tmp_path, "lib.rs", self.RUST_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        load = next((s for s in result.symbols if s.name == "load"), None)
        assert load is not None
        assert load.is_async

    def test_rust_parses_use_imports(self, tmp_path):
        fi = _make_file_info(tmp_path, "lib.rs", self.RUST_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert len(result.imports) >= 2

    def test_rust_graceful_on_syntax_noise(self, tmp_path):
        fi = _make_file_info(tmp_path, "weird.rs", "fn { { { broken")
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert isinstance(result, ParseResult)


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

class TestJavaParser:
    JAVA_SOURCE = """\
package com.example;

import java.util.List;
import java.util.ArrayList;

public class UserService {
    private final List<String> users = new ArrayList<>();

    public UserService() {}

    public List<String> getUsers() {
        return users;
    }

    public void addUser(String name) {
        users.add(name);
    }

    private static boolean validate(String name) {
        return name != null && !name.isEmpty();
    }
}

interface Serializable {
    String serialize();
}
"""

    def test_java_parses_class(self, tmp_path):
        fi = _make_file_info(tmp_path, "UserService.java", self.JAVA_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert result is not None
        names = [s.name for s in result.symbols]
        assert "UserService" in names

    def test_java_parses_methods(self, tmp_path):
        fi = _make_file_info(tmp_path, "UserService.java", self.JAVA_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        names = [s.name for s in result.symbols]
        assert "getUsers" in names
        assert "addUser" in names

    def test_java_parses_imports(self, tmp_path):
        fi = _make_file_info(tmp_path, "UserService.java", self.JAVA_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert any("java.util.List" in imp for imp in result.imports)

    def test_java_no_errors_on_valid_source(self, tmp_path):
        fi = _make_file_info(tmp_path, "UserService.java", self.JAVA_SOURCE)
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert result.errors == []

    def test_java_graceful_on_empty_file(self, tmp_path):
        fi = _make_file_info(tmp_path, "Empty.java", "")
        reg = ParserRegistry.default()
        result = reg.parse(fi)
        assert isinstance(result, ParseResult)


# ---------------------------------------------------------------------------
# Class methods
#
# JS/TS were the only two of the thirteen languages whose parser emitted no
# SymbolKind.METHOD at all: _METHOD_RE was defined but never used, so every
# method in every class was invisible to the index.
# ---------------------------------------------------------------------------


class TestJsTsClassMethods:
    SOURCE = """\
class UserService {
  constructor(db) { this.db = db; }
  async findUser(id) { return this.db.get(id); }
  deleteUser(id) {
    return this.db.remove(id);
  }
}

function standalone(x) { return x + 1; }
"""

    def _symbols(self, tmp_path, name="svc.js", source=None):
        fi = _make_file_info(tmp_path, name, source or self.SOURCE)
        return ParserRegistry.default().parse(fi).symbols

    def test_methods_are_found(self, tmp_path):
        names = {s.name for s in self._symbols(tmp_path)}

        assert {"findUser", "deleteUser"} <= names

    def test_methods_are_kind_method_not_function(self, tmp_path):
        kinds = {s.name: s.kind for s in self._symbols(tmp_path)}

        assert kinds["findUser"] is SymbolKind.METHOD
        assert kinds["standalone"] is SymbolKind.FUNCTION

    def test_methods_are_qualified_by_their_class(self, tmp_path):
        qnames = {s.name: s.qualified_name for s in self._symbols(tmp_path)}

        assert qnames["findUser"] == "UserService.findUser"
        assert qnames["standalone"] == "standalone"

    def test_constructor_is_indexed(self, tmp_path):
        qnames = {s.name: s.qualified_name for s in self._symbols(tmp_path)}

        assert qnames.get("constructor") == "UserService.constructor"

    def test_async_methods_are_marked_async(self, tmp_path):
        by_name = {s.name: s for s in self._symbols(tmp_path)}

        assert by_name["findUser"].is_async is True
        assert by_name["deleteUser"].is_async is False

    def test_statements_inside_a_method_are_not_mistaken_for_methods(self, tmp_path):
        """
        _METHOD_RE is loose — `name(` matches a bare call too. A call
        statement in a method body must not be indexed as a method.
        """
        source = """\
class Runner {
  start() {
    doSomething(1);
    this.helper(2);
  }
}
"""
        names = {s.name for s in self._symbols(tmp_path, source=source)}

        assert "start" in names
        assert "doSomething" not in names

    def test_control_flow_is_not_indexed(self, tmp_path):
        source = """\
class Guard {
  check(x) {
    if (x) {
      return 1;
    }
    for (let i = 0; i < 3; i++) {}
  }
}
"""
        names = {s.name for s in self._symbols(tmp_path, source=source)}

        assert "check" in names
        assert not ({"if", "for", "while", "switch"} & names)

    def test_typescript_methods_too(self, tmp_path):
        source = """\
export class Repo {
  private async load(id: string): Promise<void> {
    return;
  }
}
"""
        qnames = {s.name: s.qualified_name for s in self._symbols(
            tmp_path, name="repo.ts", source=source
        )}

        assert qnames.get("load") == "Repo.load"

    def test_top_level_function_is_not_qualified(self, tmp_path):
        """A function outside any class must keep its bare name."""
        by_name = {s.name: s for s in self._symbols(tmp_path)}

        assert by_name["standalone"].kind is SymbolKind.FUNCTION
        assert by_name["standalone"].qualified_name == "standalone"

    def test_methods_beyond_the_block_scan_window_are_still_found(self, tmp_path):
        """
        Class spans were estimated with _estimate_end's 200-line cap, so in
        a class longer than that every method past the window fell outside
        its own class and was dropped. Real example: MCPConnection in the
        VS Code extension starts at line 40 and yielded 8 of its 15
        methods — the 7 missing ones all sat past line 240.
        """
        filler = "\n".join(f"    // padding line {i}" for i in range(300))
        source = (
            "class Big {\n"
            "  early() { return 1; }\n"
            f"{filler}\n"
            "  late() { return 2; }\n"
            "}\n"
        )

        symbols = self._symbols(tmp_path, source=source)
        qnames = {s.name: s.qualified_name for s in symbols}

        assert qnames.get("early") == "Big.early"
        assert qnames.get("late") == "Big.late", (
            "a method past the 200-line scan window was dropped"
        )

    def test_method_with_a_wrapping_signature_is_found(self, tmp_path):
        """
        About 14% of method declarations in the project's own TypeScript
        wrap their parameter list. Missing them loses one method in seven.
        """
        source = """\
class Client {
  sendRequest(
    method: string,
    params: Record<string, unknown>,
  ): Promise<void> {
    return this.send(method, params);
  }
}
"""
        qnames = {s.name: s.qualified_name for s in self._symbols(
            tmp_path, name="c.ts", source=source
        )}

        assert qnames.get("sendRequest") == "Client.sendRequest"

    def test_a_wrapping_call_is_still_not_a_method(self, tmp_path):
        """Allowing newlines must not let multi-line calls through."""
        source = """\
class Runner {
  start() {
    doSomething(
      alpha,
      beta,
    );
    withCallback(
      () => { return 1; },
    );
  }
}
"""
        names = {s.name for s in self._symbols(tmp_path, source=source)}

        assert "start" in names
        assert "doSomething" not in names
        assert "withCallback" not in names
