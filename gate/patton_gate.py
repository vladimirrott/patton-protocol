#!/usr/bin/env python3
"""Patton gate: a Claude Code hook that asks before an agent changes a file that existed at session start.

Standard library only, one file, so a user copies one file. It holds the Bash classifier, the decision rules,
the ledger, the session manifest and the hook entry point. Given a command string the classifier says whether
the command only reads, changes some files, or cannot be read. A command
the classifier cannot read is `opaque`, and the gate refuses it. The classifier never executes anything.
It is a reader for an honest agent's mistakes, not a sandbox against an adversary.
"""

from __future__ import annotations

import contextlib
import glob as _glob
import hashlib
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
                        for f in self._files_under(real):
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
            return Classification(MUTATES, paths)
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


@dataclass
class Decision:
    verdict: str  # allow, deny, approve
    paths: tuple[str, ...] = ()
    reason: str = ""
    note: str = ""  # "stalled" when the denial limit ended the mission


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


def write_manifest(root: str, state_dir: str) -> None:
    """Record every file under `root` with its sha256. A path in the manifest is protected."""
    root_real = os.path.realpath(root)
    state_real = os.path.realpath(state_dir)
    files: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root_real, followlinks=False):
        dirnames[:] = [d for d in dirnames if os.path.realpath(os.path.join(dirpath, d)) != state_real]
        names = filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
        for name in names:
            full = os.path.join(dirpath, name)
            files[os.path.relpath(full, root_real).replace(os.sep, "/")] = _hash(full)
    os.makedirs(state_dir, exist_ok=True)
    _atomic_write(os.path.join(state_dir, "manifest.json"), json.dumps({"root": root_real, "files": files}))


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
        return cls(root_real, state_dir, frozenset(manifest["files"]), set(map(str, granted)), denials)

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
            # No stop sentence here: a rewrite comes back through the gate, so the protected files stay guarded.
            decision = Decision("deny", reason=(f"Patton gate: this command cannot be checked ({found.why}). Rewrite it "
                                                "as a plain command that names each file, such as ls, cat, rm, mv or an "
                                                "explicit redirect."))
        else:
            decision = _needs(tuple(p for p in found.paths if p in state.protected and p not in state.granted))
    else:
        raw = tool_input.get(EDIT_TOOLS[tool])
        if not isinstance(raw, str) or not raw:
            return Decision("deny", reason=f"Patton gate: the {tool} call names no file. " + STOP)
        real = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(cwd, raw))
        rel = os.path.relpath(real, state.root).replace(os.sep, "/")
        inside = not (rel == ".." or rel.startswith("../"))
        decision = _needs((rel,) if inside and rel in state.protected and rel not in state.granted else ())
    if state.denials >= DENIAL_LIMIT:
        return Decision("deny", reason=("Patton gate: this mission has been refused too many times. Report the mission "
                                        "blocked and say which files you needed. " + STOP), note="stalled")
    return decision


def _needs(paths: tuple[str, ...]) -> Decision:
    if not paths:
        return Decision("allow")
    return Decision("approve", paths, "Patton gate: this changes " + ", ".join(paths) + ", which existed when the session started.")


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


def main(stdin, stdout, environ) -> int:
    """The hook entry point. Always exits 0; the decision is in the JSON on stdout."""
    try:
        event = json.loads(stdin.read())
    except (ValueError, OSError):
        event = None
    name = event.get("hook_event_name") if isinstance(event, dict) else None
    state_dir, root = environ.get("PATTON_GATE_STATE"), environ.get("PATTON_GATE_ROOT")
    if name == "SessionStart":
        if state_dir and root:
            try:
                write_manifest(root, state_dir)
            except OSError:
                pass
        return 0
    if name == "PostToolUse":
        return _after(event, state_dir, root, environ)
    if name != "PreToolUse":
        return _emit(stdout, "deny", "Patton gate: the hook input could not be read. " + STOP)
    if not state_dir or not root:
        return _emit(stdout, "deny", "Patton gate: the gate is not configured (no state directory or work directory). " + STOP)
    try:
        state = GateState.load(state_dir, root)
    except ManifestError as err:
        return _emit(stdout, "deny", f"Patton gate: {err}. " + STOP)
    decision = decide(event, state)
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    entry = {"event": "PreToolUse", "tool": event.get("tool_name"), "paths": list(decision.paths),
             "run": environ.get("PATTON_GATE_RUN", ""), "approver": environ.get("PATTON_GATE_OPERATOR", "operator"),
             "command": str(tool_input.get("command", ""))[:500], "tool_use_id": event.get("tool_use_id", "")}
    if decision.verdict == "allow":
        state.record({**entry, "verdict": "allow"})
        return _emit(stdout, "allow", "Patton gate: nothing protected is changed.")
    if decision.verdict == "deny":
        state.record({**entry, "verdict": "stalled" if decision.note == "stalled" else "deny", "reason": decision.reason[:300]})
        if decision.note != "stalled":
            state.count_denial()
        return _emit(stdout, "deny", decision.reason)
    spec = environ.get("PATTON_GATE_APPROVER") or None
    try:
        timeout = float(environ.get("PATTON_GATE_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    answer = approve(decision, event, spec, timeout, environ.get("PATTON_GATE_RUN", ""))
    if answer.get("ask"):
        state.set_pending({**state.pending(), str(event.get("tool_use_id", "")): list(decision.paths)})
        state.record({**entry, "verdict": "asked"})
        return _emit(stdout, "ask", decision.reason)
    state.grant(answer["approved"])
    answered = {**entry, "granted": answer["approved"], "denied": answer["denied"]}
    if not answer["denied"]:
        state.record({**answered, "verdict": "approved"})
        return _emit(stdout, "allow", "Patton gate: the operator approved " + ", ".join(decision.paths) + ".")
    state.count_denial()
    reason = ("Patton gate: the operator refused " + ", ".join(answer["denied"]) + ". " + STOP)
    state.record({**answered, "verdict": "refused", "reason": reason[:300]})
    return _emit(stdout, "deny", reason)


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
        state.grant(paths)
        state.set_pending(pending)
        state.record({"event": "PostToolUse", "tool": event.get("tool_name"), "paths": paths, "verdict": "approved",
                      "run": environ.get("PATTON_GATE_RUN", ""), "approver": environ.get("PATTON_GATE_OPERATOR", "operator"),
                      "tool_use_id": tool_use_id})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin, sys.stdout, os.environ))
