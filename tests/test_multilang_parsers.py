"""
Tests for JS/TS, Go, Rust, and Java regex-based parsers.
Verifies graceful degradation: parsers never raise, always return ParseResult.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.repository.parsers import ParseResult, ParserRegistry, SymbolKind
from src.repository.models import Language


def _make_file_info(tmp_path: Path, name: str, content: str):
    """Write content to a temp file and return a FileInfo for it."""
    from src.repository.models import FileInfo, EXTENSION_TO_LANGUAGE
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

    def test_registry_has_six_parsers(self):
        reg = ParserRegistry.default()
        assert len(reg) == 6


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
