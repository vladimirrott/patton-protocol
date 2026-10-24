#!/usr/bin/env python3
"""Patton gate, the Claude Code plugin: asks before an agent changes a file that existed at session start.

This is the product gate. It keeps the Bash classifier and the decision rules of the gate the 2026-10-21 study
froze (gate/patton_gate.py, which never changes), and adapts the parts that met daily work:

- A command the classifier cannot read (pytest, make, npm test) asks the person instead of being refused, and an
  answer never counts toward the denial limit. PATTON_GATE_OPAQUE=deny restores the studied refusal.
- When nothing protected changes, the gate gives no decision, so Claude Code's own permission flow applies.
- The session manifest lists paths without hashing them, never opens a socket or a FIFO, skips dependency and
  build directories, and keeps the first manifest of a session across a resume.
- With no configuration the project is CLAUDE_PROJECT_DIR (or the hook's cwd), and the state of each session lives
  under CLAUDE_PLUGIN_DATA (or XDG_STATE_HOME/patton-gate).
- An error inside the gate denies the call with its reason (fail closed).

With PATTON_GATE_APPROVER set the gate runs in study mode and answers as the study measured. Standard library only.
The classifier never executes anything; it is a reader for an honest agent's mistakes, not a sandbox against an
adversary. `python3 patton_gate.py log` prints what the gate did in the latest session.
"""

from __future__ import annotations

import contextlib
import glob as _glob
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass

try:
    import fcntl
except ImportError:  # pragma: no cover - not on Windows
    fcntl = None

READ_ONLY, MUTATES, OPAQUE = "read_only", "mutates", "opaque"
MAX_COMMAND = 100_000
MAX_WORD = 4096
MAX_SCRIPT = 2000


@dataclass(frozen=True)
class Classification:
    kind: str
    paths: tuple[str, ...]
    why: str = ""
    outside: tuple[str, ...] = ()  # absolute paths outside the project the command changes


class _Opaque(Exception):
    pass


# ---- lexer ----------------------------------------------------------------------------------------------

@dataclass
class Word:
    text: str
    pattern: str
    glob: bool
    expands: bool
    brace: bool
    quoted: bool


_PUNCT = set(";&|()<>")
_REDIRECTS = ("&>>", "&>", ">>", ">|", ">&", "<<<", "<<-", "<<", "<&", "<>", ">", "<")
_VAR_START = set("_{@*#?$!-")


def _word(text: str, i: int) -> tuple[Word, int]:
    n = len(text)
    buf: list[str] = []
    pat: list[str] = []
    unq: list[str] = []
    glob = expands = quoted = False
    start = i
    while i < n:
        c = text[i]
        if c in " \t\n" or c in _PUNCT:
            break
        if c == "\\":
            if i + 1 >= n:
                buf.append("\\"); pat.append("\\\\"); unq.append("\0"); i += 1
                continue
            if text[i + 1] == "\n":
                i += 2
                continue
            buf.append(text[i + 1]); pat.append(_glob.escape(text[i + 1])); unq.append("\0"); quoted = True
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise _Opaque("unterminated quote")
            literal = text[i + 1:j]
            buf.append(literal); pat.append(_glob.escape(literal)); unq.append("\0"); quoted = True
            i = j + 1
            continue
        if c == '"':
            i += 1
            chunk: list[str] = []
            while True:
                if i >= n:
                    raise _Opaque("unterminated quote")
                d = text[i]
                if d == '"':
                    i += 1
                    break
                if d == "\\" and i + 1 < n and text[i + 1] in '$`"\\\n':
                    if text[i + 1] != "\n":
                        chunk.append(text[i + 1])
                    i += 2
                    continue
                if d == "`":
                    raise _Opaque("command substitution")
                if d == "$":
                    nxt = text[i + 1:i + 2]
                    if nxt == "(":
                        raise _Opaque("command substitution")
                    if nxt and (nxt.isalnum() or nxt in _VAR_START):
                        expands = True
                chunk.append(d)
                i += 1
            joined = "".join(chunk)
            buf.append(joined); pat.append(_glob.escape(joined)); unq.append("\0"); quoted = True
            continue
        if c == "`":
            raise _Opaque("command substitution")
        if c == "$":
            nxt = text[i + 1:i + 2]
            if nxt == "(":
                raise _Opaque("command substitution")
            if nxt in ("'", '"'):
                raise _Opaque("ANSI-C or locale quoting")
            if nxt and (nxt.isalnum() or nxt in _VAR_START):
                expands = True
        if c in "*?[":
            glob = True
        buf.append(c); pat.append(c); unq.append(c)
        i += 1
        if i - start > MAX_WORD:
            raise _Opaque("word too long to read")
    raw = "".join(buf)
    brace = bool(re.search(r"\{[^{}]*(?:,|\.\.)[^{}]*\}", "".join(unq)))
    if raw.startswith("~") and unq and unq[0] == "~":
        raw = os.path.expanduser(raw)
        pat = [_glob.escape(raw)]
    return Word(raw, "".join(pat), glob, expands, brace, quoted), i


def _lex(text: str) -> list[tuple]:
    """Items: ('word', Word), ('op', str), ('redir', op, fd, Word | None)."""
    items: list[tuple] = []
    heredocs: list[tuple[str, bool, bool]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t":
            i += 1
            continue
        if c == "\\" and text[i + 1:i + 2] == "\n":
            i += 2
            continue
        if c == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "\n":
            items.append(("op", "\n"))
            i += 1
            for delim, strip, quoted in heredocs:
                body: list[str] = []
                while True:
                    if i >= n:
                        raise _Opaque("unterminated here-document")
                    j = text.find("\n", i)
                    line = text[i:j] if j >= 0 else text[i:]
                    i = j + 1 if j >= 0 else n
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
                    body.append(line)
                if not quoted and any("$(" in line or "`" in line for line in body):
                    raise _Opaque("command substitution in a here-document")
            heredocs.clear()
            continue
        two = text[i:i + 2]
        if two in ("&&", "||", ";;"):
            items.append(("op", two))
            i += 2
            continue
        if two == "|&":
            items.append(("op", "|"))
            i += 2
            continue
        redirect = next((r for r in _REDIRECTS if text.startswith(r, i)), None)
        if redirect is not None:
            i = _redirect(text, i, redirect, "", items, heredocs)
            continue
        if c in ";&|()":
            items.append(("op", c))
            i += 1
            continue
        word, j = _word(text, i)
        if j < n and text[j] in "<>" and word.text.isdigit() and not word.quoted and not word.expands:
            redirect = next(r for r in _REDIRECTS if text.startswith(r, j))
            i = _redirect(text, j, redirect, word.text, items, heredocs)
            continue
        items.append(("word", word))
        i = j
    if heredocs:
        raise _Opaque("unterminated here-document")
    return items


def _redirect(text: str, i: int, op: str, fd: str, items: list[tuple], heredocs: list) -> int:
    i += len(op)
    n = len(text)
    while i < n and text[i] in " \t":
        i += 1
    if i >= n or text[i] in "\n;&|()<>":
        raise _Opaque("process substitution or a redirection without a target")
    target, i = _word(text, i)
    if op in ("<<", "<<-"):
        heredocs.append((target.text, op == "<<-", target.quoted))
        items.append(("redir", op, fd, None))
    else:
        items.append(("redir", op, fd, target))
    return i


# ---- verbs ---------------------------------------------------------------------------------------------

READ_ONLY_VERBS = frozenset(
    "ls cat head tail less more wc stat file du df tree pwd echo printf test [ [[ true false : sleep date whoami id "
    "uname hostname which type basename dirname realpath readlink grep egrep fgrep rg diff cmp sha1sum sha224sum "
    "sha256sum sha384sum sha512sum md5sum b2sum cksum cut tr tac rev nl fold column paste join comm seq expand "
    "unexpand jq export unset set cd sort uniq sed find git".split())
RM_VERBS = frozenset({"rm", "unlink", "shred", "rmdir"})
SAFE_BIN_DIRS = frozenset({"/bin", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin"})
KEYWORDS = frozenset({"if", "then", "elif", "else", "fi", "do", "done", "while", "until", "!", "time"})
COMPOUND_OPEN = frozenset({"if", "while", "until"})
COMPOUND_CLOSE = frozenset({"fi", "done"})
COMPOUND_OPAQUE = frozenset({"for", "case", "select", "function", "coproc"})
GIT_READ_ONLY = frozenset(
    "status log diff show ls-files rev-parse add commit blame grep describe branch remote shortlog ls-tree "
    "cat-file".split())
# Commands `find -exec` may run without making the find opaque: each only reads the files it is given.
FIND_EXEC_READ_ONLY = frozenset(
    "du ls stat wc cat head tail file grep md5sum sha1sum sha256sum sha512sum cksum".split())
# xargs options the gate reads. Anything else (-I, -a, -p ...) makes the command opaque.
XARGS_FLAGS = frozenset({"-0", "--null", "-r", "--no-run-if-empty", "-t", "--verbose"})
XARGS_VALUE_FLAGS = frozenset({"-n", "-L", "-P", "-s", "-d", "--max-args", "--max-procs", "--delimiter"})
XARGS_JOINED = re.compile(r"^-[nLPs][0-9]+$")
ALWAYS_SUCCEEDS = frozenset({"cd", "true", ":", "echo", "pwd"})
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _args(words: list[Word], short_arg: str = "", long_arg: tuple[str, ...] = ()):
    """Split words into flags and operands. Returns (flags, operands, option_values)."""
    flags: list[str] = []
    operands: list[Word] = []
    values: dict[str, str] = {}
    done = False
    i = 0
    while i < len(words):
        w = words[i]
        t = w.text
        i += 1
        if done or not t.startswith("-") or t == "-":
            operands.append(w)
            continue
        if t == "--":
            done = True
            continue
        flags.append(t)
        if t.startswith("--"):
            name, eq, val = t.partition("=")
            if name in long_arg:
                if eq:
                    values[name] = val
                elif i < len(words):
                    values[name] = words[i].text
                    i += 1
            continue
        for k, ch in enumerate(t[1:], start=1):
            if ch in short_arg:
                rest = t[k + 1:]
                if rest:
                    values["-" + ch] = rest
                elif i < len(words):
                    values["-" + ch] = words[i].text
                    i += 1
                break
    return flags, operands, values


def _cluster_has(flags: list[str], letters: str, long_name: str = "") -> bool:
    return any((f.startswith("--") and f == long_name) or (not f.startswith("--") and any(ch in letters for ch in f[1:]))
               for f in flags)


class _Walker:
    def __init__(self, root: str, cwd: str) -> None:
        self.root = root
        self.cwds: set[str] = {cwd}
        self.paths: set[str] = set()
        self.outside: set[str] = set()
        self.mutates = False
        self.reach_certain = True
        self.cond_depth = 0

    # -- path helpers --
    def _real(self, raw: str, cwd: str, follow: bool) -> str:
        p = raw if os.path.isabs(raw) else os.path.join(cwd, raw)
        had_slash = len(p) > 1 and p.endswith("/")
        p = p.rstrip("/") or "/"
        if follow or had_slash or os.path.basename(p) in (".", ".."):
            return os.path.realpath(p)
        return os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p))

    def _inside(self, real: str) -> str | None:
        rel = os.path.relpath(real, self.root)
        if rel == "." or rel == ".." or rel.startswith(".." + os.sep):
            return None if rel != "." else "."
        return rel.replace(os.sep, "/")

    def _add(self, real: str) -> None:
        rel = self._inside(real)
        if rel is not None:
            self.paths.add(rel)
        else:
            self.outside.add(real)

    def _files_under(self, real: str) -> list[str]:
        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(real, followlinks=False):
            found += [os.path.join(dirpath, f) for f in filenames]
            found += [os.path.join(dirpath, d) for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
        return found or [real]

    def _targets(self, word: Word, cwd: str) -> list[str]:
        """Absolute paths an operand names, globs expanded. A glob that matches nothing names nothing."""
        if word.expands or word.brace:
            raise _Opaque("a path the shell expands: " + word.text[:40])
        if word.glob:
            base = word.pattern if os.path.isabs(word.pattern) else os.path.join(cwd, word.pattern)
            return sorted(_glob.glob(base))
        return [word.text if os.path.isabs(word.text) else os.path.join(cwd, word.text)]

    def _each(self, words, follow: bool, expand: bool = False, create_ok: bool = True) -> None:
        for cwd in self.cwds:
            for word in words:
                if word.glob and not create_ok:
                    raise _Opaque("a pattern in a path that is created")
                for target in self._targets(word, cwd):
                    real = self._real(target, cwd, follow)
                    if expand and os.path.isdir(real) and not os.path.islink(real):
                        # Walk only what can hold project files: `rm -rf /` used to walk the whole file system.
                        if self._inside(real) is not None:
                            for f in self._files_under(real):
                                self._add(f)
                        else:
                            self.outside.add(real)
                            if self.root.startswith(real.rstrip(os.sep) + os.sep):
                                for f in self._files_under(self.root):
                                    self._add(f)
                    else:
                        self._add(real)

    # -- redirects --
    def _redirects(self, redirs: list[tuple]) -> None:
        for op, _fd, target in redirs:
            writes = op in (">", ">>", ">|", "&>", "&>>", "<>")
            if op == ">&":
                writes = not (target.text.isdigit() or target.text == "-")
            if not writes or target is None:
                continue
            if target.expands or target.brace or target.glob:
                raise _Opaque("a redirection target the shell expands")
            if target.text.startswith("/dev/"):
                continue
            self.mutates = True
            for cwd in self.cwds:
                p = target.text if os.path.isabs(target.text) else os.path.join(cwd, target.text)
                self._add(self._real(p, cwd, True))

    # -- segments --
    def segment(self, words: list[Word], redirs: list[tuple], before: str, after: str) -> None:
        piped = before == "|" or after == "|"
        background = after == "&"
        while words and not words[0].quoted and words[0].text in KEYWORDS | COMPOUND_OPAQUE:
            kw = words[0].text
            if kw in COMPOUND_OPAQUE:
                raise _Opaque(f"{kw} compound command")
            if kw in COMPOUND_OPEN:
                self.cond_depth += 1
            elif kw in COMPOUND_CLOSE:
                self.cond_depth = max(0, self.cond_depth - 1)
            words = words[1:]
        self._redirects(redirs)
        while words and ASSIGNMENT.match(words[0].text) and not words[0].quoted:
            words = words[1:]
        if not words:
            return
        words, name = self._verb(words)
        if name is None:
            return
        self._dispatch(name, words, piped or background)

    def _verb(self, words: list[Word]) -> tuple[list[Word], str | None]:
        for _ in range(8):
            head = words[0]
            text = head.text
            if text.startswith("$") or head.expands or head.brace or (head.glob and text not in ("[", "[[")):
                raise _Opaque("a command name the shell expands")
            if "/" in text:
                if os.path.dirname(text) not in SAFE_BIN_DIRS:
                    raise _Opaque("a program run by path: " + text[:40])
                text = os.path.basename(text)
            if text in ("env", "sudo", "nohup", "command", "nice"):
                rest = words[1:]
                if text == "command" and rest and rest[0].text in ("-v", "-V"):
                    return rest, None
                skipped = 0
                for w in rest:
                    if text == "env" and (w.text.startswith("-") or ASSIGNMENT.match(w.text)):
                        skipped += 1
                    elif text == "sudo" and w.text in ("-n", "-E", "-H", "-S"):
                        skipped += 1
                    elif text == "command" and w.text == "-p":
                        skipped += 1
                    else:
                        break
                if text == "sudo" and rest and rest[skipped:skipped + 1] and rest[skipped].text.startswith("-"):
                    raise _Opaque("sudo with options")
                if text == "nice":
                    raise _Opaque("nice")
                words = rest[skipped:]
                if not words:
                    return words, None
                continue
            if text == "time":
                words = words[1:]
                if not words:
                    return words, None
                continue
            return words[1:], text
        raise _Opaque("too many wrappers")

    def _dispatch(self, name: str, words: list[Word], isolated: bool) -> None:
        if name in RM_VERBS:
            self._rm(name, words)
        elif name in ("mv", "cp", "install", "ln"):
            self._move(name, words)
        elif name in ("truncate", "tee", "touch", "chmod", "chown", "chgrp", "dd", "mkdir"):
            self._write(name, words)
        elif name == "sed":
            self._sed(words)
        elif name == "find":
            self._find(words)
        elif name == "git":
            self._git(words)
        elif name == "xargs":
            self._xargs(words)
        elif name == "sort":
            self._sort(words)
        elif name == "uniq":
            _f, operands, _v = _args(words)
            if len(operands) > 1:
                self.mutates = True
                self._each(operands[1:2], follow=True)
        elif name == "cd":
            self._cd(words, isolated)
        elif name in READ_ONLY_VERBS:
            return
        else:
            raise _Opaque(f"unknown command: {name}")

    def _rm(self, name: str, words: list[Word]) -> None:
        flags, operands, _v = _args(words, short_arg="ns" if name == "shred" else "", long_arg=("--iterations", "--size"))
        self.mutates = True
        recursive = name == "rm" and _cluster_has(flags, "rR", "--recursive")
        self._each(operands, follow=False, expand=recursive)

    def _move(self, name: str, words: list[Word]) -> None:
        flags, operands, values = _args(words, short_arg="StmogT" if name != "ln" else "StT", long_arg=("--suffix",))
        if any(f.startswith("--target-directory") or f == "-t" or f.startswith("-t") and not f.startswith("--") and "t" in f[1:]
               for f in flags):
            raise _Opaque(f"{name} with a target directory option")
        self.mutates = True
        if name == "install" and _cluster_has(flags, "d"):
            self._each(operands, follow=False)
            return
        if name == "ln" and len(operands) == 1:
            operands = operands + [Word(os.path.basename(operands[0].text), os.path.basename(operands[0].text),
                                         False, False, False, True)]
        if len(operands) < 2:
            return
        sources, dest = operands[:-1], operands[-1]
        follow_dest = name in ("cp", "install")
        for cwd in self.cwds:
            for d in self._targets(dest, cwd):
                d_real = self._real(d, cwd, True)
                into = os.path.isdir(d_real) or dest.text.endswith("/") or len(sources) > 1
                if name == "mv":
                    for s in sources:
                        for t in self._targets(s, cwd):
                            r = self._real(t, cwd, False)
                            if os.path.isdir(r) and not os.path.islink(r):
                                for f in self._files_under(r):
                                    self._add(f)
                            else:
                                self._add(r)
                for s in sources:
                    for t in self._targets(s, cwd):
                        target = os.path.join(d, os.path.basename(t.rstrip("/"))) if into else d
                        self._add(self._real(target, cwd, follow_dest and not into))

    def _write(self, name: str, words: list[Word]) -> None:
        self.mutates = True
        if name == "dd":
            for w in words:
                if w.text.startswith("of="):
                    path = Word(w.text[3:], w.pattern[3:], w.glob, w.expands, w.brace, w.quoted)
                    if path.text.startswith("/dev/"):
                        continue
                    self._each([path], follow=True, create_ok=False)
            return
        short = {"truncate": "sr", "touch": "drt", "mkdir": "m"}.get(name, "")
        long = {"truncate": ("--size", "--reference"), "touch": ("--date", "--reference", "--time"),
                "mkdir": ("--mode",), "chmod": ("--reference",), "chown": ("--reference",), "chgrp": ("--reference",)}
        flags, operands, values = _args(words, short_arg=short, long_arg=long.get(name, ()))
        if name in ("chmod", "chown", "chgrp") and not any(f.startswith("--reference") for f in flags) and operands:
            operands = operands[1:]
        recursive = name in ("chmod", "chown", "chgrp") and _cluster_has(flags, "R", "--recursive")
        self._each(operands, follow=name != "mkdir", expand=recursive, create_ok=name in ("touch", "mkdir"))

    _SED_WRITE = re.compile(r"s(.)(?:\\.|(?!\1).)*\1(?:\\.|(?!\1).)*\1[a-zA-Z0-9]*[weWE]")
    _SED_CMD = re.compile(r"(?:^|[;{}\n])\s*(?:[0-9$]+|/(?:\\.|[^/])*/)?(?:,\s*(?:[0-9$]+|/(?:\\.|[^/])*/))?\s*!?\s*[wWer]\b")

    def _sed(self, words: list[Word]) -> None:
        scripts: list[str] = []
        files: list[Word] = []
        in_place = False
        have_script_flag = False
        i = 0
        done = False
        while i < len(words):
            t = words[i].text
            w = words[i]
            i += 1
            if done or not t.startswith("-") or t == "-":
                files.append(w)
                continue
            if t == "--":
                done = True
                continue
            if t.startswith("--"):
                if t.startswith("--in-place"):
                    in_place = True
                elif t in ("--expression", "--file") and i < len(words):
                    have_script_flag = True
                    scripts.append(words[i].text)
                    i += 1
                elif t.startswith("--expression=") or t.startswith("--file="):
                    have_script_flag = True
                    scripts.append(t.split("=", 1)[1])
                continue
            k = 1
            while k < len(t):
                ch = t[k]
                if ch == "i":
                    in_place = True
                    break
                if ch in "ef":
                    have_script_flag = True
                    rest = t[k + 1:]
                    if rest:
                        scripts.append(rest)
                    elif i < len(words):
                        scripts.append(words[i].text)
                        i += 1
                    break
                k += 1
        if not have_script_flag and files:
            scripts.append(files[0].text)
            files = files[1:]
        for script in scripts:
            if len(script) > MAX_SCRIPT or self._SED_WRITE.search(script) or self._SED_CMD.search(script):
                raise _Opaque("a sed script that can write or run commands")
        if in_place:
            self.mutates = True
            self._each(files, follow=True, create_ok=False)

    def _find(self, words: list[Word]) -> None:
        starts: list[Word] = []
        i = 0
        while i < len(words) and not words[i].text.startswith(("-", "!", "(")) :
            starts.append(words[i])
            i += 1
        expr = [w.text for w in words[i:]]
        if any(t in ("-ok", "-okdir") or t.startswith(("-fprint", "-fls")) for t in expr):
            raise _Opaque("find with -ok or a file output")
        k = 0
        while k < len(expr):
            if expr[k] in ("-exec", "-execdir"):
                if k + 1 >= len(expr) or expr[k + 1] not in FIND_EXEC_READ_ONLY:
                    raise _Opaque("find -exec with a command that is not read-only")
                end = next((j for j in range(k + 2, len(expr)) if expr[j] in (";", "+")), None)
                if end is None:
                    raise _Opaque("find -exec without its terminator")
                k = end
            k += 1
        if "-delete" in expr:
            self.mutates = True
            self._each(starts or [Word(".", ".", False, False, False, False)], follow=False, expand=True)

    def _xargs(self, words: list[Word]) -> None:
        """xargs runs its command on names from stdin, so only a read-only command keeps it read-only."""
        i = 0
        while i < len(words) and words[i].text.startswith("-"):
            text = words[i].text
            if text in XARGS_FLAGS or XARGS_JOINED.match(text):
                i += 1
            elif text in XARGS_VALUE_FLAGS:
                i += 2
            else:
                raise _Opaque("xargs with an option the gate does not read")
        if i >= len(words) or words[i].text not in FIND_EXEC_READ_ONLY:
            raise _Opaque("xargs with a command that is not read-only")

    def _git(self, words: list[Word]) -> None:
        moved = False
        while words and words[0].text in ("-C", "--no-pager"):
            if words[0].text == "-C":  # `git -C` with no directory leaves no subcommand and is refused below
                moved, words = True, words[2:]
            else:
                words = words[1:]
        if not words or words[0].text.startswith("-"):
            raise _Opaque("git with options before the subcommand")
        sub, rest = words[0].text, words[1:]
        if sub in GIT_READ_ONLY:
            return
        if moved:  # -C moves where paths resolve; only read-only subcommands get past it
            raise _Opaque("git -C with a subcommand that changes files")
        if sub == "rm":
            flags, operands, _v = _args(rest)
            self.mutates = True
            self._each(operands, follow=False, expand=_cluster_has(flags, "r"))
        elif sub == "mv":
            _f, operands, _v = _args(rest)
            self.mutates = True
            self._each(operands, follow=False)
        elif sub == "checkout":
            if not any(w.text == "--" for w in rest):
                raise _Opaque("git checkout without --")
            after = rest[[w.text for w in rest].index("--") + 1:]
            self.mutates = True
            self._each(after, follow=False, expand=True)
        elif sub == "restore":
            _f, operands, _v = _args(rest, short_arg="sS", long_arg=("--source",))
            self.mutates = True
            self._each(operands, follow=False, expand=True)
        else:
            raise _Opaque(f"git {sub}")

    def _sort(self, words: list[Word]) -> None:
        flags, operands, values = _args(words, short_arg="kotTSp", long_arg=("--output", "--key"))
        out = values.get("-o") or values.get("--output")
        if out is not None:
            self.mutates = True
            self._each([Word(out, _glob.escape(out), False, False, False, True)], follow=True)

    def _cd(self, words: list[Word], isolated: bool) -> None:
        _f, operands, _v = _args(words)
        if len(operands) != 1:
            raise _Opaque("cd without exactly one directory")
        if isolated:
            raise _Opaque("cd in a pipeline or a background job")
        w = operands[0]
        if w.expands or w.brace or w.glob or w.text == "-":
            raise _Opaque("cd to a directory the shell expands")
        fresh: set[str] = set()
        for cwd in self.cwds:
            target = os.path.realpath(w.text if os.path.isabs(w.text) else os.path.join(cwd, w.text))
            if not os.path.isdir(target):
                raise _Opaque("cd to a directory that does not exist")
            fresh.add(target)
        certain = self.reach_certain and self.cond_depth == 0
        self.cwds = fresh if certain else self.cwds | fresh


def _always_succeeds(words: list[Word]) -> bool:
    return bool(words) and words[0].text in ALWAYS_SUCCEEDS and not words[0].quoted


def classify_bash(command: str, root: str, cwd: str | None = None) -> Classification:
    """What does this command do to files under `root`? Never raises, never runs the command."""
    try:
        root_real = os.path.realpath(root)
        if not os.path.isdir(root_real):
            raise _Opaque("the work directory cannot be read")
        if len(command) > MAX_COMMAND:
            raise _Opaque("command too long to read")
        walker = _Walker(root_real, os.path.realpath(cwd or root_real))
        items = _lex(command)
        words: list[Word] = []
        redirs: list[tuple] = []
        before = ""
        stack: list[tuple[set[str], int]] = []
        for item in items + [("op", "\n")]:
            if item[0] == "word":
                w = item[1]
                if not words and not w.quoted and w.text in ("{", "}"):
                    continue
                words.append(w)
                continue
            if item[0] == "redir":
                redirs.append((item[1], item[2], item[3]))
                continue
            op = item[1]
            if op == "(":
                if words or redirs:
                    walker.segment(words, redirs, before, "(")
                    words, redirs = [], []
                stack.append((set(walker.cwds), walker.cond_depth))
                before = "("
                continue
            ran = bool(words or redirs)
            if ran:
                walker.segment(words, redirs, before, op)
            if op in (";", "\n", "&"):
                walker.reach_certain = True
            elif op == "&&":
                walker.reach_certain = ran and walker.reach_certain and _always_succeeds(words)
            elif op in ("||", ")"):
                walker.reach_certain = False
            words, redirs = [], []
            if op == ")" and stack:
                walker.cwds, walker.cond_depth = stack.pop()
            before = op
        paths = tuple(sorted(walker.paths))
        if walker.mutates:
            return Classification(MUTATES, paths, outside=tuple(sorted(walker.outside)))
        return Classification(READ_ONLY, ())
    except _Opaque as why:
        return Classification(OPAQUE, (), str(why))
    except (OSError, ValueError, re.error, RecursionError) as err:
        return Classification(OPAQUE, (), f"could not read the command: {type(err).__name__}")


# ---- decisions, ledger, manifest ------------------------------------------------------------------------

STOP = "Do not try another command to reach the same file. State what you need and stop."
EDIT_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
SAFE_TOOLS = frozenset({"Read", "Glob", "Grep", "LS", "TodoWrite", "NotebookRead"})
DENIAL_LIMIT = 5
DEFAULT_TIMEOUT = 10.0
VERSION = "0.3.0"
# Rebuilt by tools, so not protected. `.git` is not here: deleting the repository's history must ask first.
SKIP_DIRS = frozenset({"node_modules", "target", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache",
                       ".pytest_cache", ".ruff_cache", ".gradle", ".next"})
MAX_LISTED = 200_000  # past this every existing file counts as protected


# ---- configuration --------------------------------------------------------------------------------------

MISSION_LIMITS = ("max_workers", "max_dispatches", "max_commands", "max_output_tokens", "timeout_minutes",
                  "max_retries")
PRESETS = {"off": {"max_workers": None, "max_dispatches": None, "max_commands": None, "max_output_tokens": None,
                   "timeout_minutes": None, "max_retries": None, "exclusive_paths": False},
           # 12 = 4 workers, each dispatched once and retried at most twice
           "patton": {"max_workers": 4, "max_dispatches": 12, "max_commands": 20, "timeout_minutes": 15,
                      "max_retries": 2, "exclusive_paths": True}}
DEFAULTS = {
    "files": {"protect": True, "on_change": "ask", "unreadable_commands": "ask", "trusted_commands": [],
              "ignore": [], "always_protect": [], "skip_dirs": sorted(SKIP_DIRS), "max_refusals": DENIAL_LIMIT,
              "keep_originals_mb": 200},
    "missions": {"preset": "off", "max_workers": None, "max_dispatches": None, "max_commands": None,
                 "max_output_tokens": None, "timeout_minutes": None, "max_retries": None, "exclusive_paths": False},
    "verify": {"main": False, "subagents": False, "endpoint": "", "model": "", "api_key_env": "", "timeout_s": 120,
               "max_rounds": 2, "checks": [], "check_timeout_s": 300, "notes": ["README*", "HANDOFF*", "NOTES*", "*.md"],
               "max_diff_chars": 24000},
}
# Each choice field lists its values from loosest to strictest.
CHOICES = {"files.on_change": ("ask", "deny"), "files.unreadable_commands": ("allow", "ask", "deny"),
           "missions.preset": ("off", "patton")}
BOOLS = ("files.protect", "missions.exclusive_paths", "verify.main", "verify.subagents")
LISTS = ("files.trusted_commands", "files.ignore", "files.always_protect", "files.skip_dirs", "verify.checks",
         "verify.notes")
COUNTS = ("files.max_refusals", "verify.timeout_s", "verify.max_rounds", "verify.check_timeout_s",
          "verify.max_diff_chars")  # a whole number, at least 1, never off
# A string field and the pattern it must match; an empty string leaves it unset.
STRINGS = {"verify.endpoint": re.compile(r"(https?://\S+)?"), "verify.model": re.compile(r"\S*"),
           "verify.api_key_env": re.compile(r"([A-Za-z_][A-Za-z0-9_]*)?")}
# Fields a project file may never set: where the change and the key go, and what commands run. Anyone who commits
# to the repository, the agent included, can write that file.
USER_ONLY = frozenset(f"verify.{name}" for name in ("endpoint", "model", "api_key_env", "timeout_s", "max_rounds",
                                                     "checks", "check_timeout_s", "notes", "max_diff_chars"))
SIZES = ("files.keep_originals_mb",)  # a whole number of megabytes, 0 turns it off; more is stricter


class ConfigError(Exception):
    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path, self.message = path, message


def config_paths(root: str, environ, host: str = "claude") -> tuple[str, str]:
    """The user file and the project file the gate reads, whether or not they exist."""
    if environ.get("PATTON_GATE_CONFIG"):
        user = environ["PATTON_GATE_CONFIG"]
    else:
        home = environ.get("HOME") or os.path.expanduser("~")
        user = os.path.join(environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "patton-gate",
                            "config.json")
    return user, os.path.join(root, ".codex" if host == "codex" else ".claude", "patton-gate.json")


def _read_config_file(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return None
    except OSError as err:
        raise ConfigError(path, f"the configuration cannot be read ({type(err).__name__})") from err
    try:
        data = json.loads(text)
    except ValueError as err:
        raise ConfigError(path, f"the configuration is not valid JSON ({err})") from err
    if not isinstance(data, dict):
        raise ConfigError(path, "the configuration must be a JSON object")
    return data


def _validate_layer(path: str, data: dict, allow_projects: bool) -> tuple[dict, dict]:
    """Check every field of one layer against the schema. Returns the values its preset sets and the fields it
    names, each as {"section.field": value}; a named field wins over the preset."""
    known = {"files", "missions", "verify"} | ({"projects"} if allow_projects else set())
    out: dict[str, object] = {}
    for section, body in data.items():
        if section not in known:
            raise ConfigError(path, f"unknown field {section!r}"
                              + (" (a projects map belongs in the user file)" if section == "projects" else ""))
        if section == "projects":
            continue
        if not isinstance(body, dict):
            raise ConfigError(path, f"{section} must be an object")
        for name, value in body.items():
            key = f"{section}.{name}"
            if name not in DEFAULTS[section]:
                raise ConfigError(path, f"unknown field {key}")
            if key in CHOICES:
                if value not in CHOICES[key]:
                    raise ConfigError(path, f"{key} must be one of {', '.join(CHOICES[key])}")
            elif key in BOOLS:
                if not isinstance(value, bool):
                    raise ConfigError(path, f"{key} must be true or false")
            elif key in LISTS:
                if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                    raise ConfigError(path, f"{key} must be a list of non-empty strings")
            elif key in COUNTS:
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ConfigError(path, f"{key} must be a whole number of at least 1")
            elif key in STRINGS:
                if not isinstance(value, str) or not STRINGS[key].fullmatch(value):
                    raise ConfigError(path, f"{key} must be a string of the form {STRINGS[key].pattern}")
            elif key in SIZES:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ConfigError(path, f"{key} must be a whole number of at least 0")
            else:  # a mission limit: null turns it off
                whole = name != "timeout_minutes"
                ok = value is None or (not isinstance(value, bool) and isinstance(value, (int, float))
                                       and value >= 0 and (not whole or isinstance(value, int)))
                if not ok or (name == "timeout_minutes" and value is not None and value <= 0):
                    raise ConfigError(path, f"{key} must be null or a {'whole ' if whole else ''}number"
                                      f"{' above 0' if name == 'timeout_minutes' else ' of at least 0'}")
            out[key] = value
    preset = out.get("missions.preset")
    from_preset = {f"missions.{k}": v for k, v in PRESETS[str(preset)].items()} if preset is not None else {}
    return {k: v for k, v in from_preset.items() if k not in out}, out


def _tighter(key: str, current, new) -> bool:
    """Is `new` at least as strict as `current` for this field?"""
    if key in CHOICES:
        return CHOICES[key].index(new) >= CHOICES[key].index(current)
    if key in BOOLS:
        return bool(new) or not current
    if key == "files.always_protect":
        return True  # merged as a union, so it only ever adds protection
    if key == "files.skip_dirs":
        return set(new) <= set(current)
    if key in LISTS:  # trusted_commands and ignore loosen whatever they add
        return set(new) <= set(current)
    if key in COUNTS:
        return new <= current
    if key in SIZES:
        return new >= current
    if new is None:  # a mission limit
        return current is None
    return current is None or new <= current


def _apply(config: dict, sources: dict, ignored: list, layer: tuple[dict, dict], name: str, tighten_only: bool) -> None:
    from_preset, named = layer
    for key, value in [(k, v) for k, v in from_preset.items()] + [(k, v) for k, v in named.items()]:
        section, field = key.split(".")
        current = config[section][field]
        if key == "files.always_protect":
            value = sorted(set(current) | set(value))
        if tighten_only and key in USER_ONLY:
            if value != current:
                ignored.append({"field": key, "value": value, "kept": current, "file": name,
                                "why": "only the user file may set this"})
            continue
        if tighten_only and not _tighter(key, current, value):
            if key in named:  # a preset value that would loosen is skipped without a note
                ignored.append({"field": key, "value": value, "kept": current, "file": name,
                                "why": "a project file can only tighten; loosen it in the user file's projects map"})
            continue
        if value != current or key == "missions.preset":
            config[section][field] = value
            sources[key] = name + (" (preset)" if key in from_preset else "")


def load_config(root: str, environ, host: str = "claude") -> tuple[dict, dict, list]:
    """The effective configuration for `root`: defaults, then the user file, then the user file's entry for this
    project, then the project file, which may only tighten. Raises ConfigError naming the file on any problem."""
    config = json.loads(json.dumps(DEFAULTS))
    sources = {f"{s}.{f}": "default" for s in DEFAULTS for f in DEFAULTS[s]}
    ignored: list = []
    user_path, project_path = config_paths(root, environ, host)
    user = _read_config_file(user_path)
    if user is not None:
        _apply(config, sources, ignored, _validate_layer(user_path, user, True), "user file", False)
        projects = user.get("projects", {})
        if not isinstance(projects, dict):
            raise ConfigError(user_path, "projects must map a project path to an object")
        root_real = os.path.realpath(root)
        for project, body in projects.items():
            if not isinstance(body, dict):
                raise ConfigError(user_path, f"projects[{project}] must be an object")
            layer = _validate_layer(f"{user_path} projects[{project}]", body, False)
            if os.path.realpath(os.path.expanduser(project)) == root_real:
                _apply(config, sources, ignored, layer, f"user file, projects[{project}]", False)
    project = _read_config_file(project_path)
    if project is not None:
        _apply(config, sources, ignored, _validate_layer(project_path, project, False), "project file", True)
    verify = config["verify"]
    if (verify["main"] or verify["subagents"]) and not (verify["endpoint"] and verify["model"]):
        raise ConfigError(user_path, "verify is on but verify.endpoint and verify.model are not set; set both in "
                          "the user file")
    return config, sources, ignored


def _glob_regex(pattern: str) -> re.Pattern:
    """A project-relative glob as a regex. `**` crosses directories, `*` and `?` do not. A pattern without a slash
    matches a name at any depth, as in .gitignore."""
    p = pattern.strip()
    p = p[2:] if p.startswith("./") else p
    anchored = "/" in p.rstrip("/")
    p = p.strip("/")
    out, i = [], 0
    while i < len(p):
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif p[i] == "*":
            out.append("[^/]*")
            i += 1
        elif p[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(p[i]))
            i += 1
    return re.compile(("" if anchored else "(?:.*/)?") + "".join(out))


def _matches(patterns, rel: str) -> bool:
    if not patterns:
        return False
    parts = rel.split("/")
    candidates = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]  # the path and every directory above it
    return any(_glob_regex(p).fullmatch(c) for p in patterns for c in candidates)


def _trusted(command: str, prefixes) -> bool:
    """One plain command whose words start with a trusted prefix: no operator, redirect, expansion or second
    command, so trust in `pytest` never covers `pytest; rm notes.txt`."""
    if not prefixes:
        return False
    try:
        items = _lex(command)
    except _Opaque:
        return False
    if not items or any(item[0] != "word" or item[1].expands or item[1].brace for item in items):
        return False
    words = [item[1].text for item in items]
    return any(words[:len(prefix.split())] == prefix.split() for prefix in prefixes)


def _missions_on(missions: dict) -> bool:
    return any(missions.get(k) is not None for k in MISSION_LIMITS) or bool(missions.get("exclusive_paths"))


@dataclass
class Decision:
    verdict: str  # allow, deny, approve
    paths: tuple[str, ...] = ()
    reason: str = ""
    note: str = ""  # "stalled" when the denial limit ended the mission
    changed: tuple[str, ...] = ()  # project paths the call changes, protected or not


class ManifestError(Exception):
    pass


def _atomic_write(path: str, text: str) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _hash(path: str) -> str:
    digest = hashlib.sha256()
    if os.path.islink(path):
        digest.update(b"->" + os.readlink(path).encode("utf-8", "surrogateescape"))
    else:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


def write_manifest(root: str, state_dir: str, skip_dirs=SKIP_DIRS) -> None:
    """Record every file under `root` that existed now. A path in the manifest is protected.

    Paths only: a hash per file took minutes on a Rust tree. Sockets, FIFOs and devices are never opened (a FIFO
    would block the session start). Past MAX_LISTED paths the manifest is marked full and every file that exists
    counts as protected, which asks more often and never less."""
    import stat as _stat
    root_real = os.path.realpath(root)
    state_real = os.path.realpath(state_dir)
    files: dict[str, str] = {}
    full_up = False
    for dirpath, dirnames, filenames in os.walk(root_real, followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if d not in skip_dirs and os.path.realpath(os.path.join(dirpath, d)) != state_real]
        for name in filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
            full = os.path.join(dirpath, name)
            try:
                mode = os.lstat(full).st_mode
            except OSError:
                continue
            if not (_stat.S_ISREG(mode) or _stat.S_ISLNK(mode)):
                continue
            files[os.path.relpath(full, root_real).replace(os.sep, "/")] = ""
            if len(files) >= MAX_LISTED:
                full_up = True
                break
        if full_up:
            break
    os.makedirs(state_dir, exist_ok=True)
    _atomic_write(os.path.join(state_dir, "manifest.json"),
                  json.dumps({"root": root_real, "files": files, "full": full_up, "version": VERSION}))


class _Protected:
    """The protected set of a manifest that hit MAX_LISTED: listed paths, and any other path that exists."""

    def __init__(self, listed: frozenset[str], root: str) -> None:
        self.listed, self.root = listed, root

    def __contains__(self, rel: object) -> bool:
        return isinstance(rel, str) and (rel in self.listed or os.path.lexists(os.path.join(self.root, rel)))


@contextlib.contextmanager
def _locked(state_dir: str):
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, ".lock"), "a") as handle:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


class GateState:
    def __init__(self, root: str, state_dir: str, protected: frozenset[str], granted: set[str], denials: int) -> None:
        self.root, self.state_dir, self.protected, self.granted, self.denials = root, state_dir, protected, granted, denials
        self.config = json.loads(json.dumps(DEFAULTS))
        self.own: tuple[str, ...] = ()  # the gate's own settings and state: always asked about

    def is_protected(self, rel: str) -> bool:
        files = self.config["files"]
        if not files["protect"]:
            return False
        if _matches(files["always_protect"], rel):
            return True
        return rel in self.protected and not _matches(files["ignore"], rel)

    def own_hits(self, inside: tuple[str, ...], outside: tuple[str, ...]) -> list[str]:
        """The gate's own files a call changes: named directly, or under a directory it removes from outside."""
        hits = []
        for real in [os.path.join(self.root, rel) for rel in inside] + list(outside):
            for own in self.own:
                if real == own or real.startswith(own + os.sep) or (real in outside and own.startswith(real.rstrip(os.sep) + os.sep)):
                    hits.append(real if real in outside else os.path.relpath(real, self.root).replace(os.sep, "/"))
                    break
        return hits

    @classmethod
    def load(cls, state_dir: str, root: str) -> "GateState":
        try:
            manifest = _read_json(os.path.join(state_dir, "manifest.json"), None)
            if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
                raise ManifestError("the manifest is missing or unreadable")
            root_real = os.path.realpath(root)
            if manifest.get("root") != root_real:
                raise ManifestError("the manifest belongs to a different work directory")
            granted = _read_json(os.path.join(state_dir, "granted.json"), [])
            denials = _read_json(os.path.join(state_dir, "denials.json"), 0)
        except (OSError, ValueError) as err:
            raise ManifestError(f"the gate state is unreadable: {type(err).__name__}") from err
        if not isinstance(granted, list) or not isinstance(denials, int):
            raise ManifestError("the gate state is corrupt")
        listed = frozenset(manifest["files"])
        protected = _Protected(listed, root_real) if manifest.get("full") else listed
        return cls(root_real, state_dir, protected, set(map(str, granted)), denials)

    def grant(self, paths) -> None:
        with _locked(self.state_dir):
            current = set(_read_json(os.path.join(self.state_dir, "granted.json"), [])) | set(paths)
            _atomic_write(os.path.join(self.state_dir, "granted.json"), json.dumps(sorted(current)))
            self.granted = current

    def count_denial(self) -> None:
        with _locked(self.state_dir):
            count = int(_read_json(os.path.join(self.state_dir, "denials.json"), 0)) + 1
            _atomic_write(os.path.join(self.state_dir, "denials.json"), json.dumps(count))
            self.denials = count

    def record(self, entry: dict) -> None:
        with _locked(self.state_dir):
            with open(os.path.join(self.state_dir, "ledger.jsonl"), "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"t": round(time.time(), 3), **entry}, ensure_ascii=False) + "\n")

    def pending(self) -> dict:
        return _read_json(os.path.join(self.state_dir, "pending.json"), {})

    def set_pending(self, pending: dict) -> None:
        with _locked(self.state_dir):
            _atomic_write(os.path.join(self.state_dir, "pending.json"), json.dumps(pending))


# ---- originals ------------------------------------------------------------------------------------------

def keep_originals(state: GateState, changed: tuple[str, ...]) -> list[str]:
    """Copy each protected file a call may change, as it was at the session's first such call, into the session's
    state, so an approval given by mistake can be undone. Returns the paths that now have a copy."""
    import shutil
    limit = state.config["files"]["keep_originals_mb"] << 20
    wanted = [rel for rel in changed if rel in state.protected and state.is_protected(rel)]
    if not limit or not wanted:
        return []
    store = os.path.join(state.state_dir, "originals")
    index_path = os.path.join(state.state_dir, "originals.json")
    kept: list[str] = []
    with _locked(state.state_dir):
        index = _read_json(index_path, {"kept": {}, "skipped": {}, "bytes": 0})
        for rel in wanted:
            if rel in index["kept"]:
                kept.append(rel)
                continue
            source = os.path.join(state.root, rel)
            try:
                link = os.path.islink(source)
                size = 0 if link else os.path.getsize(source)
                if not link and not os.path.isfile(source):
                    continue
                if index["bytes"] + size > limit:
                    index["skipped"][rel] = f"too large to keep ({size} bytes; files.keep_originals_mb is "\
                                            f"{state.config['files']['keep_originals_mb']})"
                    continue
                target = os.path.join(store, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if link:
                    os.symlink(os.readlink(source), target)
                else:
                    shutil.copy2(source, target)
            except OSError as err:
                index["skipped"][rel] = f"could not be copied ({type(err).__name__})"
                continue
            index["kept"][rel] = {"bytes": size, "t": round(time.time(), 3), "link": link}
            index["bytes"] += size
            kept.append(rel)
        _atomic_write(index_path, json.dumps(index))
    return kept


# ---- missions -------------------------------------------------------------------------------------------

MISSION_TOOLS = frozenset({"Agent", "Task"})
RESERVATION_SECONDS = 60  # a dispatch that has not started a subagent by then no longer holds a place


def _now() -> float:
    return time.time()


def _missions_file(state_dir: str) -> str:
    return os.path.join(state_dir, "missions.json")


def _load_missions(state_dir: str) -> dict:
    data = _read_json(_missions_file(state_dir), {})
    if not isinstance(data, dict):
        raise ManifestError("the mission state is corrupt")
    for key in ("reservations", "running", "commands", "dispatches", "owners"):
        if not isinstance(data.setdefault(key, {}), dict):
            raise ManifestError("the mission state is corrupt")
    return data


def _save_missions(state_dir: str, data: dict) -> None:
    _atomic_write(_missions_file(state_dir), json.dumps(data))


def _refuse(field: str, why: str, then: str) -> Decision:
    return Decision("deny", reason=f"Patton gate: {why} (missions.{field} in the gate's settings). {then}",
                    note="mission:" + field)


def dispatch_decision(event: dict, state_dir: str, missions: dict) -> Decision:
    """A dispatch of a subagent: refused past the worker limit or the retry limit, otherwise counted."""
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    key = f"{tool_input.get('subagent_type', '')}\x00{tool_input.get('description') or str(tool_input.get('prompt', ''))[:200]}"
    now = _now()
    with _locked(state_dir):
        data = _load_missions(state_dir)
        data["reservations"] = {k: t for k, t in data["reservations"].items() if now - t < RESERVATION_SECONDS}
        tries = data["dispatches"].get(key, 0)
        retries, workers = missions["max_retries"], missions["max_workers"]
        if retries is not None and tries > retries:
            return _refuse("max_retries", f"this mission was dispatched {tries} times, and the limit is 1 plus "
                           f"{retries} retries", "Report the mission blocked to the person instead of dispatching it again.")
        total = sum(data["dispatches"].values())
        if missions["max_dispatches"] is not None and total >= missions["max_dispatches"]:
            return _refuse("max_dispatches", f"this session has dispatched {total} subagents, and the limit is "
                           f"{missions['max_dispatches']}", "Finish the work yourself, or report what is left.")
        busy = len(data["reservations"]) + len(data["running"])
        if workers is not None and busy >= workers:
            return _refuse("max_workers", f"{busy} subagents are running or starting, and the limit is {workers}",
                           "Wait for one to finish, or do this mission yourself.")
        data["reservations"][str(event.get("tool_use_id", ""))] = now
        data["dispatches"][key] = tries + 1
        _save_missions(state_dir, data)
    return Decision("allow")


def subagent_event(name: str, event: dict, state_dir: str) -> None:
    agent = str(event.get("agent_id") or "")
    with _locked(state_dir):
        data = _load_missions(state_dir)
        if name == "SubagentStart" and agent:
            if data["reservations"]:
                oldest = min(data["reservations"], key=data["reservations"].get)
                data["reservations"].pop(oldest)
            data["running"].setdefault(agent, {"start": _now()})
        elif name == "SubagentStop" and agent:
            data["running"].pop(agent, None)
            data["owners"] = {p: a for p, a in data["owners"].items() if a != agent}
        elif name in ("PostToolUse", "PostToolUseFailure"):
            data["reservations"].pop(str(event.get("tool_use_id", "")), None)
        _save_missions(state_dir, data)


def _output_tokens(event: dict, agent: str) -> int:
    """Output tokens in the subagent's transcript, each message counted once (it is written again as it streams)."""
    transcript = event.get("transcript_path")
    if not isinstance(transcript, str) or not transcript:
        return 0
    path = os.path.join(os.path.splitext(transcript)[0], "subagents", f"agent-{agent}.jsonl")
    seen: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                message = entry.get("message") if isinstance(entry, dict) else None
                if entry.get("type") == "assistant" and isinstance(message, dict) and isinstance(message.get("usage"), dict):
                    seen[str(message.get("id"))] = int(message["usage"].get("output_tokens") or 0)
    except FileNotFoundError:
        return 0
    return sum(seen.values())


def mission_budget(event: dict, state_dir: str, missions: dict) -> Decision:
    """A subagent's command: counted, then refused once its command, token or time budget is spent."""
    agent = str(event["agent_id"])
    now = _now()
    with _locked(state_dir):
        data = _load_missions(state_dir)
        started = data["running"].setdefault(agent, {"start": now})["start"]
        used = data["commands"].get(agent, 0) + 1
        data["commands"][agent] = used
        _save_missions(state_dir, data)
    report = "Stop here and report what you did and what is left."
    if missions["max_commands"] is not None and used > missions["max_commands"]:
        return _refuse("max_commands", f"this subagent has used its {missions['max_commands']} commands", report)
    if missions["timeout_minutes"] is not None and now - started > missions["timeout_minutes"] * 60:
        return _refuse("timeout_minutes", f"this subagent has run past its {missions['timeout_minutes']} minutes",
                       report)
    if missions["max_output_tokens"] is not None:
        tokens = _output_tokens(event, agent)
        if tokens >= missions["max_output_tokens"]:
            return _refuse("max_output_tokens", f"this subagent has generated {tokens} tokens, and the limit is "
                           f"{missions['max_output_tokens']}", report)
    return Decision("allow")


def exclusive_conflict(event: dict, state_dir: str, changed: tuple[str, ...], claim: bool) -> Decision | None:
    """Refuse a change to a path another running subagent changed; otherwise a subagent claims what it changes."""
    me = str(event.get("agent_id") or "")
    with _locked(state_dir):
        data = _load_missions(state_dir)
        taken = sorted(p for p in changed if data["owners"].get(p) not in (None, me))
        if taken:
            return _refuse("exclusive_paths", "another running subagent is changing " + ", ".join(taken),
                           "Leave those files to it, or wait until it finishes.")
        if claim and me and changed:
            for path in changed:
                data["owners"][path] = me
            _save_missions(state_dir, data)
    return None


def decide(event: dict, state: GateState) -> Decision:
    tool, tool_input = event.get("tool_name"), event.get("tool_input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        return Decision("deny", reason="Patton gate: the call has no readable tool input. " + STOP)
    if tool in SAFE_TOOLS:
        return Decision("allow")
    if tool not in EDIT_TOOLS and tool != "Bash":
        return Decision("deny", reason=f"Patton gate: the tool {tool!r} is not one the gate knows. " + STOP)
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and os.path.isabs(event["cwd"]) else state.root
    if tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return Decision("deny", reason="Patton gate: the Bash call has no command. " + STOP)
        found = classify_bash(command, state.root, cwd)
        if found.kind == READ_ONLY:
            return Decision("allow")
        if found.kind == OPAQUE:
            if _trusted(command, state.config["files"]["trusted_commands"]):
                return Decision("allow", note="trusted")
            # No stop sentence here: a rewrite comes back through the gate, so the protected files stay guarded.
            decision = Decision("deny", reason=(f"Patton gate: this command cannot be checked ({found.why}). Rewrite it "
                                                "as a plain command that names each file, such as ls, cat, rm, mv or an "
                                                "explicit redirect."), note="opaque:" + found.why)
        else:
            own = [o for o in state.own_hits(found.paths, found.outside) if o not in state.granted]
            decision = _needs(tuple(p for p in found.paths if state.is_protected(p) and p not in state.granted
                                    and p not in own), tuple(own))
            decision.changed = found.paths
    else:
        raw = tool_input.get(EDIT_TOOLS[tool])
        if not isinstance(raw, str) or not raw:
            return Decision("deny", reason=f"Patton gate: the {tool} call names no file. " + STOP)
        real = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(cwd, raw))
        rel = os.path.relpath(real, state.root).replace(os.sep, "/")
        inside = not (rel == ".." or rel.startswith("../"))
        own = [o for o in state.own_hits((rel,) if inside else (), () if inside else (real,)) if o not in state.granted]
        decision = _needs((rel,) if inside and state.is_protected(rel) and rel not in state.granted and rel not in own
                          else (), tuple(own))
        decision.changed = (rel,) if inside else ()
    if state.denials >= state.config["files"]["max_refusals"]:
        return Decision("deny", reason=("Patton gate: this mission has been refused too many times. Report the mission "
                                        "blocked and say which files you needed. " + STOP), note="stalled")
    return decision


def _needs(paths: tuple[str, ...], own: tuple[str, ...] = ()) -> Decision:
    if not paths and not own:
        return Decision("allow")
    parts = []
    if paths:
        parts.append("this changes " + ", ".join(paths) + ", which existed when the session started")
    if own:
        parts.append(("it also changes " if paths else "this changes ") + ", ".join(own)
                     + ", which holds the gate's own settings or state")
    return Decision("approve", paths + own, "Patton gate: " + "; ".join(parts) + ".", note="own" if own else "")


def _request(decision: Decision, event: dict, run: str) -> dict:
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    return {"run": run, "paths": list(decision.paths), "tool": event.get("tool_name"),
            "command": str(tool_input.get("command", ""))[:2000]}


def approve(decision: Decision, event: dict, spec: str | None, timeout: float = DEFAULT_TIMEOUT, run: str = "") -> dict:
    """Ask the approver about `decision.paths`. Any failure is a refusal. spec None means a person answers in the host."""
    if spec is None:
        return {"approved": [], "denied": list(decision.paths), "ask": True}
    request = _request(decision, event, run)
    refused = {"approved": [], "denied": list(decision.paths)}
    try:
        if spec.startswith(("http://", "https://")):
            req = urllib.request.Request(spec, data=json.dumps(request).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as response:
                answer = json.loads(response.read(1 << 20).decode("utf-8"))
        else:
            done = subprocess.run(shlex.split(spec), input=json.dumps(request), capture_output=True, text=True,
                                  timeout=timeout)
            if done.returncode != 0:
                return refused
            answer = json.loads(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError, UnicodeDecodeError):
        return refused
    if (not isinstance(answer, dict) or not isinstance(answer.get("approved"), list)
            or not isinstance(answer.get("denied"), list)
            or not all(isinstance(p, str) for p in answer["approved"] + answer["denied"])):
        return refused
    approved = [p for p in decision.paths if p in set(answer["approved"])]
    return {"approved": approved, "denied": [p for p in decision.paths if p not in approved]}


def _emit(stdout, verdict: str, reason: str) -> int:
    stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": verdict,
                                                    "permissionDecisionReason": reason}}))
    return 0


def _configured(event, environ) -> dict:
    """The environment with the plugin's defaults filled in. Explicit PATTON_GATE_* values always win."""
    env = dict(environ)
    if not isinstance(event, dict):
        return env
    if not env.get("PATTON_GATE_ROOT"):
        root = env.get("CLAUDE_PROJECT_DIR") or event.get("cwd")
        if isinstance(root, str) and os.path.isabs(root):
            env["PATTON_GATE_ROOT"] = root
    session = event.get("session_id")
    if not env.get("PATTON_GATE_STATE") and isinstance(session, str) and session:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:128]
        base = env.get("CLAUDE_PLUGIN_DATA") or env.get("PLUGIN_DATA") or os.path.join(
            env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state"),
            "patton-gate")
        env["PATTON_GATE_STATE"] = os.path.join(base, "sessions", safe)
    return env


def main(stdin, stdout, environ) -> int:
    """The hook entry point. Always exits 0; a decision, when there is one, is the JSON on stdout.

    Any error inside the gate denies a PreToolUse call: a gate that crashed has not checked anything."""
    try:
        raw = stdin.read()
    except OSError:
        raw = ""
    try:
        event = json.loads(raw)
    except ValueError:
        event = None
    try:
        return _run(event, stdout, _configured(event, environ))
    except Exception as err:  # noqa: BLE001 - fail closed on anything
        if isinstance(event, dict) and event.get("hook_event_name") == "PreToolUse":
            return _emit(stdout, "deny", f"Patton gate: the gate failed ({type(err).__name__}: {str(err)[:200]}), so "
                         "it refuses this call rather than let it run unchecked. Disable the patton-gate plugin to "
                         "continue without it, and report the error.")
        return 0


def _snapshot(root: str, state_dir: str, environ) -> dict:
    """Read the configuration for a new session and keep a copy, so an edit during the session waits for the next."""
    try:
        config, sources, ignored = load_config(root, environ, environ.get("PATTON_GATE_HOST", "claude"))
        snap = {"config": config, "sources": sources, "ignored": ignored, "error": None}
    except ConfigError as err:
        snap = {"error": str(err)}
    os.makedirs(state_dir, exist_ok=True)
    _atomic_write(os.path.join(state_dir, "config.json"), json.dumps(snap))
    return snap


def _session_config(state_dir: str) -> dict:
    """The session's configuration copy. A session that started before the plugin had settings uses the defaults."""
    snap = _read_json(os.path.join(state_dir, "config.json"), None)
    if snap is None:
        return {"config": json.loads(json.dumps(DEFAULTS)), "error": None}
    if not isinstance(snap, dict) or (snap.get("error") is None and not isinstance(snap.get("config"), dict)):
        return {"error": "the session's copy of the configuration is corrupt"}
    return snap


def own_files(root: str, state_dir: str, environ) -> tuple[str, ...]:
    """The gate's settings, its state, and the host settings that can switch it off."""
    home = environ.get("HOME") or os.path.expanduser("~")
    user, _project = config_paths(root, environ)
    found = [user, os.path.join(root, ".claude", "patton-gate.json"), os.path.join(root, ".codex", "patton-gate.json"),
             os.path.dirname(os.path.dirname(state_dir)) if os.path.basename(os.path.dirname(state_dir)) == "sessions"
             else state_dir]
    if environ.get("PATTON_GATE_HOST") == "codex":
        codex_home = environ.get("CODEX_HOME") or os.path.join(home, ".codex")
        found += [os.path.join(codex_home, "config.toml"), os.path.join(codex_home, "hooks.json")]
    else:
        found += [os.path.join(home, ".claude", "settings.json"), os.path.join(root, ".claude", "settings.json"),
                  os.path.join(root, ".claude", "settings.local.json")]
    for plugin_root in (environ.get("CLAUDE_PLUGIN_ROOT"), environ.get("PLUGIN_ROOT")):
        if plugin_root:
            found.append(plugin_root)
    return tuple(sorted({os.path.realpath(p) for p in found}))


# ---- the blind Verifier and the candidate digest ------------------------------------------------------------
#
# When the agent (or a subagent) stops, a reader that sees only the person's request and the agent's final message
# says whether the agent claims the work is done. If it does, a Verifier that never sees that message reads the
# request, the change since the turn began, the project notes as they were before it, and the configured checks'
# output, and says pass or fail. A failure it backs with a quote from what it read refuses the stop, at most
# verify.max_rounds times a turn; after that the person is told. The change is named by a git tree hash, the
# candidate digest, taken through a private index and object store, so the user's index, refs and objects stay as
# they were. Any error lets the agent stop and says the change was not verified: a check that could not run must
# not trap the session.

CLAIMS_SYSTEM = """You read the final message of a coding agent. Say whether the message states that the work the
person asked for is done. A message that reports a problem, asks a question, or says part of the work is left
undone does not claim it is done.

Answer with JSON only: {"claims_done": true or false, "quote": "<the sentence that says the work is done, copied
word for word, or empty>"}"""

VERIFIER_SYSTEM = """You are an independent verifier. A coding agent has changed a project to do what a person
asked. You see the person's request, the project's notes as they were before the change, the output of the
project's checks run on the change, and the change itself. You do not see what the agent said about its work.

Decide whether the change does what was asked without losing or breaking anything the request or the notes say
must stay. Fail only for a concrete problem: the requested change is missing or only partly made, a check fails,
or the change removes, overwrites or breaks something the notes or the request say is needed. Pass when the change
does what was asked, including when you would have done it differently.

Answer with JSON only: {"verdict": "pass" or "fail", "findings": [{"problem": "<one sentence>", "evidence":
"<text copied word for word from the request, the notes, the checks or the change that shows the problem>"}]}"""

VERIFY_MIN_EVIDENCE = 12
VERIFY_PROMPTS_KEPT = 3
_FILE_CAP = 20000


class VerifyError(Exception):
    pass


def _git_run(root: str, args: list[str], env_extra: dict | None = None, timeout: float = 60) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(env_extra or {})
    done = subprocess.run(["git", "-C", root, *args], capture_output=True, env=env, timeout=timeout)
    if done.returncode != 0:
        raise VerifyError(f"git {args[0]} failed: {done.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return done.stdout


def _git_env(root: str, state_dir: str) -> dict | None:
    """The environment that points git at the gate's own object store, borrowing the repository's objects."""
    try:
        common = _git_run(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]).decode().strip()
    except (VerifyError, OSError, subprocess.SubprocessError):
        return None
    objects = os.path.join(state_dir, "git-objects")
    os.makedirs(objects, exist_ok=True)
    return {"GIT_OBJECT_DIRECTORY": objects, "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.path.join(common, "objects")}


def git_snapshot(root: str, state_dir: str) -> str | None:
    """The git tree hash of the working tree as git sees it (tracked and untracked files, ignored files left out),
    or None when `root` is not in a git repository. It writes only into the gate's state directory."""
    env = _git_env(root, state_dir)
    if env is None:
        return None
    try:
        git_dir = _git_run(root, ["rev-parse", "--absolute-git-dir"]).decode().strip()
    except (VerifyError, OSError, subprocess.SubprocessError):
        return None
    index = os.path.join(state_dir, f"git-index-{os.getpid()}-{time.time_ns()}")
    try:
        try:
            with open(os.path.join(git_dir, "index"), "rb") as src, open(index, "wb") as dst:
                dst.write(src.read())  # its stat cache saves hashing every unchanged file
        except OSError:
            pass
        env = {**env, "GIT_INDEX_FILE": index}
        _git_run(root, ["add", "-A", "--", "."], env, timeout=120)
        return _git_run(root, ["write-tree"], env).decode().strip()
    finally:
        for leftover in (index, index + ".lock"):
            with contextlib.suppress(OSError):
                os.remove(leftover)


def _blob(root: str, env: dict, tree: str, path: str) -> str:
    data = _git_run(root, ["cat-file", "blob", f"{tree}:{path}"], env)
    if b"\0" in data[:8000]:
        return f"[binary file, {len(data)} bytes]"
    text = data.decode("utf-8", "replace")
    return text if len(text) <= _FILE_CAP else text[:_FILE_CAP] + "\n[cut]"


def file_changes(pairs, max_chars: int = 24000) -> list[dict]:
    """(path, status, text before or None, text after or None) to the change records the Verifier reads, each with a
    unified diff; past `max_chars` of diff the rest is cut."""
    import difflib
    changes, used = [], 0
    for path, status, before, after in pairs:
        diff = "".join(difflib.unified_diff((before or "").splitlines(True), (after or "").splitlines(True),
                                            f"a/{path}", f"b/{path}"))
        if used + len(diff) > max_chars:
            diff = diff[:max(0, max_chars - used)] + "\n[diff cut]"
        used += len(diff)
        changes.append({"path": path, "status": status, "before": before, "after": after, "diff": diff})
    return changes


def git_changes(root: str, state_dir: str, base: str, head: str, max_chars: int = 24000) -> list[dict]:
    """Every file that differs between two snapshot trees, with its text before and after and a unified diff."""
    env = _git_env(root, state_dir)
    if env is None:
        raise VerifyError("not a git repository")
    raw = _git_run(root, ["diff-tree", "-r", "-z", "--no-renames", "--name-status", base, head], env).decode()
    parts = [p for p in raw.split("\0") if p]
    names = {"A": "added", "D": "deleted", "M": "modified", "T": "modified"}
    pairs = [(path, names.get(status[:1], "modified"), None if status == "A" else _blob(root, env, base, path),
              None if status == "D" else _blob(root, env, head, path))
             for status, path in zip(parts[0::2], parts[1::2])]
    return file_changes(pairs, max_chars)


def verify_notes(root: str, state_dir: str, tree: str, patterns, cap: int = 8000) -> list[tuple[str, str]]:
    env = _git_env(root, state_dir)
    if env is None or not patterns:
        return []
    names = [n for n in _git_run(root, ["ls-tree", "-r", "-z", "--name-only", tree], env).decode().split("\0") if n]
    notes, used = [], 0
    for name in sorted(n for n in names if _matches(patterns, n)):
        text = _blob(root, env, tree, name)
        if used + len(text) > cap:
            break
        notes.append((name, text))
        used += len(text)
    return notes


def run_checks(root: str, checks, timeout: float) -> list[dict]:
    results = []
    for command in checks:
        try:
            done = subprocess.run(["/bin/sh", "-c", command], cwd=root, capture_output=True, timeout=timeout)
            code, output = str(done.returncode), (done.stdout + done.stderr).decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            code, output = "timeout", f"the check did not finish within {timeout:g} s"
        except OSError as err:
            code, output = "error", f"the check could not start ({err})"
        results.append({"command": command, "code": code, "output": output[-3000:]})
    return results


def verifier_input(mission: str, changes: list[dict], notes, checks: list[dict], digest: str) -> str:
    lines = ["## The person's request", mission.strip() or "(no request recorded)", "",
             "## Project notes before the change"]
    lines += [f"### {path}\n{text.rstrip()}\n" for path, text in notes] or ["(none)", ""]
    lines += ["## Checks run on the change"]
    for check in checks:
        lines += [f"### `{check['command']}` (exit code {check['code']})", check["output"].rstrip() or "(no output)", ""]
    if not checks:
        lines += ["(none configured)", ""]
    lines += [f"## The change (candidate {digest[:12]})"]
    for change in changes:
        lines += [f"### {change['path']} ({change['status']})", "```diff", change["diff"].rstrip(), "```", ""]
    if not changes:
        lines += ["No file changed."]
    return "\n".join(lines)


def claims_input(mission: str, final: str) -> str:
    return "\n".join(["## The person's request", mission.strip() or "(no request recorded)", "",
                      "## The agent's final message", final.strip() or "(empty)"])


def parse_reply(text) -> dict | None:
    """The first JSON object in a model's reply, inside a code fence or not."""
    if not isinstance(text, str):
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fence.group(1)] if fence else []
    start = text.find("{")
    if start >= 0:
        candidates.append(text[start:])
    for candidate in candidates:
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def verify_chat(cfg: dict, environ, system: str, user: str) -> str:
    """One OpenAI-compatible chat completion. The key comes from the environment variable the user file names."""
    key = environ.get(cfg["api_key_env"], "") if cfg["api_key_env"] else ""
    body = json.dumps({"model": cfg["model"], "max_tokens": 4096,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    request = urllib.request.Request(cfg["endpoint"].rstrip("/") + "/chat/completions", data=body.encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(request, timeout=cfg["timeout_s"]) as response:
            data = json.loads(response.read())
        content = data["choices"][0]["message"]["content"]
    except Exception as err:  # noqa: BLE001 - any failure means "not verified"
        raise VerifyError(f"the verifier endpoint failed ({type(err).__name__}: {str(err)[:160]})") from err
    if not isinstance(content, str):
        raise VerifyError("the verifier endpoint returned no text")
    return content


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("`", "")).strip().lower()


def quoted_findings(reply: dict, source: str) -> list[dict]:
    """The findings whose evidence is quoted from what the Verifier read; the rest cannot refuse a stop."""
    findings = reply.get("findings") if isinstance(reply.get("findings"), list) else []
    text = _norm_text(source)
    out = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        problem, evidence = finding.get("problem"), finding.get("evidence")
        if isinstance(problem, str) and isinstance(evidence, str) and len(_norm_text(evidence)) >= VERIFY_MIN_EVIDENCE \
                and _norm_text(evidence) in text:
            out.append({"problem": problem.strip(), "evidence": evidence.strip()})
    return out


def read_claim(reply) -> bool:
    """Whether the agent claimed the work done. An answer that cannot be read counts as a claim, so the change is
    still checked."""
    claim = parse_reply(reply)
    return True if claim is None or not isinstance(claim.get("claims_done"), bool) else claim["claims_done"]


def judge(reply, source: str) -> tuple[str | None, list[dict], str]:
    """(verdict, quoted findings, decision) from the Verifier's answer and the text it read. Only a failure backed by
    a quote can refuse a stop; an answer that cannot be read is no verdict at all."""
    parsed = parse_reply(reply)
    if parsed is None or parsed.get("verdict") not in ("pass", "fail"):
        return None, [], "allow (not verified)"
    if parsed["verdict"] == "pass":
        return "pass", [], "allow (passed)"
    findings = quoted_findings(parsed, source)
    return "fail", findings, "block" if findings else "allow (failure without quoted evidence)"


def _turns_file(state_dir: str) -> str:
    return os.path.join(state_dir, "verify-turns.json")


def _mission_from_transcript(path) -> str:
    """The first user message of a subagent's transcript: the prompt its dispatcher gave it."""
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                message = item.get("message") if isinstance(item, dict) else None
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    except (OSError, TypeError):
        pass
    return ""


def verify_event(name: str, event: dict, state_dir: str, root: str, environ, cfg: dict) -> dict | None:
    """Handle one hook event for the Verifier. Returns the JSON to print, or None."""
    scope = "main" if name in ("UserPromptSubmit", "Stop") else f"agent:{event.get('agent_id') or ''}"
    if (scope == "main" and not cfg["main"]) or (scope != "main" and not cfg["subagents"]):
        return None
    if name in ("UserPromptSubmit", "SubagentStart"):
        base = git_snapshot(root, state_dir)
        with _locked(state_dir):
            turns = _read_json(_turns_file(state_dir), {})
            turns = turns if isinstance(turns, dict) else {}
            old = turns.get(scope) or {}
            prompts = (old.get("prompts") or []) + ([str(event.get("prompt") or "")] if scope == "main" else [])
            turns[scope] = {"base": base, "rounds": 0, "prompts": prompts[-VERIFY_PROMPTS_KEPT:]}
            _atomic_write(_turns_file(state_dir), json.dumps(turns))
        return None
    with _locked(state_dir):
        turns = _read_json(_turns_file(state_dir), {})
    turn = (turns.get(scope) if isinstance(turns, dict) else None) or {}
    entry = {"t": round(_now(), 3), "scope": scope, "root": root, "base": turn.get("base")}
    try:
        if not turn.get("base"):
            raise VerifyError("no snapshot of the turn's start (the project is not a git repository, or the turn "
                              "began before the verifier was on)")
        if scope == "main":
            prompts = turn.get("prompts") or []
            mission = prompts[-1] if prompts else ""
            if len(prompts) > 1:
                mission = "Earlier requests in this session:\n" + "\n".join(f"- {p}" for p in prompts[:-1]) + \
                          "\n\nThe latest request:\n" + mission
        else:
            mission = _mission_from_transcript(event.get("agent_transcript_path"))
        final = str(event.get("last_assistant_message") or "")
        claims = read_claim(verify_chat(cfg, environ, CLAIMS_SYSTEM, claims_input(mission, final)))
        entry["claims_done"] = claims
        if not claims:
            entry["decision"] = "allow (no completion claimed)"
            return None
        digest = git_snapshot(root, state_dir)
        if digest is None:
            raise VerifyError("the candidate could not be snapshotted")
        entry["digest"] = digest
        changes = git_changes(root, state_dir, turn["base"], digest, cfg["max_diff_chars"])
        entry["files"] = [c["path"] for c in changes]
        notes = verify_notes(root, state_dir, turn["base"], cfg["notes"])
        checks = run_checks(root, cfg["checks"], cfg["check_timeout_s"]) if cfg["checks"] else []
        entry["checks"] = [{"command": c["command"], "code": c["code"]} for c in checks]
        source = verifier_input(mission, changes, notes, checks, digest)
        verdict, findings, judged = judge(verify_chat(cfg, environ, VERIFIER_SYSTEM, source), source)
        if verdict is None:
            raise VerifyError("the verifier's answer could not be read")
        entry["verdict"] = verdict
        if judged != "block":
            entry["decision"] = judged
            if findings:
                entry["findings"] = findings
            return None
        entry["findings"] = findings
        listed = "\n".join(f"- {f['problem']} (evidence: \"{f['evidence']}\")" for f in findings)
        rounds = int(turn.get("rounds") or 0)
        if rounds >= cfg["max_rounds"]:
            entry["decision"] = "allow (round cap)"
            return {"systemMessage": f"Patton verify: the independent check of candidate {digest[:12]} still fails "
                                     f"after {rounds} refused stop(s), so the agent was allowed to stop:\n{listed}"}
        with _locked(state_dir):
            turns = _read_json(_turns_file(state_dir), {})
            if isinstance(turns, dict) and isinstance(turns.get(scope), dict):
                turns[scope]["rounds"] = rounds + 1
                _atomic_write(_turns_file(state_dir), json.dumps(turns))
        entry["decision"] = "block"
        return {"decision": "block",
                "reason": (f"Patton verify: an independent check of this change (candidate {digest[:12]}) found a "
                           f"problem:\n{listed}\nFix it, or say plainly in your final message what is wrong and what "
                           "is left undone. The check runs again when you stop.")}
    except (VerifyError, OSError, subprocess.SubprocessError, ValueError) as err:
        entry["decision"] = "allow (not verified)"
        entry["error"] = str(err)[:300]
        return {"systemMessage": f"Patton verify: this change was not verified ({str(err)[:200]})."}
    finally:
        if "decision" in entry:
            with _locked(state_dir), open(os.path.join(state_dir, "verify.jsonl"), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")


def verify_command(args: list[str], stdout, environ) -> int:
    """List a session's verdicts, and whether the working tree still matches the last checked candidate."""
    if "--state" in args:
        state_dir = args[args.index("--state") + 1]
    else:
        base = _sessions_dir(environ, "--codex" in args)
        sessions = sorted(_glob.glob(os.path.join(base, "*", "verify.jsonl")), key=os.path.getmtime)
        if "--session" in args:
            sessions = [os.path.join(base, args[args.index("--session") + 1], "verify.jsonl")]
        if not sessions:
            stdout.write("no verified session found\n")
            return 1
        state_dir = os.path.dirname(sessions[-1])
    try:
        with open(os.path.join(state_dir, "verify.jsonl"), encoding="utf-8") as handle:
            entries = [json.loads(line) for line in handle if line.strip()]
    except (OSError, ValueError) as err:
        stdout.write(f"cannot read the verdicts in {state_dir}: {err}\n")
        return 1
    for entry in entries:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.get("t", 0)))
        digest = (entry.get("digest") or "")[:12] or "-"
        stdout.write(f"{when}  {entry.get('scope', '?'):14} {digest:12}  {entry.get('decision')}\n")
        for finding in entry.get("findings") or []:
            stdout.write(f"    {finding['problem']}\n")
        if entry.get("error"):
            stdout.write(f"    {entry['error']}\n")
    last = next((e for e in reversed(entries) if e.get("digest")), None)
    if last:
        now = git_snapshot(last["root"], state_dir)
        same = now == last["digest"]
        stdout.write(f"the working tree {'matches' if same else 'changed since'} candidate {last['digest'][:12]}"
                     f"{'' if same else f' (now {(now or chr(63))[:12]})'}\n")
    return 0


def _run(event, stdout, environ) -> int:
    name = event.get("hook_event_name") if isinstance(event, dict) else None
    state_dir, root = environ.get("PATTON_GATE_STATE"), environ.get("PATTON_GATE_ROOT")
    study = bool(environ.get("PATTON_GATE_APPROVER"))
    if name == "SessionStart":
        if state_dir and root and (study or not os.path.exists(os.path.join(state_dir, "manifest.json"))):
            snap = {"config": DEFAULTS} if study else _snapshot(root, state_dir, environ)
            skip = (snap.get("config") or DEFAULTS)["files"]["skip_dirs"]
            try:
                write_manifest(root, state_dir, frozenset(skip))
            except OSError:
                pass
        return 0
    verdict = None
    if name in ("UserPromptSubmit", "Stop", "SubagentStart", "SubagentStop") and state_dir and root and not study:
        session = _session_config(state_dir)
        if not session.get("error"):
            try:
                verdict = verify_event(name, event, state_dir, root, environ, (session["config"] or DEFAULTS)["verify"])
            except Exception as err:  # noqa: BLE001 - a broken check must never trap the session
                verdict = {"systemMessage": f"Patton verify: this change was not verified ({type(err).__name__})."}
        if verdict:
            stdout.write(json.dumps(verdict))
        if name in ("UserPromptSubmit", "Stop"):
            return 0
    if name in ("SubagentStart", "SubagentStop", "PostToolUseFailure") or (
            name == "PostToolUse" and event.get("tool_name") in MISSION_TOOLS):
        refused = name == "SubagentStop" and (verdict or {}).get("decision") == "block"  # it keeps running
        if state_dir and not study and not refused and _missions_on(
                (_session_config(state_dir).get("config") or DEFAULTS)["missions"]):
            subagent_event(name, event, state_dir)
        return 0
    if name == "PostToolUse":
        return _after(event, state_dir, root, environ)
    if name != "PreToolUse":
        return _emit(stdout, "deny", "Patton gate: the hook input could not be read. " + STOP)
    if not state_dir or not root:
        return _emit(stdout, "deny", "Patton gate: the gate could not tell the project or the session (no project "
                     "directory or session id in the hook input). " + STOP)
    if not os.path.isfile(os.path.join(state_dir, "manifest.json")):
        # SessionStart cannot block, so a manifest it failed to write is reported here, with the place it tried.
        return _emit(stdout, "deny", f"Patton gate: the manifest is missing or unreadable in {state_dir}: the session "
                     "started before the plugin was enabled, or the gate could not write there. Start a new session "
                     "so the gate can record which files exist. " + STOP)
    snap = {"config": DEFAULTS, "error": None} if study else _session_config(state_dir)
    if snap.get("error"):
        return _emit(stdout, "deny", f"Patton gate: the gate's configuration has a problem: {snap['error']}. Fix the "
                     "file (check it with `python3 <plugin>/scripts/patton_gate.py config`), then start a new "
                     "session. Until then the gate refuses every call it sees. " + STOP)
    config = snap["config"]
    missions = config["missions"]
    limits_on = not study and _missions_on(missions)
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    entry = {"event": "PreToolUse", "tool": event.get("tool_name"), "paths": [],
             "run": environ.get("PATTON_GATE_RUN", ""), "approver": environ.get("PATTON_GATE_OPERATOR", "operator"),
             "command": str(tool_input.get("command", ""))[:500], "tool_use_id": event.get("tool_use_id", "")}
    if event.get("agent_id"):
        entry["agent"] = str(event["agent_id"])
    if event.get("tool_name") in MISSION_TOOLS:
        if not limits_on:
            return 0
        decision = dispatch_decision(event, state_dir, missions)
        _record(state_dir, {**entry, "command": str(tool_input.get("description", ""))[:200],
                            "verdict": decision.verdict, "reason": decision.reason[:300]})
        return 0 if decision.verdict == "allow" else _emit(stdout, "deny", decision.reason)
    if limits_on and event.get("agent_id") and event.get("tool_name") in set(EDIT_TOOLS) | {"Bash"}:
        budget = mission_budget(event, state_dir, missions)
        if budget.verdict == "deny":
            _record(state_dir, {**entry, "verdict": "deny", "reason": budget.reason[:300]})
            return _emit(stdout, "deny", budget.reason)
    try:
        state = GateState.load(state_dir, root)
    except ManifestError as err:
        return _emit(stdout, "deny", f"Patton gate: {err}. Start a new session so the gate can record which files "
                     "exist. " + STOP)
    state.config = config
    if not study:
        state.own = own_files(state.root, state_dir, environ)
    decision = decide(event, state)
    entry["paths"] = list(decision.paths)
    if decision.changed:
        entry["changed"] = list(decision.changed)
    exclusive = limits_on and missions["exclusive_paths"] and decision.note != "stalled"
    if exclusive:
        conflict = exclusive_conflict(event, state_dir, decision.changed, claim=False)
        if conflict is not None:
            state.record({**entry, "verdict": "deny", "reason": conflict.reason[:300]})
            return _emit(stdout, "deny", conflict.reason)

    def lost_claim() -> int | None:
        """Only a call that runs, or that the person is asked about, takes its paths. Checking and taking happen
        under one lock, so of two subagents that reach the same file at once, one gets it and one is refused."""
        lost = exclusive_conflict(event, state_dir, decision.changed, claim=True) if exclusive else None
        if lost is None:
            return None
        state.record({**entry, "verdict": "deny", "reason": lost.reason[:300]})
        return _emit(stdout, "deny", lost.reason)

    if decision.verdict == "allow":
        state.record({**entry, "verdict": "allow", **({"note": decision.note} if decision.note else {})})
        if study:
            return _emit(stdout, "allow", "Patton gate: nothing protected is changed.")
        refused = lost_claim()
        if refused is None and decision.changed:
            keep_originals(state, decision.changed)
        return 0 if refused is None else refused  # no decision: the person's own permission settings apply
    opaque = decision.note.startswith("opaque:")
    unreadable = "deny" if environ.get("PATTON_GATE_OPAQUE") == "deny" else config["files"]["unreadable_commands"]
    if opaque and not study and (unreadable == "allow" or not config["files"]["protect"]):
        state.record({**entry, "verdict": "allow", "opaque": True, "note": "unreadable commands are left to the "
                      "person's settings" if config["files"]["protect"] else "file protection is off"})
        return 0
    # Claude Code 2.1.303 auto-approved a hook's ask for `rm -rf .git` under bypassPermissions while it honoured the
    # same ask for an ordinary file, so in that mode the gate refuses, as the study measured, instead of asking.
    asks = event.get("permission_mode") != "bypassPermissions"
    if decision.verdict == "deny" and opaque and not study and asks and unreadable != "deny":
        command = str(tool_input.get("command", ""))
        if command and command in _approved_commands(state_dir):
            state.record({**entry, "verdict": "allow", "opaque": True, "note": "approved earlier this session"})
            return 0
        state.set_pending({**state.pending(), str(event.get("tool_use_id", "")): {"command": command}})
        state.record({**entry, "verdict": "asked", "opaque": True, "reason": decision.note[len("opaque:"):][:200]})
        return _emit(stdout, "ask", "Patton gate cannot tell which files this command changes ("
                     f"{decision.note[len('opaque:'):]}). Allow it only if it leaves the files you need in place.")
    if decision.verdict == "deny":
        state.record({**entry, "verdict": "stalled" if decision.note == "stalled" else "deny", "reason": decision.reason[:300]})
        if decision.note != "stalled":
            state.count_denial()
        return _emit(stdout, "deny", decision.reason)
    spec = environ.get("PATTON_GATE_APPROVER") or None
    if spec is None and config["files"]["on_change"] == "deny" and decision.note != "own":
        reason = (f"{decision.reason} The gate's settings refuse such changes (files.on_change = deny). Ask the "
                  "person to make this change. " + STOP)
        state.count_denial()
        state.record({**entry, "verdict": "deny", "reason": reason[:300]})
        return _emit(stdout, "deny", reason)
    if spec is None and not asks:
        reason = (f"{decision.reason} The session runs with bypassPermissions, where Claude Code may run a call "
                  "without asking, so the gate refuses it. Ask the person to make this change, or to restart the "
                  "session without bypassPermissions so they can approve it. " + STOP)
        state.count_denial()
        state.record({**entry, "verdict": "deny", "reason": reason[:300]})
        return _emit(stdout, "deny", reason)
    try:
        timeout = float(environ.get("PATTON_GATE_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    answer = approve(decision, event, spec, timeout, environ.get("PATTON_GATE_RUN", ""))
    if answer.get("ask"):
        refused = lost_claim()
        if refused is not None:
            return refused
        state.set_pending({**state.pending(), str(event.get("tool_use_id", "")): list(decision.paths)})
        kept = keep_originals(state, decision.changed)
        state.record({**entry, "verdict": "asked", **({"kept": kept} if kept else {})})
        reason = decision.reason
        named = [p for p in decision.paths if p in decision.changed]  # project files, not the gate's own
        if named and set(named) <= set(kept):
            reason += (" The gate keeps a copy of each as it was; `python3 <plugin>/scripts/patton_gate.py restore` "
                       "puts it back.")
        return _emit(stdout, "ask", reason)
    state.grant(answer["approved"])
    answered = {**entry, "granted": answer["approved"], "denied": answer["denied"]}
    if not answer["denied"]:
        state.record({**answered, "verdict": "approved"})
        return _emit(stdout, "allow", "Patton gate: the operator approved " + ", ".join(decision.paths) + ".")
    state.count_denial()
    reason = "Patton gate: the operator refused " + ", ".join(answer["denied"]) + ". "
    if answer["approved"]:  # the whole call is blocked, so say the approved part may run on its own
        reason = ("Patton gate: the operator approved " + ", ".join(answer["approved"]) + ": run the command again "
                  "for those files alone. The operator refused " + ", ".join(answer["denied"]) + ". ")
    reason += STOP
    state.record({**answered, "verdict": "refused", "reason": reason[:300]})
    return _emit(stdout, "deny", reason)


def _record(state_dir: str, entry: dict) -> None:
    with _locked(state_dir):
        with open(os.path.join(state_dir, "ledger.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": round(time.time(), 3), **entry}, ensure_ascii=False) + "\n")


def _after(event, state_dir, root, environ) -> int:
    if not (isinstance(event, dict) and state_dir and root):
        return 0
    try:
        state = GateState.load(state_dir, root)
    except ManifestError:
        return 0
    pending = state.pending()
    tool_use_id = str(event.get("tool_use_id", ""))
    if tool_use_id in pending:
        paths = pending.pop(tool_use_id)
        if isinstance(paths, dict):  # an unreadable command the person let run: not asked again this session
            _approve_command(state_dir, str(paths.get("command", "")))
            paths = []
        state.grant(paths)
        state.set_pending(pending)
        state.record({"event": "PostToolUse", "tool": event.get("tool_name"), "paths": paths, "verdict": "approved",
                      "run": environ.get("PATTON_GATE_RUN", ""), "approver": environ.get("PATTON_GATE_OPERATOR", "operator"),
                      "tool_use_id": tool_use_id})
    return 0


def _approved_commands(state_dir: str) -> set[str]:
    found = _read_json(os.path.join(state_dir, "approved_commands.json"), [])
    return set(map(str, found)) if isinstance(found, list) else set()


def _approve_command(state_dir: str, command: str) -> None:
    if not command:
        return
    with _locked(state_dir):
        _atomic_write(os.path.join(state_dir, "approved_commands.json"),
                      json.dumps(sorted(_approved_commands(state_dir) | {command})))


# ---- Codex --------------------------------------------------------------------------------------------------

CODEX_TOOLS = ("Bash", "apply_patch")
PATCH_OPERATIONS = ("*** Add File:", "*** Update File:", "*** Delete File:", "*** Move to:")
HEREDOC = re.compile(r"\A\s*(?:cd\s+(?P<dir>[^\s;&|<>]+)\s*&&\s*)?apply_patch\s*<<-?\s*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)"
                     r"[ \t]*\n(?P<body>.*?)\n(?P=tag)\s*\Z", re.S)
APPROVE = re.compile(r"\bapprove\s+([0-9a-f]{6})\b", re.I)


def _codex_deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def patch_paths(patch: str) -> list[str] | None:
    """Every path a patch adds, updates, deletes or moves to, in order; None when it is not a readable patch."""
    lines = [line.strip() for line in patch.strip().splitlines()]
    if not lines or lines[0] != "*** Begin Patch":
        return None
    paths: list[str] = []
    for line in lines:
        for op in PATCH_OPERATIONS:
            if line.startswith(op):
                path = line[len(op):].strip()
                if path and path not in paths:
                    paths.append(path)
    return paths or None


def heredoc_patch(command: str) -> tuple[str, str] | None:
    """(patch, directory) for a shell command that runs apply_patch on a heredoc, as Codex intercepts it."""
    match = HEREDOC.match(command)
    if not match:
        return None
    directory = match.group("dir") or ""
    if directory:
        parts = shlex.split(directory)
        if len(parts) != 1:
            return None
        directory = parts[0]
    return match.group("body"), directory


def _patch_command(paths: list[str], directory: str = "") -> str:
    import posixpath
    joined = [p if posixpath.isabs(p) or not directory else posixpath.normpath(posixpath.join(directory, p))
              for p in paths]
    return "rm -f -- " + " ".join(shlex.quote(p) for p in joined)


def _holds_file(state_dir: str) -> str:
    return os.path.join(state_dir, "holds.json")


def _codex_hold(event: dict, environ, reason: str) -> dict:
    """Codex cannot ask the person from a hook. Keep what the call needs under a short id, and tell the model how
    the person approves it."""
    state_dir = environ["PATTON_GATE_STATE"]
    tool_use_id = str(event.get("tool_use_id", ""))
    with _locked(state_dir):
        pending = _read_json(os.path.join(state_dir, "pending.json"), {})
        need = pending.pop(tool_use_id, None)
        _atomic_write(os.path.join(state_dir, "pending.json"), json.dumps(pending))
        holds = _read_json(_holds_file(state_dir), {})
        held = hashlib.sha256(f"{tool_use_id}\0{time.time()}\0{len(holds)}".encode()).hexdigest()[:6]
        holds[held] = {"need": need, "tool": event.get("tool_name"), "t": round(time.time(), 3),
                       "command": str((event.get("tool_input") or {}).get("command", ""))[:500]}
        _atomic_write(_holds_file(state_dir), json.dumps(holds))
    return _codex_deny(f"{reason} Codex cannot ask the person from a hook, so the gate holds this call as {held}. "
                       f"Tell the person what it changes and ask them to type: approve {held}. Then make the same "
                       "call again.")


def _codex_approve(event: dict, environ) -> dict | None:
    """`approve <id>` in a prompt the person typed: grant what that held call needed."""
    prompt = event.get("prompt")
    ids = APPROVE.findall(prompt) if isinstance(prompt, str) else []
    state_dir, root = environ.get("PATTON_GATE_STATE"), environ.get("PATTON_GATE_ROOT")
    if not ids or not state_dir or not root:
        return None
    state = GateState.load(state_dir, root)
    notes = []
    for held in dict.fromkeys(i.lower() for i in ids):
        with _locked(state_dir):
            holds = _read_json(_holds_file(state_dir), {})
            entry = holds.pop(held, None)
            _atomic_write(_holds_file(state_dir), json.dumps(holds))
        if entry is None:
            notes.append(f"no held call has the id {held}")
            continue
        need = entry.get("need")
        if isinstance(need, dict):  # a command the gate cannot read: that exact command may run
            _approve_command(state_dir, str(need.get("command", "")))
            what = f"the command {need.get('command', '')[:120]!r}"
        else:
            state.grant(need or [])
            what = ", ".join(need or []) or "the call"
        state.record({"event": "UserPromptSubmit", "tool": entry.get("tool"), "paths": need if isinstance(need, list)
                      else [], "verdict": "approved", "approver": "person", "held": held,
                      "command": entry.get("command", "")})
        notes.append(f"the person approved {held} ({what}); make that call again")
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                   "additionalContext": "Patton gate: " + "; ".join(notes) + "."}}


def _codex(event: dict, environ) -> dict | None:
    name = event.get("hook_event_name")
    if name == "SessionStart":
        _run(event, io.StringIO(), environ)
        return None
    if name == "UserPromptSubmit":  # codex_main turns a crash here into no answer, never a blocked prompt
        return _codex_approve(event, environ)
    if name != "PreToolUse":
        return None
    tool, tool_input = event.get("tool_name"), event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if tool not in CODEX_TOOLS or not isinstance(command, str):
        return _codex_deny(f"Patton gate: the tool {tool!r} is not one the gate can check.")
    cwd = event.get("cwd")
    if not (isinstance(cwd, str) and os.path.isabs(cwd)):
        return _codex_deny("Patton gate: the hook input names no absolute working directory, so the gate cannot "
                           "place this call.")
    found = (command, "") if tool == "apply_patch" else heredoc_patch(command)
    # Codex has no ask, so the gate always holds instead of asking; bypassPermissions changes nothing here.
    gate_event = {**event, "permission_mode": "default"}
    if found is not None:
        paths = patch_paths(found[0])
        if paths is None:
            return _codex_deny("Patton gate: this patch cannot be checked: it has no '*** Begin Patch' line or names "
                               "no file. Rewrite it as a patch that names each file it adds, updates, deletes or moves.")
        gate_event = {**gate_event, "tool_name": "Bash", "tool_input": {"command": _patch_command(paths, found[1])}}
    out = io.StringIO()
    _run(gate_event, out, environ)
    text = out.getvalue()
    answer = (json.loads(text).get("hookSpecificOutput") or {}) if text.strip() else {}
    verdict, reason = answer.get("permissionDecision"), str(answer.get("permissionDecisionReason") or "")
    if verdict == "deny":
        return _codex_deny(reason or "Patton gate: refused.")
    if verdict == "ask":
        return _codex_hold(gate_event, environ, reason)
    if verdict not in (None, "allow"):
        return _codex_deny(f"Patton gate: the gate gave no usable decision ({verdict!r}), so the call is refused.")
    if tool == "apply_patch":
        return None  # Codex has no plain allow; no answer lets the patch run as written
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": {"command": f"cd {shlex.quote(cwd)} && {command}"}}}


def codex_trust(stdout, environ) -> int:
    """Mark this plugin's Codex hooks trusted, as Codex's own /hooks review does, then check that Codex agrees.

    Codex skips a plugin hook until the person trusts it, and drops the trust when the hook changes, so this runs
    after every install and upgrade. It talks to `codex app-server` and changes nothing but `hooks.state`."""
    import select
    import shutil
    codex = shutil.which("codex")
    if codex is None:
        stdout.write("codex is not on PATH.\n")
        return 1
    proc = subprocess.Popen([codex, "app-server", "--listen", "stdio://"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=dict(environ))
    try:
        def call(number: int, method: str, params: dict) -> dict:
            proc.stdin.write(json.dumps({"id": number, "method": method, "params": params}) + "\n")
            proc.stdin.flush()
            while True:
                ready, _, _ = select.select([proc.stdout], [], [], 30)
                line = proc.stdout.readline() if ready else ""
                if not line:
                    raise RuntimeError(f"codex app-server gave no answer to {method}")
                reply = json.loads(line)
                if reply.get("id") == number:
                    if "error" in reply:
                        raise RuntimeError(f"{method}: {reply['error']}")
                    return reply.get("result") or {}

        def ours(result: dict) -> list[dict]:
            return [h for group in result.get("data") or [] for h in group.get("hooks") or []
                    if str(h.get("key", "")).startswith("patton-gate@")]

        call(1, "initialize", {"clientInfo": {"name": "patton-gate", "version": VERSION}})
        proc.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        proc.stdin.flush()
        hooks = ours(call(2, "hooks/list", {"cwds": [os.getcwd()]}))
        if not hooks:
            stdout.write("Codex lists no patton-gate hooks. Install the plugin first:\n  codex plugin marketplace add "
                         "vladimirrott/patton-protocol && codex plugin add patton-gate@patton-protocol\n")
            return 1
        value = {h["key"]: {"trusted_hash": h["currentHash"]} for h in hooks}
        call(3, "config/batchWrite", {"edits": [{"keyPath": "hooks.state", "mergeStrategy": "upsert", "value": value}],
                                      "reloadUserConfig": True})
        after = ours(call(4, "hooks/list", {"cwds": [os.getcwd()]}))
    except (OSError, ValueError, RuntimeError) as err:
        stdout.write(f"Could not trust the hooks: {err}\n")
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    for h in after:
        stdout.write(f"  {h.get('trustStatus', '?'):10} {h['key']}\n")
    if len(after) != len(hooks) or any(h.get("trustStatus") != "trusted" for h in after):
        stdout.write("Codex does not report every patton-gate hook as trusted.\n")
        return 1
    stdout.write(f"patton-gate {VERSION}: {len(after)} Codex hooks trusted. They take effect in the next session.\n")
    return 0


def codex_main(stdin, stdout, environ) -> int:
    """The Codex hook entry point. Codex lets a call run when its hook fails, so every PreToolUse ends in an answer."""
    event = None
    try:
        event = json.loads(stdin.read())
        if not isinstance(event, dict):
            raise ValueError("the hook input is not a JSON object")
        answer = _codex(event, _configured(event, {**environ, "PATTON_GATE_HOST": "codex"}))
    except Exception as err:  # noqa: BLE001 - fail closed on anything
        if isinstance(event, dict) and event.get("hook_event_name") not in (None, "PreToolUse"):
            return 0
        answer = _codex_deny(f"Patton gate: the gate failed ({type(err).__name__}: {str(err)[:200]}), so it refuses "
                             "this call rather than let it run unchecked. Remove the patton-gate plugin to continue "
                             "without it, and report the error.")
    if answer is not None:
        stdout.write(json.dumps(answer))
    return 0


USAGE = f"""patton-gate {VERSION}: a Claude Code hook that asks before a file that existed at session start changes.

Claude Code runs this file as a hook; it reads one hook event as JSON on stdin. From a shell:

  python3 patton_gate.py log [SESSION] [--codex]
                                             what the gate did in the latest session (or in SESSION)
  python3 patton_gate.py config [--codex]    the settings a new session in this directory gets, and where each
                                             one comes from
  python3 patton_gate.py config --check FILE check one settings file without starting a session
  python3 patton_gate.py restore [--all | PATH...] [--session ID] [--codex]
                                             list, or put back, the files the gate kept before a change it let
                                             through
  python3 patton_gate.py codex-trust         trust this plugin's hooks in Codex (after install or upgrade)
  python3 patton_gate.py --version
"""


def _sessions_dir(environ, codex: bool = False) -> str:
    """Where the sessions live: the plugin's data directory, found on disk when the hook environment is absent."""
    home = environ.get("HOME") or os.path.expanduser("~")
    base = environ.get("CLAUDE_PLUGIN_DATA") or environ.get("PLUGIN_DATA")
    if not base:
        host_dir = (os.path.join(environ.get("CODEX_HOME") or os.path.join(home, ".codex"), "plugins", "data")
                    if codex else os.path.join(home, ".claude", "plugins", "data"))
        installed = _glob.glob(os.path.join(host_dir, "patton-gate*"))
        base = max(installed, key=os.path.getmtime) if installed else os.path.join(
            environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state"), "patton-gate")
    return os.path.join(base, "sessions")


def log(stdout, environ, session: str | None = None, codex: bool = False) -> int:
    """Print what the gate did in one session (the latest by default): one line per ledger entry."""
    sessions = _sessions_dir(environ, codex)
    try:
        names = [n for n in os.listdir(sessions) if os.path.isfile(os.path.join(sessions, n, "ledger.jsonl"))]
    except OSError:
        names = []
    if session is None and names:
        session = max(names, key=lambda n: os.path.getmtime(os.path.join(sessions, n, "ledger.jsonl")))
    if not session or session not in names:
        stdout.write(f"No gate activity recorded under {sessions}. The gate records a session from its first file "
                     "change or command: start a Claude Code session with the plugin enabled, then run this again.\n")
        return 0
    stdout.write(f"Patton gate {VERSION}, session {session}\n")
    with open(os.path.join(sessions, session, "ledger.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            when = time.strftime("%H:%M:%S", time.localtime(e.get("t", 0)))
            what = e.get("command") or ", ".join(e.get("paths") or []) or e.get("tool") or ""
            stdout.write(f"{when}  {e.get('verdict', '?'):9} {e.get('tool', '')}: {what[:120]}\n")
    return 0


def restore_command(args: list[str], stdout, environ) -> int:
    """List the files the gate kept in a session, or put them back: `restore`, `restore --all`, `restore PATH...`.
    `--session ID` picks a session (the latest by default); `--codex` looks in Codex's plugin data."""
    import shutil
    args = list(args)
    codex = "--codex" in args
    args = [a for a in args if a != "--codex"]
    session = None
    if "--session" in args:
        i = args.index("--session")
        session = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    sessions = _sessions_dir(environ, codex)
    try:
        names = [n for n in os.listdir(sessions) if os.path.isfile(os.path.join(sessions, n, "originals.json"))]
    except OSError:
        names = []
    if session is None and names:
        session = max(names, key=lambda n: os.path.getmtime(os.path.join(sessions, n, "originals.json")))
    if not session or session not in names:
        stdout.write(f"No kept files under {sessions}"
                     + (f", so nothing to put back for {' '.join(args)}" if args else "") + ".\n")
        return 1 if args else 0
    state_dir = os.path.join(sessions, session)
    index = _read_json(os.path.join(state_dir, "originals.json"), {"kept": {}, "skipped": {}})
    manifest = _read_json(os.path.join(state_dir, "manifest.json"), {})
    root = manifest.get("root") if isinstance(manifest, dict) else None
    if not isinstance(root, str) or not os.path.isdir(root):
        stdout.write(f"The project of session {session} is gone or unreadable.\n")
        return 1
    kept = index.get("kept") or {}
    if not args:
        stdout.write(f"Session {session}, project {root}: {len(kept)} kept file(s)\n")
        for rel in sorted(kept):
            now = os.path.join(root, rel)
            same = os.path.lexists(now) and not kept[rel].get("link") and os.path.isfile(now) and _hash(now) == _hash(
                os.path.join(state_dir, "originals", rel))
            status = "missing" if not os.path.lexists(now) else ("unchanged" if same else "changed")
            stdout.write(f"  {status:9} {rel}\n")
        for rel, why in sorted((index.get("skipped") or {}).items()):
            stdout.write(f"  not kept  {rel}: {why}\n")
        stdout.write("Put files back with `restore --all` or `restore PATH...`.\n")
        return 0
    wanted = sorted(kept) if args == ["--all"] else args
    failed = 0
    root_real = os.path.realpath(root)
    for rel in wanted:
        source = os.path.join(state_dir, "originals", rel)
        target = os.path.realpath(os.path.join(root_real, rel))
        if rel not in kept or not os.path.lexists(source):
            stdout.write(f"  not kept: {rel}\n")
            failed += 1
            continue
        if not target.startswith(root_real + os.sep):
            stdout.write(f"  refused: {rel} is outside the project\n")
            failed += 1
            continue
        try:
            if os.path.lexists(target):
                os.replace(target, target + ".before-restore")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if kept[rel].get("link"):
                os.symlink(os.readlink(source), target)
            else:
                shutil.copy2(source, target)
        except OSError as err:
            stdout.write(f"  failed: {rel} ({type(err).__name__})\n")
            failed += 1
            continue
        stdout.write(f"  restored {rel}\n")
    return 1 if failed else 0


def config_command(args: list[str], stdout, environ) -> int:
    """Print the effective settings for the current directory, or check one file."""
    if args[:1] == ["--check"]:
        if len(args) != 2:
            stdout.write("usage: patton_gate.py config --check FILE\n")
            return 2
        try:
            data = _read_config_file(args[1])
            if data is None:
                stdout.write(f"{args[1]}: no such file\n")
                return 1
            _validate_layer(args[1], data, True)
            for project, body in (data.get("projects") or {}).items():
                if not isinstance(body, dict):
                    raise ConfigError(args[1], f"projects[{project}] must be an object")
                _validate_layer(f"{args[1]} projects[{project}]", body, False)
        except ConfigError as err:
            stdout.write(f"invalid: {err}\n")
            return 1
        stdout.write(f"valid: {args[1]}\n")
        return 0
    host = "codex" if "--codex" in args else "claude"
    root = environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    user, project = config_paths(root, environ, host)
    stdout.write(f"patton-gate {VERSION} settings for {root}\n  user file:    {user}"
                 f"{'' if os.path.exists(user) else ' (none)'}\n  project file: {project}"
                 f"{'' if os.path.exists(project) else ' (none)'}\n")
    try:
        config, sources, ignored = load_config(root, environ, host)
    except ConfigError as err:
        stdout.write(f"invalid: {err}\nA session started now refuses every call until this is fixed.\n")
        return 1
    for section in DEFAULTS:
        for field in DEFAULTS[section]:
            key = f"{section}.{field}"
            stdout.write(f"  {key:28} {json.dumps(config[section][field]):40} {sources[key]}\n")
    for item in ignored:
        stdout.write(f"  ignored: {item['field']} = {json.dumps(item['value'])} in the {item['file']}: {item['why']}\n")
    stdout.write("A running session keeps the settings it started with; these apply from the next session.\n")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--host" in args and args[args.index("--host") + 1:args.index("--host") + 2] == ["codex"]:
        sys.exit(codex_main(sys.stdin, sys.stdout, os.environ))
    if "codex-trust" in args:
        sys.exit(codex_trust(sys.stdout, os.environ))
    if "config" in args:
        sys.exit(config_command(args[args.index("config") + 1:], sys.stdout, os.environ))
    if "restore" in args:
        sys.exit(restore_command(args[args.index("restore") + 1:], sys.stdout, os.environ))
    if "verify" in args:
        sys.exit(verify_command(args[args.index("verify") + 1:], sys.stdout, os.environ))
    if "log" in args:  # a glob over two installed versions puts a second path before "log"
        rest = [a for a in args[args.index("log") + 1:] if a != "--codex"]
        sys.exit(log(sys.stdout, os.environ, rest[0] if rest else None, codex="--codex" in args))
    if "--version" in args:
        print(f"patton-gate {VERSION}")
        sys.exit(0)
    if args or sys.stdin.isatty():
        sys.stdout.write(USAGE)
        sys.exit(2)
    sys.exit(main(sys.stdin, sys.stdout, os.environ))
